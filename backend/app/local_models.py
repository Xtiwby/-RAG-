"""Offline, revision-pinned BGE inference; no remote code or request-time downloads."""
import asyncio
import json
import math
import threading
from pathlib import Path
from fastapi import HTTPException
from . import telemetry

MODEL_ROOT=Path(__file__).resolve().parents[1]/'models'
SUPPORTED={'BAAI/bge-m3':'bge-m3','BAAI/bge-reranker-v2-m3':'bge-reranker-v2-m3'}
_lock=threading.Lock()
_loaded={}
_state={}


def identity(model):
    if model not in SUPPORTED:
        raise ValueError('不支持的本地模型')
    path=MODEL_ROOT/SUPPORTED[model]
    try:
        manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
        revision=manifest['revision']
        if manifest['model']!=model or len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):
            raise ValueError()
        required=('config.json','tokenizer_config.json','tokenizer.json','model.safetensors')
        if not all((path/name).is_file() for name in required):
            raise ValueError()
    except (OSError,ValueError,KeyError,TypeError):
        raise RuntimeError('本地模型文件未准备完整，请运行 backend/prepare_models.py；未退回词项算法') from None
    return path,revision


def namespace(config):
    _,revision=identity(config.model)
    return f'local-cls-normalized-v1|{config.model}|{revision}|length={config.max_length}'


def status():
    result={}
    for model in SUPPORTED:
        try:
            _,revision=identity(model)
            result[model]={'downloaded':True,'revision':revision,**_state.get(model,{'loaded':False})}
        except RuntimeError:
            result[model]={'downloaded':False,'loaded':False}
    return result


def _load(model,device):
    import torch
    from transformers import AutoTokenizer,AutoModel,AutoModelForSequenceClassification
    path,revision=identity(model)
    actual='cuda' if device=='auto' and torch.cuda.is_available() else 'cpu' if device=='auto' else device
    if actual=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('已要求 CUDA，但当前 PyTorch 无法使用显卡；请检查驱动或安装 CUDA 版 PyTorch')
    key=(model,revision,actual)
    if key not in _loaded:
        # At most one device instance per model; the lock serializes GPU use.
        for old in list(_loaded):
            if old[0]==model:
                del _loaded[old]
        if actual=='cpu':
            torch.set_num_threads(min(8,torch.get_num_threads()))
        tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
        loader=AutoModel if model=='BAAI/bge-m3' else AutoModelForSequenceClassification
        network=loader.from_pretrained(path,local_files_only=True,trust_remote_code=False,
            use_safetensors=True,torch_dtype=torch.float16 if actual=='cuda' else torch.float32)
        network.to(actual).eval()
        _loaded[key]=(tokenizer,network)
        _state[model]={'loaded':True,'device':actual,'dtype':'float16' if actual=='cuda' else 'float32'}
    return *_loaded[key],actual


def validate_vectors(values,count):
    if len(values)!=count or not values:
        raise ValueError('向量数量不匹配')
    dimension=len(values[0])
    if not dimension or any(len(v)!=dimension or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in v) or sum(x*x for x in v)<=0 for v in values):
        raise ValueError('无效语义向量')
    return values


def _infer(config,texts,pairs=False):
    import torch
    with _lock,torch.inference_mode():
        tokenizer,network,device=_load(config.model,config.device)
        values=[]
        truncated=0
        for start in range(0,len(texts),config.batch_size):
            batch=texts[start:start+config.batch_size]
            # Surface truncation instead of suggesting the entire passage was scored.
            encoded=tokenizer(batch,padding=True,truncation=True,max_length=config.max_length,return_tensors='pt')
            truncated+=int((encoded['attention_mask'].sum(dim=1)>=config.max_length).sum().item())
            encoded={k:v.to(device) for k,v in encoded.items()}
            output=network(**encoded)
            if pairs:
                scores=output.logits.float().reshape(-1).sigmoid().cpu().tolist()
                if len(scores)!=len(batch) or any(not math.isfinite(x) for x in scores):
                    raise ValueError('无效重排得分')
                values.extend(scores)
            else:
                vector=torch.nn.functional.normalize(output.last_hidden_state[:,0].float(),p=2,dim=1)
                values.extend(vector.cpu().tolist())
        if not pairs:
            validate_vectors(values,len(texts))
        return values,{'device':device,'revision':identity(config.model)[1],
            'max_length':config.max_length,'at_length_limit':truncated}


async def _run(config,texts,pairs=False):
    call=telemetry.begin('reranking' if pairs else 'embedding',config.model)
    try:
        result=await asyncio.wait_for(asyncio.to_thread(_infer,config,texts,pairs),config.timeout_seconds)
        telemetry.finish(call,'success',characters=sum(len(str(t)) for t in texts))
        return result
    except asyncio.CancelledError:
        telemetry.finish(call,'interrupted','本地推理请求已中止')
        raise
    except Exception as error:
        message=str(error) if isinstance(error,RuntimeError) and ('请' in str(error) or '未退回' in str(error)) else '本地模型推理失败或超时，请检查运行状态、模型文件及显存；未退回词项算法'
        telemetry.finish(call,'failure',message)
        raise HTTPException(503,message) from None


async def embed(config,texts):
    values,_=await _run(config,texts)
    return values


async def rerank(config,query,texts):
    return await _run(config,[(query,text) for text in texts],True)

r"""Download pinned official snapshots through hf CLI, then publish manifests.

Usage: .venv\Scripts\python.exe prepare_models.py
No knowledge-base contents or credentials are uploaded.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
REPOS=('BAAI/bge-m3','BAAI/bge-reranker-v2-m3')
PINS={
    'BAAI/bge-m3':('5617a9f61b028005a4858fdac845db406aefb181','pytorch_model.bin','b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38'),
    'BAAI/bge-reranker-v2-m3':('953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e','model.safetensors','d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286')}
FILES=('config.json','tokenizer_config.json','tokenizer.json','special_tokens_map.json',
       'sentencepiece.bpe.model')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',choices=REPOS)
    parser.add_argument('--source',choices=['huggingface','modelscope'],default='huggingface',help='ModelScope fallback uses identical pinned weight hashes')
    args=parser.parse_args()
    from huggingface_hub import HfApi
    api=HfApi()
    cli=Path(sys.executable).with_name('hf.exe' if os.name=='nt' else 'hf')
    for repo in ([args.model] if args.model else REPOS):
        revision,weights,checksum=PINS[repo]
        info=api.model_info(repo,revision=revision,files_metadata=True)
        if info.sha!=revision:
            raise SystemExit('仓库提交校验失败')
        target=ROOT/'models'/repo.split('/')[-1]
        manifest=target/'manifest.json'
        if manifest.exists():
            current=json.loads(manifest.read_text(encoding='utf-8'))
            if current.get('revision')!=revision:
                raise SystemExit('已有模型版本不同；请先显式规划模型升级，不覆盖活动模型')
        print(f'Downloading {repo} @ {revision} into {target}',flush=True)
        if args.source=='modelscope':
            from download_verified import download
            download(f'https://modelscope.cn/models/{repo}/resolve/master/{weights}',target/weights,checksum)
        subprocess.run([str(cli),'download',repo,*FILES,*([weights] if args.source=='huggingface' else []),'--revision',revision,'--local-dir',str(target),'--max-workers','2'],check=True)
        for entry in info.siblings:
            if entry.rfilename not in (*FILES,weights):continue
            downloaded=target/entry.rfilename
            with downloaded.open('rb') as handle:
                if entry.lfs:
                    digest=hashlib.file_digest(handle,'sha256').hexdigest()
                    expected=entry.lfs.sha256
                else:
                    data=handle.read()
                    digest=hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
                    expected=entry.blob_id
            if digest!=expected or entry.rfilename==weights and digest!=checksum:
                raise SystemExit('下载文件哈希校验失败：'+entry.rfilename)
        if weights.endswith('.bin') and (not manifest.exists() or not (target/'model.safetensors').exists()):
            import torch
            from safetensors.torch import save_file
            print('Converting verified weights to safetensors (weights_only=True)',flush=True)
            tensors=torch.load(target/weights,map_location='cpu',weights_only=True)
            staged=target/'model.safetensors.converting'
            save_file({k:v.contiguous() for k,v in tensors.items()},str(staged),metadata={'format':'pt'})
            staged.replace(target/'model.safetensors')
            del tensors
        required=('config.json','tokenizer_config.json','tokenizer.json','model.safetensors')
        if not all((target/name).is_file() for name in required):
            raise SystemExit('模型下载不完整，未发布可用标记')
        manifest.write_text(json.dumps({'model':repo,'revision':revision,'format':'safetensors',
            'source':'https://huggingface.co/'+repo,'weights_download_source':args.source,'download_endpoint':os.getenv('HF_ENDPOINT','https://huggingface.co'),
            'source_weights_sha256':checksum},indent=2),encoding='utf-8')
        print(f'Ready: {repo}',flush=True)

if __name__=='__main__':
    main()

"""Real local-model/OCR/Kimi acceptance on disposable data; never edits live data.

Run --local for OCR and semantic tests, --kimi for a bounded six-question live
model run. Only synthetic public test text is sent to the configured provider.
"""
import argparse
import asyncio
import io
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-results/remaining-2026-11-19'


def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


async def local():
    from app import local_models,settings,ocr,pdf_ocr,rag
    from PIL import Image,ImageDraw,ImageFont
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    import torch
    config=settings.defaults()
    config.embedding=settings.EmbeddingSettings(provider='local',model='BAAI/bge-m3',timeout_seconds=300)
    config.reranking=settings.RerankingSettings(provider='local',timeout_seconds=300)
    config.ocr.provider='local'
    token=settings.active.set(config)
    try:
        question='空气中的水分如何去除？'
        texts=['The dehumidifier removes excess moisture from ambient air using a condensation coil.',
               'The network router forwards packets between subnets using its routing table.',
               'The warehouse prints shipping labels on cardboard cartons.']
        t=time.perf_counter();vectors=await local_models.embed(config.embedding,[question,*texts]);cold=time.perf_counter()-t
        similarities=[sum(a*b for a,b in zip(vectors[0],v)) for v in vectors[1:]]
        scores,meta=await local_models.rerank(config.reranking,question,texts)
        timings=[]
        for _ in range(8):
            t=time.perf_counter()
            await local_models.embed(config.embedding,[question]);await local_models.rerank(config.reranking,question,texts)
            timings.append((time.perf_counter()-t)*1000)
        result={'torch':torch.__version__,'gpu':torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU',
                'embedding_dimensions':len(vectors[0]),'cosine':similarities,'rerank_scores':scores,
                'cold_embedding_seconds':cold,'warm_embed_and_rerank_ms':timings,'warm_median_ms':statistics.median(timings),
                'models':local_models.status(),'pass':len(vectors[0])==1024 and similarities[0]==max(similarities) and scores[0]==max(scores)}
        save('local-models.json',result);assert result['pass'];print('Real BGE embedding/reranker PASS',flush=True)
        with tempfile.TemporaryDirectory(prefix='rag-live-ocr-') as folder:
            image=Image.new('RGB',(1400,450),'white');draw=ImageDraw.Draw(image)
            font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',48)
            draw.text((50,55),'知源扫描件验收：控制器采用被动散热。',font=font,fill='black')
            draw.text((50,155),'设备编号 NX90217，保留期限为21天。',font=font,fill='black')
            path=Path(folder)/'scan.pdf';doc=canvas.Canvas(str(path),pagesize=(700,225));doc.drawImage(ImageReader(image),0,0,700,225);doc.save()
            units=rag.parse_local(path,'pdf');assert not units[0]['text'].strip()
            t=time.perf_counter();recognized=await pdf_ocr.extract(path,units,lambda:None)
            result={'units':recognized,'latency_seconds':time.perf_counter()-t,
                    'pass':all(term in ''.join(x['text'] for x in recognized) for term in ['被动散热','NX90217','21'])}
            save('real-scanned-pdf.json',result);assert result['pass'];print('Real scanned PDF OCR PASS',flush=True)
    finally:
        settings.active.reset(token)


def kimi():
    from fastapi.testclient import TestClient
    from app.main import app
    from app import store
    from dotenv import dotenv_values
    cfg=dotenv_values(Path(__file__).resolve().parent/'.env')
    for name in ['KIMI_API_KEY','KIMI_BASE_URL','KIMI_MODEL']:
        if cfg.get(name):os.environ[name]=cfg[name]
    if not os.environ.get('KIMI_API_KEY'):
        raise RuntimeError('未配置 Kimi 密钥')
    cases=[('NX90217的保留期限是多少天？',['21']),('What cooling method does NX90217 use?',['passive','被动']),
           ('NX90217的最低工作温度是多少？',['-11']),('NX90217开壳前需要做什么？',['断电']),
           ('NX90217的期限、温度和维护步骤分别是什么？',['21','-11']),('NX90217已经实测达到多少QPS？',[])]
    with TestClient(app) as client:
        response=client.post('/api/auth/login',json={'username':'admin','password':'admin123'});response.raise_for_status()
        headers={'Authorization':'Bearer '+response.json()['token']}
        kid=client.post('/api/knowledge-bases',headers=headers,json={'name':'真实模型合成验收'}).json()['id']
        raw='NX90217控制器采用被动散热，不使用电动风扇。保留期限为21天。最低工作温度为-11摄氏度。维护时必须先断电，然后才能打开机壳。未提供本项目的QPS实测结果。'
        uploaded=client.post('/api/documents',headers=headers,data={'knowledge_base_id':kid},files={'file':('NX90217.txt',raw.encode())}).json()
        for _ in range(100):
            current=client.get(f"/api/documents/{uploaded['id']}",headers=headers).json()
            if current['status']=='ready':break
            time.sleep(.1)
        assert current['status']=='ready'
        records=[]
        for question,terms in cases:
            t=time.perf_counter();resp=client.post('/api/qa/stream',headers=headers,json={'query':question,'knowledge_base_ids':[kid]})
            events=[json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith('data:')]
            content=''.join(x.get('content','') for x in events if x['type']=='token')
            passed=events[-1]['type']=='done' and (all(term in content.lower() for term in terms) if terms and 'cooling' not in question else
                any(term in content.lower() for term in terms) if terms else any(term in content for term in ['证据不足','未提供','没有','无法']))
            records.append({'question':question,'answer':content,'seconds':time.perf_counter()-t,'pass':passed,'events':events})
            save('kimi-real.json',{'results':records,'model_calls':store.rows('SELECT kind,model,status,latency_ms,input_tokens,output_tokens FROM model_calls')})
            print('Kimi',len(records),'PASS' if passed else 'FAIL',round(records[-1]['seconds'],2),flush=True)
        assert all(item['pass'] for item in records),'真实模型验收存在失败，见结果记录'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--local',action='store_true');parser.add_argument('--kimi',action='store_true');args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='rag-real-acceptance-') as folder:
        os.environ.update(PYTHON_DOTENV_DISABLED='1',RAG_DATA_DIR=folder,RAG_DATABASE_URL='',RAG_FILE_STORAGE='local',RAG_WORKER_MODE='embedded',
                          RAG_QA_VERIFICATION_MODE='citation-first',RAG_QA_HEDGE_DELAY_SECONDS='0',RAG_MIN_SIMILARITY='.3')
        if args.local:asyncio.run(local())
        if args.kimi:kimi()


if __name__=='__main__':main()

"""Opt-in real-server acceptance with synthetic evidence only; leaves clearly named test records.

Run: python check_acceptance_v04.py --base-url http://127.0.0.1:8000 --live
--live authorizes a small real generation + verification model call sequence.
"""
import argparse
import io
import json
import time
import zipfile
from pathlib import Path
import httpx

def main():
    args=argparse.ArgumentParser()
    args.add_argument('--base-url',default='http://127.0.0.1:8000')
    args.add_argument('--live',action='store_true')
    options=args.parse_args()
    def value(response):
        response.raise_for_status()
        return response.json()
    with httpx.Client(base_url=options.base_url,timeout=240) as client:
        auth=value(client.post('/api/auth/login',json={'username':'admin','password':'admin123'}))
        client.headers['Authorization']='Bearer '+auth['token']
        scope=[]
        for name in ('V0.4 功能验收（合成资料）','V0.4 导出范围（空库）'):
            items=value(client.get('/api/knowledge-bases'))
            kid=next((item['id'] for item in items if item['name']==name),None)
            if kid is None:
                kid=value(client.post('/api/knowledge-bases',json={'name':name,'description':'仅用于新版功能验收，不代表真实项目进展'}))['id']
            scope.append(kid)
        text='# 合成验收资料\n\n关键词检索采用 BM25 算法。混合检索将关键词检索与向量检索结果通过 RRF 融合。\n\n本资料仅用于功能验收，不包含医疗结论或项目实际指标。'
        docs=value(client.get('/api/documents',params={'knowledge_base_id':scope[0]}))
        doc=next((doc for doc in docs if doc['name']=='逐句校验合成资料.md' and doc['status']=='ready'),None)
        if doc is None:
            doc=value(client.post('/api/documents',data={'knowledge_base_id':scope[0]},files={'file':('逐句校验合成资料.md',text.encode())}))
        for _ in range(200):
            doc=value(client.get(f"/api/documents/{doc['id']}"))
            if doc['status'] not in ('uploading','processing'):
                break
            time.sleep(.1)
        assert doc['status']=='ready',doc['parse_error']
        pipeline=value(client.get(f"/api/documents/{doc['id']}/processing"))
        assert pipeline['progress']==100 and all(stage['status']=='completed' for stage in pipeline['stages'])
        result={'backend':value(client.get('/openapi.json'))['info']['version'],'document_id':doc['id'],
                'stages':[stage['status'] for stage in pipeline['stages']],'event_count':len(pipeline['events'])}
        if options.live:
            events=[]
            with client.stream('POST','/api/qa/stream',json={'query':'仅用一句话回答：本资料中关键词检索采用哪种算法？请在句末引用证据。','knowledge_base_ids':scope}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith('data:'):
                        events.append(json.loads(line[5:]))
            assert events[-1]['type']=='done',events[-1]
            cid=events[0]['conversation_id']
            conversation=value(client.get(f'/api/conversations/{cid}'))
            answer=conversation['messages'][-1]
            assert 'BM25' in answer['content'] and any(check['status']=='supported' for check in answer['verification']),answer['verification']
            exported=client.get(f'/api/conversations/{cid}/export?format=docx');exported.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
                xml=package.read('word/document.xml').decode()
                assert 'V0.4 功能验收' in xml and 'V0.4 导出范围' in xml
                assert all(message['created_at'] in xml for message in conversation['messages'])
            result.update(conversation_id=cid,answer=answer['content'],verified_sentences=sum(check['status']=='supported' for check in answer['verification']),
                          export_bytes=len(exported.content),scope_count=len(scope))
        client.post('/api/auth/logout')
        print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    main()

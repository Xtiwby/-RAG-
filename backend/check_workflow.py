"""Opt-in live check against the local server. Uses only the included synthetic example."""
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
import httpx

def require(response,status=200):
    if response.status_code!=status:
        raise RuntimeError(f'Unexpected HTTP {response.status_code}')
    return response.json()

def stream(client,path,body):
    output=[]
    with client.stream('POST',path,json=body) as response:
        if response.status_code!=200:
            raise RuntimeError(f'Stream HTTP {response.status_code}')
        for line in response.iter_lines():
            if line.startswith('data:'):
                value=json.loads(line[5:])
                if value['type']=='error':
                    raise RuntimeError(value['message'])
                output.append(value)
    if not output or output[-1]['type']!='done':
        raise RuntimeError('Stream did not finish')
    return output

def main():
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=150) as client:
        auth=require(client.post('/api/auth/login',json={'username':'admin','password':'admin123'}))
        client.headers['Authorization']='Bearer '+auth['token']
        kbs=require(client.get('/api/knowledge-bases'))
        kid=next((k['id'] for k in kbs if k['name']=='功能验收样例'),None)
        if not kid:
            kid=require(client.post('/api/knowledge-bases',json={'name':'功能验收样例','description':'仅包含项目自带合成资料，用于验证真实 Kimi RAG 流程'}))['id']
        path=Path(__file__).resolve().parents[1]/'examples'/'功能验收资料.md'
        content=path.read_bytes()
        doc=require(client.post('/api/documents',data={'knowledge_base_id':kid},files={'file':(path.name,content,'text/markdown')}),201)
        for _ in range(200):
            doc=require(client.get(f"/api/documents/{doc['id']}"))
            if doc['status'] not in ('uploading','processing'):
                break
            time.sleep(.1)
        assert doc['status']=='ready',doc['parse_error']
        assert doc['sha256']==hashlib.sha256(content).hexdigest()
        flow=stream(client,'/api/qa/stream',{'query':'系统采用什么方式融合检索结果，并如何保存与定位引用？','knowledge_base_ids':[kid]})
        cid=flow[0]['conversation_id']
        messages=require(client.get(f'/api/conversations/{cid}'))['messages']
        assert messages[-1]['status']=='complete' and '[1]' in messages[-1]['content']
        assert messages[-1]['citations']
        source=messages[-1]['citations'][0]
        detail=require(client.get(f"/api/documents/{source['document_id']}"))
        assert any(c['id']==source['chunk_id'] for c in detail['chunks'])
        template=require(client.post('/api/templates',json={'name':'功能验收报告','sections':['检索问答','原文追溯']}),201)
        report=stream(client,'/api/report-tasks/stream',{'title':'知源知识库系统功能验收报告','template_id':template['id'],'knowledge_base_id':kid})
        tid=report[0]['task_id']
        record=require(client.get(f'/api/report-tasks/{tid}'))
        assert record['progress']==100 and len(record['sections'])==2
        export=client.get(f'/api/report-tasks/{tid}/export?format=docx')
        assert export.status_code==200
        with zipfile.ZipFile(io.BytesIO(export.content)) as docx:
            assert 'word/document.xml' in docx.namelist()
        print(json.dumps({'live_model':'OK','knowledge_base_id':kid,'document_id':doc['id'],'conversation_id':cid,
                          'answer_characters':len(messages[-1]['content']),'citation_count':len(messages[-1]['citations']),
                          'report_id':tid,'report_status':record['status'],'report_characters':len(record['content']),'word_export':'OK'},ensure_ascii=True))
        client.post('/api/auth/logout')

if __name__=='__main__':
    main()

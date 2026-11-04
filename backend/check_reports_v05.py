"""Opt-in real report-draft/section/CSV acceptance; creates clearly marked synthetic records."""
import argparse
import csv
import io
import json
import time
import zipfile
import httpx

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    if not args.live:
        parser.error('请显式使用 --live；本脚本会用合成资料调用真实模型并保存验收记录')
    def value(response):
        response.raise_for_status();return response.json()
    with httpx.Client(base_url=args.base_url,timeout=180) as client:
        auth=value(client.post('/api/auth/login',json={'username':'admin','password':'admin123'}))
        client.headers['Authorization']='Bearer '+auth['token']
        def wait(jid):
            for _ in range(400):
                job=value(client.get(f'/api/tasks/{jid}'))
                if job['status'] not in ('queued','running'):
                    assert job['status'] in ('completed','needs_review'),job['error']
                    return job
                time.sleep(.25)
            raise RuntimeError('任务验收等待超时')
        kid=value(client.post('/api/knowledge-bases',json={'name':'V0.5 报告草稿验收（合成资料）','description':'只含合成算法说明，不代表项目实际进展'}))['id']
        doc=value(client.post('/api/documents',data={'knowledge_base_id':kid},files={'file':('算法合成验收.md','# 算法说明\n\n关键词检索采用 BM25 算法。混合检索将向量检索和关键词检索的排名通过 RRF 融合。\n\n本资料只用于功能验收，不包含实际项目进度或评测指标。'.encode())}))
        wait(doc['job_id'])
        template=value(client.post('/api/templates',json={'name':'V0.5 双章节合成验收','sections':['关键词检索方案','混合检索方案']}))['id']
        report=value(client.post('/api/report-tasks',json={'title':'V0.5 草稿与单节验收报告（合成资料）','template_id':template,'knowledge_base_id':kid,'requirements':'每节只用一两句话说明资料明确给出的算法，标注引用。不要编造实际进度或指标。'}))
        wait(report['job_id'])
        row=value(client.get(f"/api/report-tasks/{report['id']}"));first,second=row['sections']
        assert first['citations'] and second['citations']
        path=f"/api/report-tasks/{report['id']}/sections/{first['id']}"
        manual='关键词检索采用 BM25 算法；本句为人工编辑的验收草稿，尚未执行事实支撑核验。[1]'
        value(client.patch(path,json={'expected_revision':first['revision'],'content':manual}))
        value(client.patch(path,json={'expected_revision':first['revision']+1,'confirmed':True}))
        assert client.post(path+'/regenerate',json={'expected_revision':first['revision']+2}).status_code==409
        protected=value(client.get(f"/api/report-tasks/{report['id']}"))['sections'][0]
        changed=value(client.post(f"/api/report-tasks/{report['id']}/sections/{second['id']}/regenerate",json={'expected_revision':second['revision'],'instructions':'仅本节用一句话说明 RRF 的融合方式，并引用资料。'}))
        wait(changed['job_id'])
        after=value(client.get(f"/api/report-tasks/{report['id']}"))
        assert after['sections'][0]==protected
        assert after['sections'][1]['revision']==second['revision']+1
        exported=client.get(f"/api/report-tasks/{report['id']}/export?format=docx");exported.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
            assert manual in package.read('word/document.xml').decode()
        params={'action':'保存报告草稿','resource':'报告章节','object_id':first['id']}
        query=value(client.get('/api/audits',params={**params,'page_size':1}))
        csv_result=client.get('/api/audits/export',params={**params,'format':'csv'});csv_result.raise_for_status()
        csv_rows=list(csv.reader(io.StringIO(csv_result.content.decode('utf-8-sig'))))
        assert len(csv_rows)-1==query['total']
        client.post('/api/auth/logout')
        print(json.dumps({'report_id':report['id'],'first_section_id':first['id'],'second_section_id':second['id'],
            'draft_saved':True,'confirmed_section_unchanged':True,'second_revision':after['sections'][1]['revision'],
            'word_bytes':len(exported.content),'filtered_csv_rows':len(csv_rows)-1,'section_job_id':changed['job_id'],
            'report_fact_entailment':'not_implemented_inspection_only'},ensure_ascii=True))

if __name__=='__main__':
    main()

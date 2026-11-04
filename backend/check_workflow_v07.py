"""Opt-in real-model acceptance on explicitly synthetic knowledge, never original reports."""
import argparse
import io
import json
import time
import zipfile
from pathlib import Path
import httpx
from check_reports_v06 import fixture

def sample():
    raw=fixture();out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            data=source.read(name)
            if name=='word/document.xml':data=data.decode().replace('<w:sectPr>','<w:p><w:r><w:t>项目负责人：{{项目负责人}}</w:t></w:r></w:p><w:sectPr>').encode()
            target.writestr(name,data)
    return out.getvalue()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--base-url',default='http://127.0.0.1:8000');args=parser.parse_args()
    if not args.live:parser.error('请使用 --live；仅发送合成资料，但会新增验收记录并调用真实Kimi')
    folder=Path(__file__).resolve().parent/'data'/'acceptance-v07';folder.mkdir(exist_ok=True)
    def val(r):r.raise_for_status();return r.json()
    with httpx.Client(base_url=args.base_url,timeout=180) as c:
        login=val(c.post('/api/auth/login',json={'username':'admin','password':'admin123'}));c.headers['Authorization']='Bearer '+login['token']
        def wait(jid):
            for _ in range(1200):
                job=val(c.get(f'/api/tasks/{jid}'))
                if job['status'] not in ('queued','running'):
                    assert job['status'] in ('completed','needs_review'),job['error'];return job
                time.sleep(.4)
            raise RuntimeError('真实模型验收等待超时')
        kid=val(c.post('/api/knowledge-bases',json={'name':'V0.7 报告工作流验收（合成资料）','description':'仅算法合成证据，不代表项目实际进度'}))['id']
        doc=val(c.post('/api/documents',data={'knowledge_base_id':kid},files={'file':('报告合成证据.txt','关键词检索采用 BM25 算法。混合检索将向量检索与关键词检索的排名通过 RRF 融合。本资料仅用于功能验收，不包含实际业务日期、上线进度或评测指标。'.encode())}));wait(doc['job_id'])
        parsed=val(c.post('/api/templates/import-docx',files={'file':('合成工作流模板.docx',sample())}));heads=[b for b in parsed['structure']['blocks'] if b['type']=='heading']
        maps=[{'key':f['key'],'target':'report_title' if f['key']=='报告名称' else 'generated_at' if f['key']=='生成时间' else 'user_input' if f['key']=='项目负责人' else 'section','required':f['key']=='项目负责人','section_block_id':heads[1 if f['key']=='混合正文' else 0]['id']} for f in parsed['structure']['fields']]
        template=val(c.post('/api/report-templates',json={'blueprint':{'name':'V0.7 DOCX真实套用验收模板','purpose':'解释资料中明确的算法，不编造项目进展','format':{'include_sources':False},'sections':[{'title':h['text'],'level':h['level'],'block_id':h['id'],'instructions':'只写资料中的算法事实，每节一句，不重复其他节。','target_words':50} for h in heads]},'import_id':parsed['import_id'],'mappings':maps,'knowledge_base_ids':[kid],'lifecycle':'published'}))['id']
        body={'title':'V0.7 报告编制闭环验收（合成资料）','template_id':template,'knowledge_base_id':kid,'workflow_version':1,'field_values':{'项目负责人':'合成验收用户'},'requirements':'只用资料里的算法事实，保留句末引用；每节不超过两句。不写标题、列表、日期或真实项目进度。'}
        checked=val(c.post('/api/reports/preflight',json=body));assert checked['missing']==0
        report=val(c.post('/api/report-tasks',json={**body,'preflight_id':checked['preflight_id']}));wait(report['job_id'])
        def get():return val(c.get(f"/api/reports/{report['id']}"))
        row=get();first,second=row['sections'];assert first['verification']['accepted_count']>0 and second['verification']['accepted_count']>0
        base=f"/api/report-tasks/{row['id']}/sections"
        val(c.patch(f"{base}/{first['id']}",json={'expected_revision':first['revision'],'confirmed':True}));protected=get()['sections'][0]
        job=val(c.post(f"{base}/{second['id']}/regenerate",json={'expected_revision':second['revision'],'preview':True,'instructions':'只用一句话说明资料中RRF融合方式，引用资料。'}));wait(job['job_id'])
        after=get();assert after['sections'][0]==protected and after['sections'][1]['content']==second['content']
        candidate=after['candidates'][0]
        val(c.post(f"/api/reports/{row['id']}/candidates/{candidate['id']}",json={'expected_revision':second['revision'],'action':'accept'}))
        after=get()
        for sec in after['sections']:val(c.post(f"/api/reports/{row['id']}/sections/{sec['id']}/review",json={'expected_revision':sec['revision']}))
        after=get();assert after['quality']['ready'],after['quality']
        final=val(c.post(f"/api/reports/{row['id']}/finalize",json={'expected_revision':after['revision'],'fixed_text_reviewed':True}))
        for fmt in ('md','docx','pdf'):
            result=c.get(f"/api/reports/{row['id']}/export",params={'format':fmt,'mode':'final'});result.raise_for_status();(folder/('workflow-final.'+fmt)).write_bytes(result.content)
        md=(folder/'workflow-final.md').read_text(encoding='utf-8-sig');assert '{{' not in md and '合成验收用户' in md and '参考来源' in md and 'SHA256' not in md
        with zipfile.ZipFile(folder/'workflow-final.docx') as z:assert b'<w:tbl>' in z.read('word/document.xml')
        assert (folder/'workflow-final.pdf').read_bytes().startswith(b'%PDF')
        # A one-section authored fixture makes the browser's autosave/restore path testable without more paid calls.
        ui_template=val(c.post('/api/report-templates',json={'blueprint':{'name':'V0.7 页面交互验收模板（固定结构）','sections':[{'title':'交互验收说明','kind':'fixed','fixed_content':'本报告仅用于自动保存与恢复交互验收，不代表真实项目结论。'}]},'knowledge_base_ids':[kid],'lifecycle':'published'}))['id']
        c.post('/api/auth/logout')
        print(json.dumps({'report_id':row['id'],'template_id':template,'ui_template_id':ui_template,'knowledge_base_id':kid,'release_id':final['id'],'candidate_id':candidate['id'],'protected_section_unchanged':True,'filled_placeholders':len(maps),'exports_folder':str(folder)},ensure_ascii=True))

if __name__=='__main__':main()

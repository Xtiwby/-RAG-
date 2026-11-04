"""Opt-in live Kimi report grounding and DOCX mapping acceptance, synthetic data only."""
import argparse
import io
import json
import time
import zipfile
from pathlib import Path
import httpx
from app import report_export,blueprints

def fixture():
    base=report_export.docx('# 合成验收模板\n\n## 关键词检索方案\n\n固定说明：以下为结构解析验收，不代表项目进展。\n\n{{关键词正文}}\n\n### 混合检索方案\n\n{{混合正文}}',blueprints.ReportFormat())
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as source,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            data=source.read(name)
            if name=='word/document.xml':
                xml=data.decode().replace('{{关键词正文}}','{{关键词</w:t></w:r><w:r><w:t>正文}}')
                table='<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:p><w:r><w:t>报告名称</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>{{报告名称}}</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>固定合并单元格 {{生成时间}}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                data=xml.replace('<w:sectPr>',table+'<w:sectPr>').encode()
            target.writestr(name,data)
    return out.getvalue()

def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--base-url',default='http://127.0.0.1:8000');args=p.parse_args()
    if not args.live:p.error('使用 --live 会新增合成验收资料，并调用真实Kimi，产生少量额度消耗')
    path=Path(__file__).resolve().parent/'data'/'acceptance-v06-template.docx';path.write_bytes(fixture())
    def value(response):response.raise_for_status();return response.json()
    with httpx.Client(base_url=args.base_url,timeout=180) as c:
        auth=value(c.post('/api/auth/login',json={'username':'admin','password':'admin123'}));c.headers['Authorization']='Bearer '+auth['token']
        def wait(jid):
            for _ in range(900):
                job=value(c.get(f'/api/tasks/{jid}'))
                if job['status'] not in ('queued','running'):
                    assert job['status'] in ('completed','needs_review'),job['error'];return job
                time.sleep(.3)
            raise RuntimeError('模型任务等待超时')
        kid=value(c.post('/api/knowledge-bases',json={'name':'V0.6 模板与事实核验（合成资料）','description':'仅含算法合成证据，不表示真实项目进展'}))['id']
        doc=value(c.post('/api/documents',data={'knowledge_base_id':kid},files={'file':('混合检索合成证据.txt','关键词检索采用 BM25 算法。混合检索将向量检索与关键词检索的排名通过 RRF 融合。本资料仅用于验收，不包含真实上线进度或评测指标。'.encode())}));wait(doc['job_id'])
        parsed=value(c.post('/api/templates/import-docx',files={'file':('V0.6结构验收.docx',path.read_bytes())}))
        structure=parsed['structure'];heads=[b for b in structure['blocks'] if b['type']=='heading']
        assert len(heads)==2 and heads[1]['level']==2 and structure['tables'][0]['rows'][1]['cells'][0]['column_span']==2
        mappings=[{'key':f['key'],'target':'report_title' if f['key']=='报告名称' else 'generated_at' if f['key']=='生成时间' else 'section','section_block_id':heads[1 if f['key']=='混合正文' else 0]['id']} for f in structure['fields']]
        tid=value(c.post(f"/api/templates/imports/{parsed['import_id']}/save",json={'name':'V0.6 DOCX结构验收模板','purpose':'仅基于合成算法证据说明方案','version':'v1.0','knowledge_base_ids':[kid],'mappings':mappings}))['id']
        report=value(c.post('/api/report-tasks',json={'title':'V0.6 模板与章节核验报告（合成资料）','template_id':tid,'knowledge_base_id':kid,'requirements':'每节只写一两句证据给出的算法事实并在句末引用。不要标题、列表、结论或实际进度。'}));wait(report['job_id'])
        def get():return value(c.get(f"/api/report-tasks/{report['id']}"))
        row=get();first,second=row['sections']
        assert first['verification']['accepted_count']>0,row
        firstpath=f"/api/report-tasks/{report['id']}/sections/{first['id']}"
        value(c.patch(firstpath,json={'expected_revision':first['revision'],'confirmed':True}));protected=get()['sections'][0]
        ref=next(i for i,e in enumerate(second['citations'],1) if 'BM25' in e['text'])
        secondpath=f"/api/report-tasks/{report['id']}/sections/{second['id']}"
        value(c.patch(secondpath,json={'expected_revision':second['revision'],'content':f'关键词检索使用 SQL 全表扫描。[{ref}]'}))
        jid=value(c.post(secondpath+'/verify',json={'expected_revision':second['revision']+1}))['job_id'];wait(jid)
        rejected=get()['sections'][1];assert rejected['verification']['accepted_count']==0 and rejected['verification']['rejected_count']==1,rejected
        assert rejected['verification']['state']=='insufficient'
        jid=value(c.post(secondpath+'/regenerate',json={'expected_revision':rejected['revision'],'instructions':'只用一句话说明资料中的RRF融合事实，句末引用，不输出标题。'}))['job_id'];wait(jid)
        after=get();assert after['sections'][0]==protected
        assert after['sections'][1]['verification']['accepted_count']>0
        md=c.get(f"/api/report-tasks/{report['id']}/export?format=md");md.raise_for_status()
        assert '核验记录' in md.text and 'SQL' not in md.text
        c.post('/api/auth/logout')
        print(json.dumps({'report_id':report['id'],'template_id':tid,'import_id':parsed['import_id'],'fixture_path':str(path),
              'headings':len(heads),'fields':len(mappings),'tables':len(structure['tables']),
              'accepted_claims':sum(s['verification']['accepted_count'] for s in after['sections']),
              'real_wrong_fact_rejected':True,'protected_section_unchanged':True,'section_job_id':jid},ensure_ascii=True))

if __name__=='__main__':main()

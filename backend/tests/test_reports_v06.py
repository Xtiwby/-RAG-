"""FR-RP-02 / FR-RP-04 integration, using synthetic OOXML and mocked judges."""
import hashlib
from functools import lru_cache
import io
import json
import zipfile
import pytest
from test_api import client,admin,application_lifespan,kb,upload,login,create_member,wait_job,generated_report,section_path,fake_model
from app import rag,store,template_docx,grounding,report_grounding

@lru_cache(maxsize=1)
def template_bytes():
    # Inherited outline level, split-run placeholder, merged/nested table, fields, SDT.
    body='''<w:p><w:pPr><w:pStyle w:val="CustomOne"/></w:pPr><w:r><w:t>混合检索方案</w:t></w:r></w:p>
    <w:p><w:r><w:t>固定说明 正文：</w:t></w:r><w:r><w:t>{{检索</w:t></w:r><w:r><w:t>正文}}</w:t></w:r></w:p>
    <w:p><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:r><w:t>检索实施</w:t></w:r></w:p>
    <w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>方案表 ${表格内容}</w:t></w:r></w:p></w:tc></w:tr>
    <w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p><w:r><w:t>合并延续</w:t></w:r></w:p></w:tc><w:tc><w:tbl><w:tr><w:tc><w:p><w:r><w:t>&lt;&lt;嵌套值&gt;&gt;</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>
    <w:p><w:fldSimple w:instr='MERGEFIELD "报告名"'><w:r><w:t>标题缓存</w:t></w:r></w:fldSimple></w:p>
    <w:sdt><w:sdtPr><w:tag w:val="控件正文"/></w:sdtPr><w:sdtContent><w:p><w:r><w:t>控件缓存</w:t></w:r></w:p></w:sdtContent></w:sdt>
    <w:p><w:r><w:drawing/></w:r></w:p>'''
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+body+'</w:body></w:document>')
        z.writestr('word/styles.xml','<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:styleId="CustomOne"><w:basedOn w:val="BaseHeading"/></w:style><w:style w:styleId="BaseHeading"><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style></w:styles>')
        z.writestr('word/_rels/document.xml.rels','<Relationships><Relationship TargetMode="External" Target="https://not-contacted.invalid"/></Relationships>')
        z.writestr('word/header1.xml','<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>页眉 {{日期}}</w:t></w:r></w:p></w:hdr>')
    return b.getvalue()

def parsed_import(headers):
    response=client.post('/api/templates/import-docx',headers=headers,files={'file':('结构验收.docx',template_bytes())})
    assert response.status_code==201,response.text
    return response.json()

def mapping_body(parsed,kid):
    heads=[b for b in parsed['structure']['blocks'] if b['type']=='heading']
    return {'name':'解析结构验收','purpose':'知识库报告','description':'包含结构与字段校正','version':'v1.0','knowledge_base_ids':[kid],
            'headings':[{'block_id':b['id'],'level':b['level']} for b in heads],
            'mappings':[{'key':f['key'],'target':'report_title' if f['key']=='报告名' else 'generated_at' if f['key']=='日期' else 'section','section_block_id':heads[0]['id']} for f in parsed['structure']['fields']]}

def test_docx_structural_parse_versions_mapping_scope_original(admin):
    kid=kb(admin);parsed=parsed_import(admin);structure=parsed['structure'];heads=[b for b in structure['blocks'] if b['type']=='heading']
    assert [(b['text'],b['level']) for b in heads]==[('混合检索方案',1),('检索实施',2)]
    assert {f['key'] for f in structure['fields']}=={'检索正文','表格内容','嵌套值','报告名','控件正文','日期'}
    paragraph=next(b for b in structure['blocks'] if b['text'].startswith('固定说明'))
    assert paragraph['segments'][0]['type']=='fixed' and paragraph['segments'][1]['key']=='检索正文'
    assert len(structure['tables'])==2
    table=structure['tables'][0];assert table['grid_columns']==2 and table['rows'][0]['header']
    assert table['rows'][0]['cells'][0]['column_span']==2 and table['rows'][1]['cells'][0]['vertical_merge']=='continue'
    assert structure['tables'][1]['parent_table']==table['id']
    assert any('外部' in w for w in structure['warnings']) and any('图片' in w for w in structure['warnings'])
    assert parsed['sha256']==hashlib.sha256(template_bytes()).hexdigest()
    body=mapping_body(parsed,kid)
    response=client.post(f"/api/templates/imports/{parsed['import_id']}/save",headers=admin,json=body)
    assert response.status_code==201,response.text
    tid=response.json()['id'];saved=client.get(f'/api/templates/{tid}/structure',headers=admin).json()
    assert saved['field_mappings']==[{**m,'value':'','required':False,'input_type':'text'} for m in body['mappings']]
    assert saved['blueprint']['sections'][1]['level']==2
    assert '检索正文' in saved['blueprint']['sections'][0]['instructions']
    assert client.get(f'/api/templates/{tid}/original',headers=admin).content==template_bytes()
    assert client.post(f"/api/templates/imports/{parsed['import_id']}/save",headers=admin,json=body).status_code==409
    updated={**body,'version':'v2.0','parent_id':tid,'headings':[{'block_id':heads[1]['id'],'level':3}]}
    child=client.post(f"/api/templates/imports/{parsed['import_id']}/save",headers=admin,json=updated).json()['id']
    assert client.get(f'/api/templates/{child}/structure',headers=admin).json()['blueprint']['sections'][1]['level']==3
    assert client.get(f'/api/templates/{tid}/structure',headers=admin).json()['blueprint']['sections'][1]['level']==2
    otherkid=kb(admin)
    assert client.post('/api/report-tasks',headers=admin,json={'title':'跨库','template_id':tid,'knowledge_base_id':otherkid}).status_code==403
    user,member=create_member(admin)
    assert client.get(f'/api/templates/{tid}/structure',headers=member).status_code==403
    assert client.get(f'/api/templates/{tid}/original',headers=member).status_code==403
    assert client.post('/api/templates/import-docx',headers=member,files={'file':('test.docx',template_bytes())}).status_code==403
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[{'knowledge_base_id':kid,'access':'read'}]})
    assert client.get(f'/api/templates/{tid}/original',headers=member).status_code==200
    assert any(t['id']==tid for t in client.get('/api/templates',headers=member).json())

def test_docx_bad_zip_xml_entity_wrong_mapping_and_correction(admin):
    assert client.post('/api/templates/import-docx',headers=admin,files={'file':('bad.doc',b'bad')}).status_code==422
    assert client.post('/api/templates/import-docx',headers=admin,files={'file':('bad.docx',b'bad')}).status_code==422
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:z.writestr('word/document.xml','<?xml version="1.0" encoding="utf-16"?><!DOCTYPE x [<!ENTITY a "x">]><x/>'.encode('utf-16'))
    assert client.post('/api/templates/import-docx',headers=admin,files={'file':('entity.docx',raw.getvalue())}).status_code==422
    parsed=parsed_import(admin);body=mapping_body(parsed,kb(admin));url=f"/api/templates/imports/{parsed['import_id']}/save"
    assert client.post(url,headers=admin,json={**body,'mappings':[]}).status_code==422
    invalid=json.loads(json.dumps(body));invalid['mappings'][0]['section_block_id']='missing'
    assert client.post(url,headers=admin,json=invalid).status_code==422
    assert client.post(url,headers=admin,json={**body,'headings':[{'block_id':'t1','level':1}]}).status_code==422
    # Reclassify a formerly fixed paragraph into a heading, then map a field to it.
    bid=next(b['id'] for b in parsed['structure']['blocks'] if b['text'].startswith('固定说明'))
    corrected={**body,'headings':[{'block_id':bid,'level':2}]}
    corrected['mappings'][0]['section_block_id']=bid
    result=client.post(url,headers=admin,json=corrected)
    assert result.status_code==201,result.text
    saved=client.get(f"/api/templates/{result.json()['id']}/structure",headers=admin).json()
    assert len(saved['blueprint']['sections'])==3

async def mixed_judge(messages,max_tokens=4096):
    data=json.loads(messages[-1]['content'])
    if 'verification_schema' not in data:
        yield '混合检索采用 BM25 和 RRF。[1]\n'
        yield '混合检索使用 SQL 全表扫描。[1]\n'
        yield '系统已经正式上线。\n'
        return
    yield json.dumps({'verdicts':[{'claim_id':c['claim_id'],'verdict':'contradicted' if 'SQL' in c['text'] else 'supported',
        'quotes':[{'reference':r,'quote':next(e['text'] for e in data['evidence_json'] if e['reference']==r)} for r in c['references']]} for c in data['claims']]})

def test_report_fact_judge_blocks_unsupported_before_events_storage_export(admin,monkeypatch):
    monkeypatch.setattr(rag,'llm_stream',mixed_judge)
    kid=kb(admin);upload(admin,kid)
    tid=client.post('/api/templates',headers=admin,json={'name':'事实校验','sections':['混合检索']} ).json()['id']
    created=client.post('/api/report-tasks',headers=admin,json={'title':'混合检索事实验收','template_id':tid,'knowledge_base_id':kid}).json()
    assert wait_job(admin,created['job_id'])['status']=='needs_review'
    row=client.get(f"/api/report-tasks/{created['id']}",headers=admin).json();section=row['sections'][0];v=section['verification']
    assert v['state']=='partial' and v['accepted_count']==1 and v['rejected_count']==2
    assert 'BM25' in section['content'] and 'SQL' not in section['content'] and '正式上线' not in section['content']
    assert v['content_sha256']==hashlib.sha256(section['content'].encode()).hexdigest()
    published=client.get(f"/api/tasks/{created['job_id']}/events",headers=admin).json()
    assert any(e['type']=='verification' for e in published)
    assert 'SQL' not in json.dumps(published,ensure_ascii=False)
    for format in ('md','docx'):
        exported=client.get(f"/api/report-tasks/{created['id']}/export?format={format}",headers=admin)
        text=exported.text if format=='md' else zipfile.ZipFile(io.BytesIO(exported.content)).read('word/document.xml').decode()
        assert 'SQL' not in text and '正式上线' not in text and '核验记录' in text and '原文' in text

@pytest.mark.parametrize('fault',['forged_quote','missing_verdict','numbers','no_citation'])
def test_report_judge_fail_closed(admin,monkeypatch,fault):
    async def model(messages,max_tokens=4096):
        data=json.loads(messages[-1]['content'])
        if 'verification_schema' not in data:
            yield '混合检索已经服务9999用户。[1]' if fault=='numbers' else '混合检索已正式上线。' if fault=='no_citation' else '混合检索采用 BM25。[1]'
            return
        if fault=='missing_verdict':yield '{"verdicts":[]}';return
        yield json.dumps({'verdicts':[{'claim_id':c['claim_id'],'verdict':'supported','quotes':[{'reference':1,'quote':'编造的原文引文' if fault=='forged_quote' else data['evidence_json'][0]['text']}]} for c in data['claims']]})
    monkeypatch.setattr(rag,'llm_stream',model)
    kid=kb(admin);upload(admin,kid)
    template=client.post('/api/templates',headers=admin,json={'name':'保守核验','sections':['混合检索']}).json()['id']
    created=client.post('/api/report-tasks',headers=admin,json={'title':'混合检索','template_id':template,'knowledge_base_id':kid}).json()
    assert wait_job(admin,created['job_id'])['status']=='needs_review'
    section=client.get(f"/api/report-tasks/{created['id']}",headers=admin).json()['sections'][0]
    assert section['verification']['state']=='insufficient' and '证据不足' in section['content']
    assert '正式上线' not in section['content'] and '9999' not in section['content']

def test_manual_verify_hash_invalidation_history_acl_and_regenerate(admin,monkeypatch):
    report=generated_report(admin,monkeypatch);first,second=report['sections'];path=section_path(report,first)
    assert first['verification']['state']=='verified'
    edited='混合检索采用 BM25 和 RRF。[1]'
    assert client.patch(path,headers=admin,json={'expected_revision':0,'content':edited}).status_code==200
    row=client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()
    assert row['sections'][0]['verification']['state']=='unverified_manual'
    _,other=create_member(admin)
    assert client.post(path+'/verify',headers=other,json={'expected_revision':1}).status_code==404
    job=client.post(path+'/verify',headers=admin,json={'expected_revision':1}).json()
    assert wait_job(admin,job['job_id'])['status']=='completed'
    row=client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()
    checked=row['sections'][0]
    assert checked['content']==edited and checked['verification']['state']=='verified' and checked['revision']==2
    assert row['sections'][1]==second
    history=client.get(path+'/history',headers=admin).json()
    assert history[-1]['verification']['state']=='verified' and history[0]['verification']['state']=='unverified_manual'
    cites=store.one('SELECT citations FROM report_sections WHERE id=?',(first['id'],))['citations']
    wrong=json.loads(cites);wrong[0]['version']='tampered-version'
    store.execute('UPDATE report_sections SET citations=? WHERE id=?',(json.dumps(wrong),first['id']))
    assert client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()['sections'][0]['verification']['state']=='unverified_manual'
    store.execute('UPDATE report_sections SET citations=? WHERE id=?',(cites,first['id']))
    # A valid old judge result cannot be attached to changed text.
    store.execute('UPDATE report_sections SET content=? WHERE id=?',('被篡改的正文',first['id']))
    assert client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()['sections'][0]['verification']['state']=='unverified_manual'
    monkeypatch.setattr(rag,'llm_stream',mixed_judge)
    job=client.post(path+'/regenerate',headers=admin,json={'expected_revision':2}).json()
    assert wait_job(admin,job['job_id'])['status']=='needs_review'
    checked=client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()['sections'][0]
    assert checked['verification']['state']=='partial' and 'SQL' not in checked['content']

def test_legacy_reports_not_retroactively_verified_and_no_evidence(admin,monkeypatch):
    report=generated_report(admin,monkeypatch);first=report['sections'][0]
    store.execute("UPDATE report_sections SET verification='{}' WHERE id=?",(first['id'],))
    row=client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()
    assert row['sections'][0]['verification']['state']=='legacy'
    assert '历史章节未执行事实支撑核验' in client.get(f"/api/report-tasks/{report['id']}/export?format=md",headers=admin).text
    empty=kb(admin)
    template=client.post('/api/templates',headers=admin,json={'name':'空证据','sections':['不存在的算法']}).json()['id']
    created=client.post('/api/report-tasks',headers=admin,json={'title':'无依据报告','template_id':template,'knowledge_base_id':empty}).json()
    assert wait_job(admin,created['job_id'])['status']=='needs_review'
    row=client.get(f"/api/report-tasks/{created['id']}",headers=admin).json()
    assert row['sections'][0]['verification']['state']=='insufficient'

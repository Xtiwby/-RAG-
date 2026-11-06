"""Report authoring workflow: immutable templates, preflight, review, candidates, releases."""
import copy
import hashlib
import json
import re
import secrets
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
from typing import Literal
from urllib.parse import quote
from fastapi import Depends,HTTPException,Query,Request
from fastapi.responses import Response
from pydantic import Field
from . import blueprints,settings,store as s,rag,jobs,template_docx,report_grounding as g,report_layout,report_retrieval,object_storage

def decoded(value,default=None):return report_layout.decode(value,{} if default is None else default)

class TemplateVersion(settings.Strict):
    blueprint:blueprints.ReportBlueprint
    import_id:int|None=None
    parent_id:int|None=None
    version:str=Field(default='',max_length=40)
    knowledge_base_ids:list[int]=Field(default_factory=list,max_length=100)
    mappings:list[template_docx.Mapping]=Field(default_factory=list,max_length=1000)
    lifecycle:Literal['draft','published']='draft'

class Revision(settings.Strict):
    expected_revision:int=Field(ge=0)

class ReportChange(Revision):
    title:str|None=Field(default=None,min_length=1,max_length=100)
    document_state:Literal['draft','in_review','archived']|None=None

class Finalize(Revision):
    fixed_text_reviewed:bool=False

class Search(settings.Strict):
    query:str=Field(min_length=1,max_length=2000)

class Attach(Revision):
    chunk_id:int

class CandidateAction(Revision):
    action:Literal['accept','discard']

def migrate():
    with s.connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS report_preflights(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,
            signature TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL)''')
migrate()

def template_snapshot(row):
    result={k:row.get(k) for k in ('id','name','version','import_id','structure','field_mappings','knowledge_base_ids','blueprint')}
    structure=decoded(row.get('structure'))
    if row.get('import_id') and structure.get('schema_version',0)<3:
        original=s.one('SELECT * FROM template_imports WHERE id=?',(row['import_id'],))
        if not original or not object_storage.exists(original):raise HTTPException(409,'旧模板原件缺失，请重新导入后发布')
        data=object_storage.read_bytes(original)
        if hashlib.sha256(data).hexdigest()!=original['sha256']:raise HTTPException(409,'模板原件校验失败，请重新导入')
        upgraded=template_docx.parse_docx(data);old={b['id']:b for b in structure.get('blocks',[])}
        for block in upgraded['blocks']:
            if block['id'] in old:
                saved=old[block['id']]
                block.update(type=saved['type'],level=saved['level'])
                if saved['type']=='heading':block['text']=saved['text']
        result['structure']=json.dumps(upgraded,ensure_ascii=False)
    return result

def validate_input(body,template):
    if not body.title.strip():raise HTTPException(422,'报告名称不能为空')
    if template['lifecycle']!='published':raise HTTPException(409,'模板尚未发布或已停用，请选择已发布版本')
    start=body.period_start;end=body.period_end
    try:
        if start:date.fromisoformat(start)
        if end:date.fromisoformat(end)
    except ValueError:raise HTTPException(422,'报告业务日期必须为 YYYY-MM-DD') from None
    if start and end and start>end:raise HTTPException(422,'开始日期不能晚于结束日期')
    if len(body.field_values)>100 or any(len(k)>100 or len(v)>4000 for k,v in body.field_values.items()):raise HTTPException(422,'业务字段数量或长度超限')
    for m in decoded(template['field_mappings'],[]):
        if m['target']!='user_input':continue
        value=body.field_values.get(m['key'],'').strip()
        if m.get('required') and not value:raise HTTPException(422,'请填写业务字段：'+m['key'])
        try:
            if value and m.get('input_type')=='date':date.fromisoformat(value)
            if value and m.get('input_type')=='number':
                import math
                if not math.isfinite(float(value)):raise ValueError()
        except ValueError:raise HTTPException(422,'业务字段格式不正确：'+m['key']) from None

def signature(body,template):
    value={k:body.model_dump()[k] for k in ('title','template_id','knowledge_base_id','requirements','period_start','period_end','field_values')}
    value['template']=template_snapshot(template);value['retrieval']=settings.get().retrieval.model_dump()
    value['embedding']=settings.get().embedding.model_dump()
    value['reranking']=settings.get().reranking.model_dump()
    value['answer_quality']=settings.get().answer_quality.model_dump()
    value['retrieval_policy']=report_retrieval.POLICY
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def use_preflight(body,template,user):
    validate_input(body,template)
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(timespec='seconds')
    row=s.one('SELECT * FROM report_preflights WHERE id=? AND user_id=? AND created_at>=?',(body.preflight_id,user['id'],cutoff))
    if not row or row['signature']!=signature(body,template):raise HTTPException(409,'生成参数、模板或检索配置已改变，请重新检查大纲与证据')
    data=decoded(row['data'])
    if data['missing'] and not body.allow_missing:raise HTTPException(422,'部分章节证据不足，请补充资料，或明确选择保留“待补充”章节')
    for section in data['sections']:
        for evidence in section['evidence']:
            doc=s.one("SELECT * FROM documents WHERE id=? AND deleted=0 AND status='ready'",(evidence['document_id'],))
            latest=s.one('SELECT MAX(id) id FROM documents WHERE name=? AND knowledge_base_id=? AND deleted=0',(doc['name'],doc['knowledge_base_id'])) if doc else None
            if not doc or latest['id']!=doc['id']:raise HTTPException(409,'预检查后来源文档已更新或不可用，请重新检查证据')
    return data

def canonical(report):
    missing_plan=not decoded(report['blueprint'])
    missing_snapshot=not decoded(report.get('template_snapshot'))
    if missing_plan or missing_snapshot:
        row=s.one('SELECT * FROM report_templates WHERE id=?',(report['template_id'],))
        report={**report}
        if missing_plan:
            if not row:raise HTTPException(409,'历史模板缺失，无法恢复报告大纲')
            report['blueprint']=blueprints.from_template(row).model_dump_json()
        if missing_snapshot and row:report['template_snapshot']=json.dumps(template_snapshot(row),ensure_ascii=False)
    return report

def section_specs(report):return blueprints.ReportBlueprint.model_validate(decoded(canonical(report)['blueprint'])).sections

def quality(report,sections):
    specs=section_specs(report);issues=[];warnings=[];passed=0;reviewed=0;human_accepted=0
    for i,section in enumerate(sections):
        spec=specs[i] if i<len(specs) else blueprints.ReportSection(title=section['title'])
        v=g.current(section)
        busy=section.get('generation_status') in ('queued','running')
        can_publish=v.get('state')=='verified' or (v.get('publishable') and v.get('state')=='partial') or spec.kind in ('fixed','container')
        if can_publish:passed+=1
        accepted=section.get('reviewed_revision',-1)==section['revision']
        if accepted:reviewed+=1
        if busy:issues.append({'section_id':section['id'],'kind':'running','message':'本节任务仍在执行'})
        if spec.required and not section['content'].strip() and spec.kind!='container':issues.append({'section_id':section['id'],'kind':'empty','message':'必需章节缺少正文'})
        included=spec.required or bool(section['content'].strip())
        if included and not can_publish:
            warnings.append({'section_id':section['id'],'title':section['title'],'kind':'evidence','verification_state':v.get('state','legacy'),'human_accepted':accepted,
                'message':section['title']+'：证据不足或尚未通过核验；'+('当前修订已由人工审阅放行，不阻止定稿。' if accepted else '请人工审阅后决定是否保留并定稿。')})
            if accepted:human_accepted+=1
        if included and not accepted:issues.append({'section_id':section['id'],'kind':'review','message':'本节尚未审阅通过当前修订'})
    if len(sections)!=len(specs):issues.append({'kind':'incomplete','message':'章节尚未全部生成'})
    if report['status'] in ('queued','generating') or s.one("SELECT id FROM jobs WHERE id=? AND status IN ('queued','running')",(report['job_id'],)):
        issues.append({'kind':'running','message':'整份报告任务尚未完成'})
    return {'issues':issues,'warnings':warnings,'ready':not issues,'verified_sections':passed,'reviewed_sections':reviewed,
            'human_accepted_sections':human_accepted,'decision_policy':'human-review-final','total_sections':len(specs)}

def editable(c,tid):
    report=dict(c.execute('SELECT * FROM report_tasks WHERE id=?',(tid,)).fetchone())
    if report['document_state'] in ('finalized','archived'):raise HTTPException(409,'报告已定稿或归档，请先创建可编辑修订')
    return report

def snapshot_report(report,sections,kb_name,decision=None):
    return {'report':report,'sections':sections,'knowledge_base_name':kb_name,
            'nodes':report_layout.nodes(report,sections,kb_name,draft=False),'decision':decision or {}}

async def generate_section(instruction,evidence,authorize,kind='fact',coverage_question=None):
    if kind in ('fixed','container'):raise ValueError('固定结构不得调用生成模型')
    content='';checks=[];count=0
    if evidence:
        extra={'fact':'只写连续事实句，不输出标题、编号或列表标记；每句末尾引用证据。','analysis':'仅给出有证据基础的分析，明确是推断，不冒充已发生事实，每句末尾引用依据。','recommendation':'仅提出有证据基础的建议，不冒充已经实施的事实，每句末尾引用依据。'}[kind]
        candidate=''
        async for token in rag.llm_stream(rag.evidence_prompt(instruction+' '+extra,evidence)):
            authorize();candidate+=token
            if len(candidate)>32000:raise ValueError('候选章节过长，未发布未经核验内容')
        lines=[]
        for line in candidate.splitlines():
            if re.match(r'^\s*#{1,6}\s+',line) and not re.search(r'\[\d+\]',line):continue
            line=re.sub(r'^\s*(?:[-*•]|\d+[.)、](?!\d))\s*','',line).strip()
            if line:lines.append(line)
        units=[unit for line in lines for unit in g.grounding.sentences(line)]
        if len(units)>128:raise ValueError('本节超过128个核验句段，请缩减目标字数')
        # Verify up to eight sentences per model call; never persist raw output.
        for offset in range(0,len(units),8):
            authorize()
            supported,batch=await g.grounding.check_batch(units[offset:offset+8],evidence,count+1,claim_kind=kind)
            authorize();count+=len(batch);checks.extend(batch)
            for unit in supported:content+=unit+'\n'
    coverage={}
    if evidence and kind=='fact' and settings.get().answer_quality.enabled:
        from . import answer_quality
        coverage,extra,batch=await answer_quality.assess(coverage_question or instruction,evidence,content.splitlines(),checks,count)
        authorize();checks.extend(batch)
        for text in extra:content+=text+'\n'
    if not content:content='【待补充】当前资料不足以支持本节内容，请补充证据后重新生成。'
    verification=g.record(content,checks,evidence=evidence,coverage=coverage)
    verification['publishable']=bool(checks) and any(c['status']=='supported' for c in checks)
    verification['content_kind']=kind
    return content,verification

def install(app,auth,require,check_kb,report_task,ReportBody,source_lines):
    def template(tid,user):
        require(user,'reports.generate');row=s.one('SELECT * FROM report_templates WHERE id=?',(tid,))
        if not row or row['lifecycle']=='deleted':raise HTTPException(404,'模板不存在或已删除')
        if not template_docx.allowed(row,user):raise HTTPException(403,'无权访问此模板')
        return row
    def detail(tid,user):
        report=canonical(report_task(user,tid))
        sections=s.rows('SELECT * FROM report_sections WHERE task_id=? ORDER BY ordinal',(tid,))
        report['quality']=quality(report,sections)
        report['sections']=[{**section,'citations':decoded(section['citations'],[]),'verification':g.current(section)} for section in sections]
        report['releases']=s.rows('SELECT id,version,created_at FROM report_releases WHERE report_id=? ORDER BY version DESC',(tid,))
        report['candidates']=[{**row,'citations':decoded(row['citations'],[]),'verification':decoded(row['verification'])} for row in s.rows("SELECT * FROM report_candidates WHERE report_id=? AND status='pending' ORDER BY id DESC",(tid,))]
        report['blueprint']=decoded(report['blueprint']);report['template_snapshot']=decoded(report['template_snapshot']);report['field_values']=decoded(report['field_values'])
        return report

    @app.get('/api/report-templates/{tid}')
    def get_template(tid:int,user=Depends(auth)):
        row=template(tid,user)
        for key,default in [('blueprint',{}),('structure',{}),('field_mappings',[]),('knowledge_base_ids',[])]:row[key]=decoded(row[key],default)
        row['blueprint']=blueprints.from_template(s.one('SELECT * FROM report_templates WHERE id=?',(tid,))).model_dump()
        return row

    @app.post('/api/report-templates',status_code=201)
    def save_template(body:TemplateVersion,user=Depends(auth)):
        require(user,'templates.manage');plan=body.blueprint
        if not plan.name.strip() or any(not sec.title.strip() for sec in plan.sections):raise HTTPException(422,'模板名和标题不能为空')
        for kid in body.knowledge_base_ids:check_kb(user,kid)
        structure={}
        if body.import_id:
            if not user['system_admin']:raise HTTPException(403,'仅管理员可维护导入的DOCX')
            imported=s.one('SELECT * FROM template_imports WHERE id=?',(body.import_id,))
            if not imported:raise HTTPException(404,'导入文件不存在')
            data=object_storage.read_bytes(imported)
            if hashlib.sha256(data).hexdigest()!=imported['sha256']:raise HTTPException(409,'模板原件校验失败')
            structure=template_docx.parse_docx(data);blocks={b['id']:b for b in structure['blocks'] if b['part']=='body' and not b['table']}
            ids=[sec.block_id for sec in plan.sections if sec.block_id]
            if len(ids)!=len(set(ids)) or any(bid not in blocks for bid in ids):raise HTTPException(422,'标题来源段落无效或重复')
            for block in blocks.values():block.update(type='paragraph',level=0)
            for sec in plan.sections:
                if sec.block_id:blocks[sec.block_id].update(type='heading',level=sec.level,text=sec.title)
            known={field['key'] for field in structure['fields']}
            if {m.key for m in body.mappings}!=known or len(body.mappings)!=len(known):raise HTTPException(422,'每个占位符必须配置一个来源或明确忽略')
        elif body.mappings:
            if len({m.key for m in body.mappings})!=len(body.mappings):raise HTTPException(422,'字段名称不能重复')
        ids={sec.block_id or f'section-{i}' for i,sec in enumerate(plan.sections)}
        for m in body.mappings:
            if m.target=='section' and m.section_block_id not in ids:raise HTTPException(422,'字段映射指向不存在的章节')
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE');parent=None
            if body.parent_id:
                parent=c.execute('SELECT * FROM report_templates WHERE id=?',(body.parent_id,)).fetchone()
                if not parent:raise HTTPException(404,'父版本不存在')
                if parent['lifecycle']=='deleted':raise HTTPException(409,'父模板已删除，请先从回收站恢复')
            family=(parent['family_id'] or parent['id']) if parent else None
            version=body.version.strip() or ('v'+str(c.execute('SELECT COUNT(*) FROM report_templates WHERE family_id=? OR id=?',(family,family)).fetchone()[0]+1)+'.0' if family else 'v1.0')
            if family and c.execute('SELECT id FROM report_templates WHERE (family_id=? OR id=?) AND version=?',(family,family,version)).fetchone():raise HTTPException(409,'版本号已存在，请使用新版本号')
            tid=c.execute('''INSERT INTO report_templates(name,description,sections,version,created_at,blueprint,import_id,parent_id,structure,field_mappings,knowledge_base_ids,lifecycle,family_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(plan.name,plan.description,json.dumps([sec.title for sec in plan.sections],ensure_ascii=False),version,s.now(),plan.model_dump_json(),body.import_id,body.parent_id,json.dumps(structure,ensure_ascii=False),json.dumps([m.model_dump() for m in body.mappings],ensure_ascii=False),json.dumps(body.knowledge_base_ids),body.lifecycle,family)).lastrowid
        s.audit(user,'保存报告模板版本','报告模板',version,object_id=tid)
        return {'id':tid,'version':version}

    @app.patch('/api/report-templates/{tid}/lifecycle')
    def lifecycle(tid:int,state:Literal['draft','published','disabled'],user=Depends(auth)):
        require(user,'templates.manage');row=template(tid,user)
        s.execute('UPDATE report_templates SET lifecycle=? WHERE id=?',(state,tid));s.audit(user,'变更模板状态','报告模板',state,object_id=tid)
        return {'ok':True}

    @app.delete('/api/report-templates/{tid}')
    def delete_template(tid:int,user=Depends(auth)):
        require(user,'templates.manage')
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            raw=c.execute('SELECT * FROM report_templates WHERE id=?',(tid,)).fetchone()
            if not raw:raise HTTPException(404,'模板不存在')
            row=dict(raw)
            if not template_docx.allowed(row,user,include_deleted=True):raise HTTPException(403,'无权管理此模板')
            if row['lifecycle']=='deleted':return {'ok':True,'message':'此模板版本已在回收站'}
            c.execute("UPDATE report_templates SET lifecycle='deleted' WHERE id=?",(tid,))
        s.audit(user,'删除报告模板版本','报告模板',f"{row['name']} · {row['version']}；移入回收站，保留历史引用",object_id=tid)
        return {'ok':True,'message':'此模板版本已移入回收站，已有报告不受影响'}

    @app.post('/api/report-templates/{tid}/restore')
    def restore_template(tid:int,user=Depends(auth)):
        require(user,'templates.manage')
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            raw=c.execute('SELECT * FROM report_templates WHERE id=?',(tid,)).fetchone()
            if not raw:raise HTTPException(404,'模板不存在')
            row=dict(raw)
            if not template_docx.allowed(row,user,include_deleted=True):raise HTTPException(403,'无权管理此模板')
            if row['lifecycle']!='deleted':raise HTTPException(409,'此模板版本不在回收站')
            c.execute("UPDATE report_templates SET lifecycle='disabled' WHERE id=?",(tid,))
        s.audit(user,'恢复报告模板版本','报告模板',f"{row['name']} · {row['version']}；恢复为停用状态",object_id=tid)
        return {'ok':True,'message':'已恢复为停用状态，发布后才能用于新建报告'}

    @app.post('/api/reports/preflight')
    async def preflight(body:ReportBody,request:Request,user=Depends(auth)):
        check_kb(user,body.knowledge_base_id);row=template(body.template_id,user);template_docx.ensure_template_scope(row,body.knowledge_base_id,user);validate_input(body,row)
        plan=blueprints.from_template(row);results=[]
        for i,spec in enumerate(plan.sections):
            evidence,params=await report_retrieval.retrieve_section(body.knowledge_base_id,spec.model_dump(),purpose=plan.purpose,
                report_title=body.title,requirements=body.requirements,period_start=body.period_start,period_end=body.period_end,
                template_structure=row.get('structure')) if spec.kind not in ('fixed','container') else ([],{})
            results.append({'ordinal':i,'title':spec.title,'kind':spec.kind,'required':spec.required,'evidence':evidence,'evidence_count':len(evidence),'retrieval':params,'ready':bool(evidence) or spec.kind in ('fixed','container')})
        fresh=auth(request.headers.get('authorization'))
        check_kb(fresh,body.knowledge_base_id)
        value={'sections':results,'missing':sum(not sec['ready'] and sec['required'] for sec in results),'warnings':['时间范围按业务语义参与检索与写作，不将文件上传时间当作业务日期；请审阅原文中的日期。'] if body.period_start or body.period_end else []}
        pid=secrets.token_urlsafe(24)
        s.execute('INSERT INTO report_preflights VALUES(?,?,?,?,?)',(pid,user['id'],signature(body,row),json.dumps(value,ensure_ascii=False),s.now()))
        return {**value,'preflight_id':pid}

    @app.get('/api/reports/{tid}')
    def get_report(tid:int,user=Depends(auth)):return detail(tid,user)

    @app.get('/api/reports/{tid}/preview')
    def preview(tid:int,user=Depends(auth)):
        report=canonical(report_task(user,tid));sections=s.rows('SELECT * FROM report_sections WHERE task_id=? ORDER BY ordinal',(tid,));kb=s.one('SELECT name FROM knowledge_bases WHERE id=?',(report['knowledge_base_id'],))
        return {'nodes':report_layout.nodes(report,sections,kb['name'],draft=report['document_state']!='finalized'),'format':blueprints.ReportBlueprint.model_validate_json(report['blueprint']).format.model_dump()}

    @app.patch('/api/reports/{tid}')
    def update(tid:int,body:ReportChange,user=Depends(auth)):
        report_task(user,tid)
        if body.title is not None and not body.title.strip():raise HTTPException(422,'报告名称不能为空')
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=dict(c.execute('SELECT * FROM report_tasks WHERE id=?',(tid,)).fetchone()) if body.document_state=='archived' and body.title is None else editable(c,tid)
            if row['revision']!=body.expected_revision:raise HTTPException(409,'报告已有较新修改，请刷新')
            if body.document_state=='archived' and (row['status'] in ('queued','generating') or c.execute("SELECT id FROM report_sections WHERE task_id=? AND generation_status IN ('queued','running')",(tid,)).fetchone()):raise HTTPException(409,'请先结束生成任务再归档')
            c.execute('UPDATE report_tasks SET title=?,document_state=?,revision=revision+1,updated_at=? WHERE id=?',(body.title or row['title'],body.document_state or row['document_state'],s.now(),tid))
        s.audit(user,'修改报告属性','报告',object_id=tid);return {'ok':True}

    @app.post('/api/reports/{tid}/reopen')
    def reopen(tid:int,body:Revision,user=Depends(auth)):
        report_task(user,tid)
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=dict(c.execute('SELECT * FROM report_tasks WHERE id=?',(tid,)).fetchone())
            if row['revision']!=body.expected_revision:raise HTTPException(409,'报告已更新')
            if row['document_state'] not in ('finalized','archived'):raise HTTPException(409,'报告已处于编辑状态')
            c.execute("UPDATE report_tasks SET document_state='draft',revision=revision+1,updated_at=? WHERE id=?",(s.now(),tid))
        s.audit(user,'创建报告可编辑修订','报告',object_id=tid);return {'ok':True}

    @app.post('/api/reports/{tid}/sections/{sid}/review')
    def review(tid:int,sid:int,body:Revision,user=Depends(auth)):
        report_task(user,tid)
        from .report_drafts import available
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE');editable(c,tid);_,sec=available(c,tid,sid,body.expected_revision)
            c.execute('UPDATE report_sections SET reviewed_revision=? WHERE id=?',(sec['revision'],sid))
            c.execute('UPDATE report_tasks SET revision=revision+1,updated_at=? WHERE id=?',(s.now(),tid))
        v=g.current(sec)
        s.audit(user,'审阅报告章节','报告章节',f"当前修订 {sec['revision']}；核验状态 {v.get('state','legacy')}；人工审阅为最终决定，不改变模型核验结果",object_id=sid);return {'ok':True}

    @app.post('/api/reports/{tid}/finalize',status_code=201)
    def finalize(tid:int,body:Finalize,user=Depends(auth)):
        report_task(user,tid)
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=editable(c,tid)
            if row['revision']!=body.expected_revision:raise HTTPException(409,'报告已更新，请重新审阅')
            sections=[dict(r) for r in c.execute('SELECT * FROM report_sections WHERE task_id=? ORDER BY ordinal',(tid,))];q=quality(row,sections)
            if q['issues']:raise HTTPException(409,{'message':'报告仍有未解决问题，不能定稿','issues':q['issues']})
            if not body.fixed_text_reviewed:raise HTTPException(422,'请确认已人工核对模板固定文字、元数据和业务日期')
            row=canonical(row);kb=c.execute('SELECT name FROM knowledge_bases WHERE id=?',(row['knowledge_base_id'],)).fetchone()
            version=c.execute('SELECT COUNT(*) FROM report_releases WHERE report_id=?',(tid,)).fetchone()[0]+1
            decision={'policy':'human-review-final','user_id':user['id'],'reviewer':user['name'],'decided_at':s.now(),
                      'human_accepted_sections':q['human_accepted_sections'],'warnings':q['warnings'],
                      'reviewed_revisions':[{'section_id':sec['id'],'revision':sec['revision'],'reviewed_revision':sec['reviewed_revision']} for sec in sections]}
            release=c.execute('INSERT INTO report_releases(report_id,version,snapshot,created_at,user_id) VALUES(?,?,?,?,?)',(tid,version,json.dumps(snapshot_report(row,sections,kb['name'],decision),ensure_ascii=False),s.now(),user['id'])).lastrowid
            c.execute("UPDATE report_tasks SET document_state='finalized',final_release_id=?,revision=revision+1,updated_at=? WHERE id=?",(release,s.now(),tid))
        s.audit(user,'报告定稿','报告',f"v{version}；人工最终决定；{q['human_accepted_sections']}节未获证据通过但已审阅放行",object_id=tid);return {'id':release,'version':version}

    @app.get('/api/reports/{tid}/export')
    def export(tid:int,format:Literal['md','docx','pdf']='docx',mode:Literal['draft','final']='draft',release_id:int|None=None,diagnostics:bool=False,user=Depends(auth)):
        report=canonical(report_task(user,tid));sections=s.rows('SELECT * FROM report_sections WHERE task_id=? ORDER BY ordinal',(tid,));kb=s.one('SELECT name FROM knowledge_bases WHERE id=?',(report['knowledge_base_id'],))
        if mode=='final':
            release=s.one('SELECT * FROM report_releases WHERE id=? AND report_id=?',(release_id or report['final_release_id'],tid))
            if not release:raise HTTPException(409,'请先审阅并定稿；未定稿只能下载草稿')
            snap=decoded(release['snapshot']);report=snap['report'];sections=snap['sections'];tree=snap['nodes'];version=f'-v{release["version"]}'
        else:
            if report['status'] in ('queued','generating'):raise HTTPException(409,'请等待本次生成完成或取消，再下载草稿')
            tree=report_layout.nodes(report,sections,kb['name'],draft=True,diagnostics=diagnostics);version='-草稿'
        fmt=blueprints.ReportBlueprint.model_validate(decoded(report['blueprint'])).format
        content=report_layout.docx(tree,fmt) if format=='docx' else report_layout.pdf(tree,fmt) if format=='pdf' else report_layout.markdown(tree).encode('utf-8-sig')
        mime={'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','pdf':'application/pdf','md':'text/markdown; charset=utf-8'}[format]
        s.audit(user,'导出报告','报告',mode+' '+format,object_id=tid)
        return Response(content,media_type=mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(report['title']+version+'.'+format),'Cache-Control':'no-store'})

    @app.post('/api/reports/{tid}/evidence-search')
    async def evidence_search(tid:int,body:Search,user=Depends(auth)):
        report=report_task(user,tid);check_kb(user,report['knowledge_base_id'])
        evidence,meta=await rag.retrieve(body.query,[report['knowledge_base_id']]);report_task(user,tid)
        return {'items':evidence,'retrieval':meta}

    @app.post('/api/reports/{tid}/sections/{sid}/citations')
    def attach(tid:int,sid:int,body:Attach,user=Depends(auth)):
        report=report_task(user,tid);check_kb(user,report['knowledge_base_id'])
        item=s.one('''SELECT c.*,d.name,d.version,d.knowledge_base_id,k.name knowledge_base_name FROM chunks c JOIN documents d ON d.id=c.document_id JOIN knowledge_bases k ON k.id=d.knowledge_base_id WHERE c.id=? AND d.deleted=0 AND d.status='ready' ''',(body.chunk_id,))
        if not item or item['knowledge_base_id']!=report['knowledge_base_id']:raise HTTPException(403,'引用必须来自本报告授权知识库的可用文档')
        from .report_drafts import available,snapshot,rebuild
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE');editable(c,tid);_,sec=available(c,tid,sid,body.expected_revision)
            if sec['confirmed']:raise HTTPException(409,'请先解锁本节')
            cites=decoded(sec['citations'],[])
            for i,e in enumerate(cites,1):
                if e['chunk_id']==body.chunk_id:return {'reference':i,'revision':sec['revision']}
            if len(cites)>=50:raise HTTPException(422,'单节最多50条引用，请删除不再使用的来源')
            item['source']=f"{item['name']} · {item['section']} · "+(f"第{item['page']}页 · " if item['page'] else '')+item['version'];item['chunk_id']=item['id'];item['relevance']='人工选择'
            cites.append(item);snapshot(c,sec,'添加引用',user['id'])
            c.execute('UPDATE report_sections SET citations=?,verification=?,revision=revision+1,reviewed_revision=-1 WHERE id=?',(json.dumps(cites,ensure_ascii=False),json.dumps(g.manual(sec['content'])),sid));rebuild(c,tid,source_lines)
        return {'reference':len(cites),'revision':sec['revision']+1}

    @app.post('/api/reports/{tid}/sections/{sid}/restore/{hid}')
    def restore(tid:int,sid:int,hid:int,body:Revision,user=Depends(auth)):
        report_task(user,tid)
        from .report_drafts import available,snapshot,rebuild
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE');editable(c,tid);_,sec=available(c,tid,sid,body.expected_revision)
            if sec['confirmed']:raise HTTPException(409,'请先解锁本节')
            old=c.execute('SELECT * FROM report_section_history WHERE id=? AND section_id=?',(hid,sid)).fetchone()
            if not old:raise HTTPException(404,'修订不存在')
            snapshot(c,sec,'恢复历史',user['id'])
            c.execute('''UPDATE report_sections SET content=?,citations=?,verification=?,revision=revision+1,edited=1,reviewed_revision=-1 WHERE id=?''',(old['content'],old['citations'],json.dumps(g.manual(old['content'])),sid));rebuild(c,tid,source_lines)
        s.audit(user,'恢复报告章节修订','报告章节',object_id=sid);return {'ok':True}

    @app.post('/api/reports/{tid}/candidates/{cid}')
    def candidate(tid:int,cid:int,body:CandidateAction,user=Depends(auth)):
        report_task(user,tid)
        from .report_drafts import available,snapshot,rebuild
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE');editable(c,tid);cand=c.execute("SELECT * FROM report_candidates WHERE id=? AND report_id=? AND status='pending'",(cid,tid)).fetchone()
            if not cand:raise HTTPException(404,'候选版本不存在或已处理')
            if body.action=='accept':
                _,sec=available(c,tid,cand['section_id'],body.expected_revision)
                if sec['revision']!=cand['expected_revision'] or sec['confirmed']:raise HTTPException(409,'原文已修改或已锁定，不能用旧候选覆盖')
                snapshot(c,sec,'采用候选版本',user['id'],cand['job_id'])
                v=decoded(cand['verification'])
                c.execute("UPDATE report_sections SET content=?,citations=?,verification=?,revision=revision+1,edited=0,reviewed_revision=-1,status=? WHERE id=?",(cand['content'],cand['citations'],cand['verification'],'completed' if v['state']=='verified' else 'needs_review',sec['id']));rebuild(c,tid,source_lines)
            c.execute('UPDATE report_candidates SET status=? WHERE id=?',('accepted' if body.action=='accept' else 'discarded',cid))
        s.audit(user,'采用报告候选' if body.action=='accept' else '放弃报告候选','报告',object_id=tid);return {'ok':True}

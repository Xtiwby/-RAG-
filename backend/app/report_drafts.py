"""Owned report drafts and atomic single-section regeneration, preserving other sections.

Generated replacements are independently evidence-checked before atomic publication.
"""
import json
import re
from fastapi import Depends,HTTPException
from pydantic import Field
from . import store as s,jobs,rag,blueprints,settings,report_grounding,report_retrieval

class DraftUpdate(settings.Strict):
    expected_revision:int=Field(ge=0)
    content:str|None=Field(default=None,max_length=100000)
    confirmed:bool|None=None

class Regenerate(settings.Strict):
    expected_revision:int=Field(ge=0)
    instructions:str=Field(default='',max_length=5000)
    preview:bool=False

def snapshot(c,section,reason,user_id,job_id=None):
    c.execute('''INSERT INTO report_section_history(section_id,revision,content,citations,status,confirmed,edited,reason,user_id,job_id,created_at,verification)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(section['id'],section['revision'],section['content'],section['citations'],section['status'],section['confirmed'],section['edited'],reason,user_id,job_id,s.now(),section['verification']))

def available(c,report_id,section_id,revision=None,busy=True):
    report=c.execute('SELECT * FROM report_tasks WHERE id=?',(report_id,)).fetchone()
    section=c.execute('SELECT * FROM report_sections WHERE id=? AND task_id=?',(section_id,report_id)).fetchone()
    if not report or not section:
        raise HTTPException(404,'报告章节不存在')
    if report['document_state'] in ('finalized','archived'):
        raise HTTPException(409,'报告已定稿或归档，请先创建可编辑修订')
    if c.execute("SELECT id FROM jobs WHERE id=? AND status IN ('queued','running')",(report['job_id'],)).fetchone() or report['status'] in ('queued','generating'):
        raise HTTPException(409,'整份报告仍在生成，请完成或取消后再编辑章节')
    if busy and c.execute("SELECT id FROM jobs WHERE id=? AND status IN ('queued','running')",(section['job_id'],)).fetchone():
        raise HTTPException(409,'本节已有生成任务，请完成或取消后再编辑')
    if revision is not None and section['revision']!=revision:
        raise HTTPException(409,'章节已被修改，请刷新后重试，未覆盖较新草稿')
    return dict(report),dict(section)

def rebuild(c,tid,source_lines):
    report=dict(c.execute('SELECT * FROM report_tasks WHERE id=?',(tid,)).fetchone())
    sections=[dict(row) for row in c.execute('SELECT * FROM report_sections WHERE task_id=? ORDER BY ordinal,id',(tid,))]
    plan=blueprints.ReportBlueprint.model_validate_json(report['blueprint']) if report['blueprint']!='{}' else None
    text=report_grounding.compose(report,sections,source_lines)
    expected=len(plan.sections) if plan else len(sections)
    finished=sum(section['status'] not in ('pending','generating') and bool(section['content'].strip()) for section in sections)
    progress=round(min(1,finished/max(1,expected))*100)
    if any(section['edited'] for section in sections):
        state='draft'
    elif len(sections)<expected or any(section['status']!='completed' for section in sections):
        state='needs_review'
    else:
        state='completed'
    c.execute('UPDATE report_tasks SET content=?,status=?,progress=?,revision=revision+1,updated_at=? WHERE id=?',(text,state,progress,s.now(),tid))

def finish_failed(row,message,status):
    if row['kind']!='report_section':
        return
    payload=json.loads(row['payload'])
    s.execute('''UPDATE report_sections SET generation_status=?,generation_error=?
        WHERE id=? AND task_id=? AND job_id=?''',(status,message,payload['section_id'],payload['report_id'],row['id']))

def retry_state(c,row):
    payload=json.loads(row['payload'])
    _,section=available(c,payload['report_id'],payload['section_id'],payload['expected_revision'])
    if section['confirmed'] or section['job_id']!=row['id']:
        raise HTTPException(409,'本节已确认或已提交新任务，不能重试旧任务；请使用当前章节重新生成')
    c.execute("UPDATE report_sections SET generation_status='queued',generation_error='' WHERE id=?",(section['id'],))

def install(app,auth,require,check_kb,report_task,source_lines):
    def owned(tid,sid,user,active=False):
        report=report_task(user,tid)
        if active:
            check_kb(user,report['knowledge_base_id'])
        section=s.one('SELECT * FROM report_sections WHERE id=? AND task_id=?',(sid,tid))
        if not section:
            raise HTTPException(404,'报告章节不存在')
        return report,section

    @app.patch('/api/report-tasks/{tid}/sections/{sid}')
    def save_draft(tid:int,sid:int,body:DraftUpdate,user=Depends(auth)):
        owned(tid,sid,user)
        if body.content is None and body.confirmed is None:
            raise HTTPException(422,'请提供草稿内容或确认状态')
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            _,section=available(c,tid,sid,body.expected_revision)
            content=section['content'] if body.content is None else body.content
            changed=content!=section['content']
            if changed and section['confirmed']:
                raise HTTPException(409,'本节已确认，请先单独取消确认，再修改正文')
            if body.confirmed and not content.strip():
                raise HTTPException(422,'空白章节不能确认')
            count=len(json.loads(section['citations']))
            if any(int(n)<1 or int(n)>count for n in re.findall(r'\[(\d+)\]',content)):
                raise HTTPException(422,'草稿包含不存在的引用编号，请使用本节已有引用')
            confirmed=section['confirmed'] if body.confirmed is None else int(body.confirmed)
            if not changed and confirmed==section['confirmed']:
                return {'ok':True,'revision':section['revision']}
            snapshot(c,section,'人工编辑' if changed else '确认状态变更',user['id'])
            verification=json.dumps(report_grounding.manual(content),ensure_ascii=False) if changed else section['verification']
            reviewed=section['revision']+1 if not changed and section['reviewed_revision']==section['revision'] else -1
            c.execute('''UPDATE report_sections SET content=?,edited=?,confirmed=?,status=?,revision=revision+1,updated_at=?,verification=?,reviewed_revision=? WHERE id=?''',
                      (content,1 if changed else section['edited'],confirmed,'draft' if changed else section['status'],s.now(),verification,reviewed,sid))
            rebuild(c,tid,source_lines)
        s.audit(user,'保存报告草稿' if changed else '确认报告章节' if confirmed else '取消章节确认','报告章节',f'报告#{tid}；章节修订 {section["revision"]+1}',object_id=sid)
        return {'ok':True,'revision':section['revision']+1}

    @app.get('/api/report-tasks/{tid}/sections/{sid}/history')
    def history(tid:int,sid:int,user=Depends(auth)):
        owned(tid,sid,user)
        return [{**row,'citations':json.loads(row['citations']),'verification':report_grounding.current(row)} for row in s.rows('SELECT * FROM report_section_history WHERE section_id=? ORDER BY id DESC',(sid,))]

    async def enqueue(tid,sid,body,user,mode):
        owned(tid,sid,user,True)
        rag.llm_config()
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            report,section=available(c,tid,sid,body.expected_revision)
            if section['confirmed'] and mode!='verify':
                raise HTTPException(409,'本节已确认，请先取消确认后再重新生成')
            plan=blueprints.ReportBlueprint.model_validate_json(report['blueprint']) if report['blueprint']!='{}' else None
            spec=plan.sections[section['ordinal']].model_dump() if plan and section['ordinal']<len(plan.sections) else {'title':section['title'],'instructions':'','target_words':400}
            payload={'report_id':tid,'section_id':sid,'knowledge_base_id':report['knowledge_base_id'],'expected_revision':section['revision'],
                     'instructions':body.instructions,'title':report['title'],'requirements':report['requirements'],'section':spec,'purpose':plan.purpose if plan else '', 'mode':mode,'preview':body.preview,'workflow_version':report['workflow_version'] or int(body.preview),'period_start':report['period_start'],'period_end':report['period_end'],
                      'template_structure':json.loads(report['template_snapshot'] or '{}').get('structure','')}
            jid=jobs.add('report_section',payload,user,{'report_id':tid,'section_id':sid},c)
            c.execute("UPDATE report_sections SET job_id=?,generation_status='queued',generation_error='' WHERE id=?",(jid,sid))
        s.audit(user,'核验报告章节' if mode=='verify' else '重新生成报告单节','报告章节',f'报告#{tid}；任务#{jid}',object_id=sid)
        await jobs.start()
        return {'job_id':jid,'report_id':tid,'section_id':sid,'status':'queued'}

    @app.post('/api/report-tasks/{tid}/sections/{sid}/regenerate',status_code=202)
    async def regenerate(tid:int,sid:int,body:Regenerate,user=Depends(auth)):
        return await enqueue(tid,sid,body,user,'generate')

    @app.post('/api/report-tasks/{tid}/sections/{sid}/verify',status_code=202)
    async def verify(tid:int,sid:int,body:Regenerate,user=Depends(auth)):
        return await enqueue(tid,sid,body,user,'verify')

    async def handler(row,payload,user):
        tid=payload['report_id'];sid=payload['section_id']
        def fresh():
            current=jobs.job_user(row)
            owned(tid,sid,current,True)
        fresh()
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            _,section=available(c,tid,sid,payload['expected_revision'],busy=False)
            if (section['confirmed'] and payload.get('mode')!='verify') or section['job_id']!=row['id']:
                raise HTTPException(409,'章节已确认或任务已过期，未修改正文')
            c.execute("UPDATE report_sections SET generation_status='running',generation_error='' WHERE id=?",(sid,))
        jobs.emit(row['id'],'section',section_id=sid,task_id=tid,title=payload['section']['title'])
        verification={}
        if payload.get('mode')=='verify':
            content=section['content'];evidence=json.loads(section['citations'])
            if payload.get('workflow_version'):
                kind=payload['section'].get('kind','fact')
                units=[unit for para in content.splitlines() if para.strip() for unit in report_grounding.grounding.sentences(para)]
                if not units or len(units)>128 or len(content)>32000:raise ValueError('核验支持1至128句段、不超过32000字，请拆分章节')
                checks=[]
                for i in range(0,len(units),8):
                    fresh();_,batch=await report_grounding.grounding.check_batch(units[i:i+8],evidence,i+1,claim_kind=kind);fresh();checks.extend(batch)
                verification=report_grounding.record(content,checks,evidence=evidence);verification['publishable']=all(c['status']=='supported' for c in checks)
            else:verification=await report_grounding.verify_existing(content,evidence,fresh)
        else:
            evidence,retrieval=await report_retrieval.retrieve_section(payload['knowledge_base_id'],payload['section'],purpose=payload['purpose'],
                report_title=payload['title'],requirements=payload['requirements'],instructions=payload['instructions'],
                period_start=payload.get('period_start',''),period_end=payload.get('period_end',''),
                 template_structure=payload.get('template_structure'))
            jobs.emit(row['id'],'retrieval',section_id=sid,parameters=retrieval,chunk_ids=[e['chunk_id'] for e in evidence])
            content=''
            hint=report_retrieval.template_section_hint(payload.get('template_structure'),payload['section'].get('block_id'))
            scoped=report_retrieval.scoped_requirements(payload['requirements'],payload['section']['title'],hint)
            focus=f"{payload['section']['title']}。{hint}。{payload['section']['instructions']}。{payload['instructions']}"
            instruction=f"仅重新撰写报告《{payload['title']}》的“{payload['section']['title']}”一节，不输出其他章节。本节定位：{hint}；本章要求：{payload['section']['instructions']}；目标约{payload['section']['target_words']}字；通用要求：{scoped}；本次要求：{payload['instructions']}。只写本节对应题目，每个事实句末必须引用本轮证据，不编造进展。"
            if payload.get('workflow_version'):
                from .report_workflow import generate_section
                content,verification=await generate_section(instruction+f" 业务期间：{payload.get('period_start','')} 至 {payload.get('period_end','')}。",evidence,fresh,payload['section'].get('kind','fact'),coverage_question=focus)
            else:
                async for item in report_grounding.generate(instruction,evidence,fresh):
                    if item['type']=='result':content=item['content'];verification=item['verification']
                    elif item['type']=='verification':jobs.emit(row['id'],'verification',section_id=sid,checks=item['checks'])
        fresh()
        state='completed' if verification['state']=='verified' else 'needs_review'
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            _,section=available(c,tid,sid,payload['expected_revision'],busy=False)
            if (section['confirmed'] and payload.get('mode')!='verify') or section['job_id']!=row['id']:
                raise HTTPException(409,'章节已确认或已有新任务，旧任务结果未覆盖草稿')
            verifying=payload.get('mode')=='verify'
            if payload.get('preview') and not verifying:
                cid=c.execute('INSERT INTO report_candidates(report_id,section_id,expected_revision,content,citations,verification,job_id,created_at) VALUES(?,?,?,?,?,?,?,?)',(tid,sid,section['revision'],content,json.dumps(evidence,ensure_ascii=False),json.dumps(verification,ensure_ascii=False),row['id'],s.now())).lastrowid
                c.execute("UPDATE report_sections SET generation_status='completed',generation_error='' WHERE id=?",(sid,))
                return {'report_id':tid,'section_id':sid,'candidate_id':cid,'status':state}
            snapshot(c,section,'独立事实核验' if verifying else '单节重新生成',user['id'],row['id'])
            c.execute('''UPDATE report_sections SET content=?,citations=?,status=?,edited=?,confirmed=?,revision=revision+1,
                generation_status='completed',generation_error='',updated_at=?,verification=? WHERE id=?''',(content,json.dumps(evidence,ensure_ascii=False),state,section['edited'] if verifying else 0,section['confirmed'] if verifying else 0,s.now(),json.dumps(verification,ensure_ascii=False),sid))
            rebuild(c,tid,source_lines)
        jobs.emit(row['id'],'section_saved',section_id=sid,revision=section['revision']+1)
        s.audit(user,'报告单节生成完成','报告章节',f'报告#{tid}',object_id=sid)
        return {'report_id':tid,'section_id':sid,'status':state}

    jobs.handlers['report_section']=handler

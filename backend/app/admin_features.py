import asyncio
import json
import os
import shutil
import time
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal
import httpx
from fastapi import Depends,HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from pydantic import Field,ValidationError
from . import blueprints,jobs,rag,settings,store as s,telemetry,ingestion,report_grounding,template_docx,report_retrieval,object_storage
from .audit_export import audit_filters,export_logs

class SettingsUpdate(settings.Strict):
    settings: settings.RuntimeSettings
    expected_revision: int = Field(ge=0)

class DocumentTask(settings.Strict):
    document_id: int

class Probe(settings.Strict):
    target: Literal['chat','embedding','reranking']

TERMINAL={'completed','needs_review','failed','interrupted','cancelled'}

def install(app,auth,require,check_kb,document,ReportBody,source_lines):
    def validate_job(kind,payload,user):
        if kind=='report':
            require(user,'reports.generate')
            check_kb(user,payload['knowledge_base_id'])
        elif kind=='report_section':
            require(user,'reports.generate')
            target=s.one('SELECT * FROM report_tasks WHERE id=? AND user_id=?',(payload['report_id'],user['id']))
            if not target or target['knowledge_base_id']!=payload['knowledge_base_id']:
                raise HTTPException(404,'报告不存在或不属于本人')
            check_kb(user,target['knowledge_base_id'])
            section=s.one('SELECT * FROM report_sections WHERE id=? AND task_id=?',(payload['section_id'],target['id']))
            if not section:
                raise HTTPException(404,'报告章节不存在')
        elif kind=='template_design':
            require(user,'templates.manage')
        elif kind in ('reindex','ingest'):
            document(user,payload['document_id'],True)
        else:
            raise HTTPException(422,'未知任务类型')

    def checked_job(jid,user,manage=False):
        row=s.one('SELECT * FROM jobs WHERE id=?',(jid,))
        if not row:
            raise HTTPException(404,'任务不存在')
        point='ops.manage' if manage else 'ops.read'
        if user['system_admin'] or point in user['permissions']:
            return row
        if row['user_id']!=user['id']:
            require(user,point)
        else:
            payload=json.loads(row['payload'])
            if row['kind'] in ('reindex','ingest') and not manage:
                document(user,payload['document_id'])
            else:
                validate_job(row['kind'],payload,user)
        return row

    def public_job(row):
        return {**{k:v for k,v in row.items() if k not in ('payload','config')},'result':json.loads(row['result'])}

    def fresh_job_user(row):
        user=jobs.job_user(row)
        validate_job(row['kind'],json.loads(row['payload']),user)
        return user

    async def design_handler(row,payload,user):
        request=blueprints.DesignRequest.model_validate(payload)
        rag.llm_config()
        jobs.progress(row['id'],15)
        text=''
        async for token in rag.llm_stream(blueprints.design_messages(request)):
            fresh_job_user(row)
            text+=token
        jobs.progress(row['id'],85)
        try:
            plan=blueprints.parse_design(text,request.constraints)
        except (ValidationError,ValueError):
            raise RuntimeError('模型返回的模板格式未通过校验，请调整需求或重试') from None
        return {'blueprint':plan.model_dump()}

    def report_record(body,user,plan,job_id=None,connection=None,template_snapshot=None):
        template_snapshot={**(template_snapshot or {}),'generation_mode':body.generation_mode}
        values=(body.title,body.template_id,body.knowledge_base_id,'queued',0,s.now(),user['id'],plan.model_dump_json(),job_id,body.requirements,body.workflow_version,json.dumps(template_snapshot or {},ensure_ascii=False),json.dumps(body.field_values,ensure_ascii=False),body.period_start,body.period_end)
        sql='''INSERT INTO report_tasks(title,template_id,knowledge_base_id,status,progress,created_at,user_id,blueprint,job_id,requirements,workflow_version,template_snapshot,field_values,period_start,period_end)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'''
        return connection.execute(sql,values).lastrowid if connection is not None else s.execute(sql,values)

    async def report_handler(row,payload,user):
        body=ReportBody.model_validate(payload)
        plan=blueprints.ReportBlueprint.model_validate(payload['blueprint'])
        tid=payload['report_id']
        if row['attempts']>1:
            tid=report_record(body,user,plan,row['id'],template_snapshot=payload.get('template_snapshot'))
        s.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps({'report_id':tid}),row['id']))
        s.execute("UPDATE report_tasks SET status='generating' WHERE id=?",(tid,))
        jobs.emit(row['id'],'meta',task_id=tid,total=len(plan.sections),job_id=row['id'])
        text='# '+body.title+'\n'
        review=False
        for i,section in enumerate(plan.sections):
            fresh_job_user(row)
            if body.workflow_version:
                evidence=payload['preflight']['sections'][i]['evidence']
            else:evidence,_=await report_retrieval.retrieve_section(body.knowledge_base_id,section.model_dump(),mode=body.mode,purpose=plan.purpose,
                report_title=body.title,requirements=body.requirements,period_start=body.period_start,period_end=body.period_end,
                 template_structure=(payload.get('template_snapshot') or {}).get('structure'))
            sid=s.execute('INSERT INTO report_sections(task_id,ordinal,title,status,citations) VALUES(?,?,?,?,?)',
                          (tid,i,section.title,'generating',json.dumps(evidence,ensure_ascii=False)))
            jobs.emit(row['id'],'section',index=i,title=section.title,citations=evidence)
            content='';verification={}
            structure=(payload.get('template_snapshot') or {}).get('structure')
            hint=report_retrieval.template_section_hint(structure,section.block_id)
            scoped=report_retrieval.scoped_requirements(body.requirements,section.title,hint)
            focus=f'{section.title}。{hint}。{section.instructions}'
            instructions=f'仅撰写报告《{body.title}》中的“{section.title}”正文，不重复报告和章节标题。本节定位：{hint}。本章要求：{section.instructions}。目标约 {section.target_words} 字。通用写作要求：{scoped}。只写本节对应的题目，其他章节的重点要求不得写入本节。每个事实句末必须有本轮证据引用，不编造项目进展。'
            if body.workflow_version or body.generation_mode=='synthesis':
                from . import report_workflow
                s.execute('UPDATE report_sections SET stage=? WHERE id=?',('基于证据综合生成' if body.generation_mode=='synthesis' else '生成与独立核验',sid))
                if section.kind in ('fixed','container'):
                    content=section.fixed_content if section.kind=='fixed' else '';verification=report_grounding.record(content,[],'structure',evidence=[])
                else:
                    instructions+=f'报告业务日期范围：{body.period_start} 至 {body.period_end}；只用来源明确支持的该期间事实，不将文档上传时间当作业务日期。业务字段：'+json.dumps(body.field_values,ensure_ascii=False)
                    if body.generation_mode=='synthesis':
                        instructions=f'报告《{body.title}》；本节：{section.title}；本节定位：{hint}；本章要求：{section.instructions}；目标约{section.target_words}字；通用要求：{scoped}；业务期间：{body.period_start} 至 {body.period_end}；业务字段：'+json.dumps(body.field_values,ensure_ascii=False)
                    content,verification=await report_workflow.generate_section(instructions,evidence,lambda:fresh_job_user(row),section.kind,coverage_question=focus,generation_mode=body.generation_mode)
                jobs.emit(row['id'],'verification',section_id=sid,checks=verification['checks'])
                jobs.emit(row['id'],'token',content=content)
            else:
                async for item in report_grounding.generate(instructions,evidence,lambda:fresh_job_user(row)):
                    if item['type']=='token':
                        content+=item['content'];jobs.emit(row['id'],'token',content=item['content'])
                        s.execute('UPDATE report_sections SET content=? WHERE id=?',(content,sid))
                    elif item['type']=='verification':jobs.emit(row['id'],'verification',section_id=sid,checks=item['checks'])
                    elif item['type']=='result':verification=item['verification']
            insufficient=verification['state']!='verified'
            review=review or insufficient
            s.execute("UPDATE report_sections SET content=?,status=?,verification=?,stage='待审阅' WHERE id=?",(content,'needs_review' if insufficient else 'completed',json.dumps(verification,ensure_ascii=False),sid))
            text+=f'\n## {section.title}\n\n{content}\n'
            if plan.format.include_sources:
                text+='\n### 本章来源\n'+source_lines(evidence)+'\n'
            progress=round((i+1)/len(plan.sections)*100)
            s.execute('UPDATE report_tasks SET content=?,progress=? WHERE id=?',(text,progress,tid))
            jobs.progress(row['id'],progress)
            jobs.emit(row['id'],'progress',progress=progress)
        status='needs_review' if review else 'completed'
        text=report_grounding.compose(s.one('SELECT * FROM report_tasks WHERE id=?',(tid,)),s.rows('SELECT * FROM report_sections WHERE task_id=? ORDER BY ordinal',(tid,)),source_lines)
        s.execute('UPDATE report_tasks SET status=?,content=? WHERE id=?',(status,text,tid))
        s.audit(user,'生成报告','报告',body.title,object_id=tid)
        return {'report_id':tid,'task_id':tid,'status':status}

    async def reindex_handler(row,payload,user):
        doc=document(user,payload['document_id'],True)
        units=json.loads(doc['structured'])
        if not units and object_storage.exists(doc):
            async with object_storage.async_local_path(doc) as source:
                units=await asyncio.to_thread(rag.parse_local,source,doc['file_type'])
        if not any(unit['text'].strip() for unit in units):
            raise RuntimeError('此文档尚无可用结构化文本，请先完成解析或视觉转录')
        prepared=rag.prepare_index(units)
        await rag.cache_local_embeddings([chunk[3] for chunk in prepared['chunks']])
        fresh_job_user(row)
        with s.connect() as c:
            count=c.execute('SELECT COUNT(*) FROM documents WHERE name=? AND knowledge_base_id=?',(doc['name'],doc['knowledge_base_id'])).fetchone()[0]
            did=c.execute('''INSERT INTO documents(knowledge_base_id,name,file_type,modality,size,status,version,page_count,content,path,created_at,sha256,storage_backend,storage_bucket,object_key,object_version_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(doc['knowledge_base_id'],doc['name'],doc['file_type'],doc['modality'],doc['size'],'processing',f'v{count+1}.0',doc['page_count'],'',doc['path'],s.now(),doc['sha256'],doc['storage_backend'],doc['storage_bucket'],doc['object_key'],doc['object_version_id'])).lastrowid
        rag.commit_index(did,prepared)
        return {'document_id':did,'previous_document_id':doc['id'],'chunk':settings.get().chunk.model_dump()}

    async def ingest_handler(row,payload,user):
        return await ingestion.run(row,payload,user,fresh_job_user)

    jobs.handlers.update(template_design=design_handler,report=report_handler,reindex=reindex_handler,ingest=ingest_handler)
    jobs.validator=validate_job

    async def enqueue_report(body,user):
        require(user,'reports.generate');check_kb(user,body.knowledge_base_id)
        template=s.one('SELECT * FROM report_templates WHERE id=?',(body.template_id,))
        if not template:
            raise HTTPException(404,'模板不存在')
        template_docx.ensure_template_scope(template,body.knowledge_base_id,user)
        rag.llm_config()
        plan=blueprints.from_template(template)
        snapshot={};preflight={}
        if body.workflow_version:
            from . import report_workflow
            preflight=report_workflow.use_preflight(body,template,user)
            snapshot=report_workflow.template_snapshot(template)
        snapshot['generation_mode']=body.generation_mode
        with s.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=dict(c.execute('SELECT * FROM report_templates WHERE id=?',(body.template_id,)).fetchone())
            template_docx.ensure_template_scope(current,body.knowledge_base_id,user)
            if body.workflow_version:report_workflow.validate_input(body,current)
            tid=report_record(body,user,plan,connection=c,template_snapshot=snapshot)
            payload={**body.model_dump(),'report_id':tid,'blueprint':plan.model_dump(),'template_snapshot':snapshot,'preflight':preflight}
            jid=jobs.add('report',payload,user,{'report_id':tid},c)
            c.execute('UPDATE report_tasks SET job_id=? WHERE id=?',(jid,tid))
        await jobs.start()
        return {'id':tid,'job_id':jid,'status':'queued'}

    @app.post('/api/report-tasks',status_code=201)
    async def create_report(body:ReportBody,user=Depends(auth)):
        return await enqueue_report(body,user)

    @app.post('/api/report-tasks/stream')
    async def report_stream(body:ReportBody,request:Request,user=Depends(auth)):
        result=await enqueue_report(body,user)
        jid=result['job_id']
        async def events():
            after=0
            while True:
                checked_job(jid,user)
                rows=s.rows('SELECT * FROM job_events WHERE job_id=? AND id>? ORDER BY id',(jid,after))
                for row in rows:
                    after=row['id']
                    yield 'data: '+row['data']+'\n\n'
                row=s.one('SELECT status FROM jobs WHERE id=?',(jid,))
                if row['status'] in TERMINAL:
                    # A terminal transaction may have committed after the event SELECT; drain the final event first.
                    if s.one('SELECT id FROM job_events WHERE job_id=? AND id>?',(jid,after)):
                        continue
                    break
                if await request.is_disconnected():
                    break  # the persistent job continues; explicit cancel is available in the queue
                await asyncio.sleep(.1)
        return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    @app.post('/api/templates/design',status_code=202)
    async def design_template(body:blueprints.DesignRequest,user=Depends(auth)):
        require(user,'templates.manage');rag.llm_config()
        jid=jobs.add('template_design',body.model_dump(),user)
        await jobs.start()
        s.audit(user,'设计模板','报告模板',object_id=jid)
        return {'job_id':jid,'status':'queued'}

    @app.post('/api/tasks/reindex',status_code=202)
    async def reindex(body:DocumentTask,user=Depends(auth)):
        document(user,body.document_id,True)
        jid=jobs.add('reindex',body.model_dump(),user)
        await jobs.start()
        return {'job_id':jid,'status':'queued'}

    @app.get('/api/tasks')
    def list_jobs(status:Literal['queued','running','completed','needs_review','failed','cancelled','interrupted']|None=None,
                  kind:Literal['report','report_section','template_design','reindex','ingest']|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),user=Depends(auth)):
        global_view=user['system_admin'] or 'ops.read' in user['permissions']
        clauses=[];params=[]
        if not global_view:
            clauses.append('user_id=?');params.append(user['id'])
        if status:
            clauses.append('status=?');params.append(status)
        if kind:
            clauses.append('kind=?');params.append(kind)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        total=s.one('SELECT COUNT(*) n FROM jobs'+where,params)['n']
        items=s.rows('SELECT * FROM jobs'+where+' ORDER BY id DESC LIMIT ? OFFSET ?',params+[page_size,(page-1)*page_size])
        if not global_view:
            # Do not expose results whose KB scope was revoked since enqueueing.
            permitted=[]
            for row in items:
                try:
                    validate_job(row['kind'],json.loads(row['payload']),user)
                    permitted.append(row)
                except HTTPException:
                    pass
            items=permitted
        return {'items':[public_job(row) for row in items],'total':total,'page':page,'page_size':page_size}

    @app.get('/api/tasks/{jid}')
    def get_job(jid:int,user=Depends(auth)):
        row=checked_job(jid,user)
        value=public_job(row)
        if user['system_admin'] or 'ops.read' in user['permissions']:
            value['configuration']=json.loads(row['config'])
        return value

    @app.get('/api/tasks/{jid}/events')
    def task_events(jid:int,after:int=Query(0,ge=0),user=Depends(auth)):
        checked_job(jid,user)
        return [{'id':r['id'],**json.loads(r['data'])} for r in s.rows('SELECT id,data FROM job_events WHERE job_id=? AND id>? ORDER BY id LIMIT 500',(jid,after))]

    @app.post('/api/tasks/{jid}/cancel')
    def cancel_job(jid:int,user=Depends(auth)):
        checked_job(jid,user,True)
        jobs.cancel(jid)
        s.audit(user,'取消任务','任务',object_id=jid)
        return {'ok':True}

    @app.post('/api/tasks/{jid}/retry',status_code=202)
    async def retry_job(jid:int,user=Depends(auth)):
        row=checked_job(jid,user,True)
        # Re-check the original owner's current permissions; an admin cannot resurrect revoked data access.
        validate_job(row['kind'],json.loads(row['payload']),jobs.job_user(row))
        jobs.retry(jid,user)
        await jobs.start()
        return {'job_id':jid,'status':'queued'}

    @app.get('/api/settings')
    def get_settings(user=Depends(auth)):
        require(user,'settings.manage')
        return settings.read()

    @app.put('/api/settings')
    def save_settings(body:SettingsUpdate,user=Depends(auth)):
        require(user,'settings.manage')
        return settings.save(body.settings,body.expected_revision,user)

    @app.post('/api/operations/probe')
    async def probe(body:Probe,user=Depends(auth)):
        require(user,'settings.manage')
        config=settings.get()
        if body.target=='reranking':
            if config.reranking.provider!='local':
                return {'status':'local','message':'当前为轻量规则重排，未使用模型','checked_at':s.now()}
            from . import local_models
            values,meta=await local_models.rerank(config.reranking,'什么动物喜欢竹子？',['熊猫以竹子为主要食物。','这是一份网络配置手册。'])
            return {'status':'success','message':f'重排模型真实推理成功（{meta["device"]}），返回 {len(values)} 个得分','details':meta,'checked_at':s.now()}
        if body.target=='embedding' and config.embedding.provider=='local':
            from . import local_models
            values=await local_models.embed(config.embedding,['本地语义模型健康检查'])
            return {'status':'success','message':f'本地语义模型真实推理成功，向量维度 {len(values[0])}','checked_at':s.now()}
        if body.target=='embedding' and config.embedding.provider=='tfidf':
            return {'status':'local','message':'本地 TF-IDF 无外部模型服务','checked_at':s.now()}
        if body.target=='chat':
            key,base,model=rag.llm_config()
        else:
            key=os.getenv('EMBEDDING_API_KEY','');base=config.embedding.base_url;model=config.embedding.model
        call=telemetry.begin('probe_'+body.target,model)
        code=None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                headers={'Authorization':'Bearer '+key} if key else {}
                if body.target=='chat':
                    response=await client.get(base+'/models',headers=headers)
                    code=response.status_code;response.raise_for_status()
                    available=model in [r['id'] for r in response.json().get('data',[])]
                    if not available:
                        raise RuntimeError('服务可连接，但模型列表中未找到配置的模型')
                else:
                    response=await client.post(base+'/embeddings',headers=headers,json={'model':model,'input':['health check']})
                    code=response.status_code;response.raise_for_status()
                    if not response.json().get('data',[{}])[0].get('embedding'):
                        raise RuntimeError('Embedding 响应不含向量')
            telemetry.finish(call,'success',status_code=code)
            return {'status':'success','message':'服务连通且响应有效','checked_at':s.now()}
        except Exception as error:
            message=str(error) if isinstance(error,RuntimeError) else f'服务探测失败（HTTP {code}）' if code else '服务不可连接或超时'
            telemetry.finish(call,'failure',message,status_code=code)
            raise HTTPException(502,message) from None

    @app.get('/api/operations')
    def operations(user=Depends(auth)):
        require(user,'ops.read')
        counts={r['status']:r['n'] for r in s.rows('SELECT status,COUNT(*) n FROM jobs GROUP BY status')}
        disk=shutil.disk_usage(s.DATA)
        files=[p for p in s.UPLOADS.rglob('*') if p.is_file()]
        originals=s.rows("SELECT * FROM documents WHERE path<>'' OR object_key<>''")
        originals+=s.rows("SELECT * FROM template_imports WHERE path<>'' OR object_key<>''")
        db_path=s.DATA/'rag_knowledge.db'
        postgres=bool(os.getenv('RAG_DATABASE_URL'))
        database_bytes=s.one('SELECT pg_database_size(current_database()) n')['n'] if postgres else db_path.stat().st_size if db_path.exists() else None
        object_references=s.one("SELECT COUNT(*) n FROM documents WHERE storage_backend='s3'")['n']
        object_references+=s.one("SELECT COUNT(*) n FROM template_imports WHERE storage_backend='s3'")['n']
        storage={'disk_total_bytes':disk.total,'disk_free_bytes':disk.free,'original_backend':object_storage.backend(),
                 'object_references':object_references,
                 'uploads_bytes':sum(p.stat().st_size for p in files),
                 'upload_files':len(files),'database_bytes':database_bytes,
                 'missing_originals':sum(not object_storage.exists(row) for row in originals),
                 'documents':s.one('SELECT COUNT(*) n FROM documents')['n'],'chunks':s.one('SELECT COUNT(*) n FROM chunks')['n']}
        metrics=s.one("""SELECT COUNT(*) total,SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) succeeded,SUM(CASE WHEN status='failure' THEN 1 ELSE 0 END) failed,
            SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) running,ROUND(AVG(CASE WHEN status='success' THEN latency_ms END)) average_latency_ms,
            SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens FROM model_calls""")
        from . import local_models
        worker=s.one('SELECT * FROM worker_heartbeats WHERE id=1')
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(worker['seen_at'])).total_seconds() if worker else None
        return {'checked_at':s.now(),'database':'ok','database_backend':'postgresql' if os.getenv('RAG_DATABASE_URL') else 'sqlite','storage':storage,'local_models':local_models.status(),'queue':{'counts':counts,
            'worker_alive':bool(age is not None and age<5),'mode':os.getenv('RAG_WORKER_MODE','embedded'),
            'active_jobs':[r['id'] for r in s.rows("SELECT id FROM jobs WHERE status='running'")],'concurrency':settings.get().queue.concurrency,'heartbeat_age_seconds':round(age,2) if age is not None else None},
            'model_calls':metrics,'recent_calls':s.rows('SELECT * FROM model_calls ORDER BY id DESC LIMIT 30'),
            'failed_tasks':[public_job(row) for row in s.rows("SELECT * FROM jobs WHERE status IN ('failed','interrupted') ORDER BY id DESC LIMIT 20")]}

    @app.get('/api/audits')
    def audits(filters=Depends(audit_filters),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),user=Depends(auth)):
        require(user,'audit.read')
        where,params,_=filters
        total=s.one('SELECT COUNT(*) n FROM audit_logs'+where,params)['n']
        rows=s.rows('SELECT * FROM audit_logs'+where+' ORDER BY id DESC LIMIT ? OFFSET ?',params+[page_size,(page-1)*page_size])
        return {'items':rows,'total':total,'page':page,'page_size':page_size}

    @app.get('/api/audits/export')
    def audit_export(format:Literal['csv','json']='csv',filters=Depends(audit_filters),user=Depends(auth)):
        require(user,'audit.read')
        return export_logs(filters,format,user)

    @app.get('/api/audits/options')
    def audit_options(user=Depends(auth)):
        require(user,'audit.read')
        return {'users':s.rows('SELECT id,username,name FROM users ORDER BY id'),
                'actions':[r['action'] for r in s.rows('SELECT DISTINCT action FROM audit_logs ORDER BY action')],
                'resources':[r['resource'] for r in s.rows('SELECT DISTINCT resource FROM audit_logs ORDER BY resource')]}

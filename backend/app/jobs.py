"""Durable SQLite queue for a single local API process, with atomic claiming and cancellation."""
import asyncio
import json
import time
import os
import socket
from . import settings,store as s,telemetry

handlers={}
validator=None
supervisor=None
active={}
heartbeat=None

def add(kind,payload,user,result=None,connection=None,*,config=None):
    if config is None:
        config=settings.get()
    values=(kind,user['id'],json.dumps(payload,ensure_ascii=False),config.model_dump_json(),config.queue.max_attempts,json.dumps(result or {}),s.now())
    sql='INSERT INTO jobs(kind,user_id,payload,config,max_attempts,result,created_at) VALUES(?,?,?,?,?,?,?)'
    return connection.execute(sql,values).lastrowid if connection is not None else s.execute(sql,values)

def emit(jid,kind,**value):
    s.execute('INSERT INTO job_events(job_id,data,created_at) VALUES(?,?,?)',(jid,json.dumps({'type':kind,**value},ensure_ascii=False),s.now()))

def progress(jid,value):
    s.execute('UPDATE jobs SET progress=? WHERE id=?',(value,jid))

def claim():
    with s.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM jobs WHERE status='queued' AND cancel_requested=0 ORDER BY id LIMIT 1" +
                      (' FOR UPDATE SKIP LOCKED' if os.getenv('RAG_DATABASE_URL') else '')).fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status='running',attempts=attempts+1,started_at=?,finished_at=NULL WHERE id=?",(s.now(),row['id']))
        return dict(c.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())

def job_user(row):
    user=s.one('SELECT * FROM users WHERE id=?',(row['user_id'],))
    if not user or user['status']!='active':
        raise RuntimeError('任务所属账户不存在或已停用')
    return s.public_user(user)

async def execute(row):
    jid=row['id']
    tokens=[(settings.active,settings.active.set(settings.RuntimeSettings.model_validate_json(row['config']))),
            (telemetry.actor_id,telemetry.actor_id.set(row['user_id'])),(telemetry.job_id,telemetry.job_id.set(jid))]
    try:
        user=job_user(row)
        payload=json.loads(row['payload'])
        if validator:
            validator(row['kind'],payload,user)
        result=await handlers[row['kind']](row,payload,user)
        status='needs_review' if result.get('status')=='needs_review' else 'completed'
        s.execute('UPDATE jobs SET status=?,progress=100,result=?,finished_at=?,error=? WHERE id=?',
                  (status,json.dumps(result,ensure_ascii=False),s.now(),'',jid))
        emit(jid,'done',**result)
        s.audit(user,'任务完成','任务',row['kind'],object_id=jid)
    except asyncio.CancelledError:
        current=s.one('SELECT cancel_requested FROM jobs WHERE id=?',(jid,))
        status='cancelled' if current and current['cancel_requested'] else 'interrupted'
        message='管理员或用户取消了任务' if status=='cancelled' else '服务停止导致任务中断，可重试'
        s.execute('UPDATE jobs SET status=?,error=?,finished_at=? WHERE id=?',(status,message,s.now(),jid))
        emit(jid,'error',message=message)
        from . import ingestion
        ingestion.interrupted_job(row,message,status)
        from . import report_drafts
        report_drafts.finish_failed(row,message,status)
        if row['kind']=='report':
            s.execute("UPDATE report_tasks SET status=?,error=? WHERE job_id=? AND status IN ('queued','generating')",(status,message,jid))
            s.execute("UPDATE report_sections SET status=? WHERE status='generating' AND task_id IN (SELECT id FROM report_tasks WHERE job_id=?)",(status,jid))
    except Exception as error:
        # Do not persist HTTP request bodies, credentials or upstream raw error payloads.
        message=getattr(error,'detail',None) or (str(error) if isinstance(error,(RuntimeError,ValueError)) else '任务执行失败：'+type(error).__name__)
        message=str(message)[:800]
        s.execute("UPDATE jobs SET status='failed',error=?,finished_at=? WHERE id=?",(message,s.now(),jid))
        emit(jid,'error',message=message)
        from . import ingestion
        ingestion.interrupted_job(row,message,'failed')
        from . import report_drafts
        report_drafts.finish_failed(row,message,'failed')
        if row['kind']=='report':
            s.execute("UPDATE report_tasks SET status='failed',error=? WHERE job_id=?",(message,jid))
            s.execute("UPDATE report_sections SET status='failed' WHERE status='generating' AND task_id IN (SELECT id FROM report_tasks WHERE job_id=?)",(jid,))
        owner=s.one('SELECT id,username,name FROM users WHERE id=?',(row['user_id'],)) or {'id':row['user_id'],'name':'任务工作进程'}
        s.audit(owner,'任务失败','任务',message,result='failure',object_id=jid)
    finally:
        for variable,token in reversed(tokens):
            variable.reset(token)

async def run():
    global heartbeat
    worker_id=f'{socket.gethostname()}:{os.getpid()}'
    last_heartbeat=0
    settings.active.set(None)
    try:
        while True:
            heartbeat=time.monotonic()
            if heartbeat-last_heartbeat>=1:
                s.execute('INSERT INTO worker_heartbeats(id,worker_id,seen_at) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET worker_id=excluded.worker_id,seen_at=excluded.seen_at',
                          (worker_id,s.now()))
                last_heartbeat=heartbeat
            for jid in list(active):
                if s.one('SELECT cancel_requested FROM jobs WHERE id=?',(jid,))['cancel_requested']:
                    active[jid].cancel()
                if active[jid].done():
                    active.pop(jid)
            limit=settings.get().queue.concurrency
            while len(active)<limit:
                row=claim()
                if not row:
                    break
                active[row['id']]=asyncio.create_task(execute(row))
            await asyncio.sleep(.2)
    finally:
        running=list(active.values())
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running,return_exceptions=True)
        active.clear()
        heartbeat=None

async def start():
    global supervisor
    if os.getenv('RAG_WORKER_MODE','embedded')=='external' and os.getenv('RAG_WORKER_PROCESS')!='1':
        return
    if supervisor is None or supervisor.done():
        # A queued job survives restart; a half-finished running attempt does not pretend to have completed.
        s.execute("UPDATE jobs SET status='interrupted',error='服务重启导致任务中断，可重试',finished_at=? WHERE status='running'",(s.now(),))
        s.execute("UPDATE model_calls SET status='interrupted',error='服务重启导致调用中断',finished_at=? WHERE status='running'" +
                  (" AND job_id IS NOT NULL" if os.getenv('RAG_WORKER_MODE')=='external' else ''),(s.now(),))
        from . import ingestion
        for row in s.rows("SELECT j.* FROM jobs j JOIN documents d ON d.job_id=j.id WHERE j.kind='ingest' AND j.status IN ('failed','interrupted','cancelled') AND d.status='processing'"):
            ingestion.interrupted_job(row,row['error'] or '服务重启，任务可手动重试',row['status'])
        from . import report_drafts
        for row in s.rows("SELECT * FROM jobs WHERE kind='report_section' AND status IN ('failed','interrupted','cancelled')"):
            report_drafts.finish_failed(row,row['error'],row['status'])
        s.execute("UPDATE report_tasks SET status='interrupted',error='工作进程重启导致生成中断' WHERE status='generating' AND job_id IN (SELECT id FROM jobs WHERE status='interrupted')")
        s.execute("UPDATE report_sections SET status='interrupted' WHERE status='generating' AND task_id IN (SELECT id FROM report_tasks WHERE status='interrupted')")
        supervisor=asyncio.create_task(run())

async def stop():
    global supervisor
    if supervisor and not supervisor.done():
        supervisor.cancel()
        await asyncio.gather(supervisor,return_exceptions=True)
    supervisor=None

def cancel(jid):
    row=s.one('SELECT * FROM jobs WHERE id=?',(jid,))
    if row['status'] not in ('queued','running'):
        from fastapi import HTTPException
        raise HTTPException(409,'只有排队或执行中的任务可取消')
    s.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?',(jid,))
    if row['status']=='queued':
        s.execute("UPDATE jobs SET status='cancelled',finished_at=?,error='任务在执行前被取消' WHERE id=?",(s.now(),jid))
        if row['kind']=='report':
            s.execute("UPDATE report_tasks SET status='cancelled',error='任务在执行前被取消' WHERE job_id=?",(jid,))
        if row['kind']=='ingest':
            from . import ingestion
            did=json.loads(row['payload'])['document_id']
            ingestion.record(did,'parse','cancelled',0,'任务在执行前被取消',jid,row['attempts'])
            s.execute("UPDATE documents SET status='processing_failed',parse_error='处理已取消，可重试' WHERE id=?",(did,))
        emit(jid,'error',message='任务在执行前被取消')
        from . import report_drafts
        report_drafts.finish_failed(row,'任务在执行前被取消','cancelled')
    if jid in active:
        active[jid].cancel()

def retry(jid,user):
    from fastapi import HTTPException
    with s.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        if row['status'] not in ('failed','interrupted','cancelled'):
            raise HTTPException(409,'仅失败、中断或取消的任务可重试')
        if row['attempts']>=row['max_attempts']:
            raise HTTPException(409,'已达到此任务的最大尝试次数，请创建新任务')
        if row['kind']=='ingest':
            doc=c.execute('SELECT job_id FROM documents WHERE id=?',(json.loads(row['payload'])['document_id'],)).fetchone()
            if not doc or doc['job_id']!=jid:
                raise HTTPException(409,'文档已有更新的处理任务，请在文档进度中操作')
        if row['kind']=='report_section':
            from . import report_drafts
            report_drafts.retry_state(c,row)
        c.execute("UPDATE jobs SET status='queued',progress=0,error='',cancel_requested=0,started_at=NULL,finished_at=NULL WHERE id=?",(jid,))
        # Events from previous attempts remain as history, while a new report version is created by its handler.
        c.execute('INSERT INTO job_events(job_id,data,created_at) VALUES(?,?,?)',(jid,json.dumps({'type':'retry','attempt':row['attempts']+1}),s.now()))
    s.audit(user,'重试任务','任务',object_id=jid)

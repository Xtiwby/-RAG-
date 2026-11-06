"""Durable per-document upload/parse/chunk/index events; workers never mutate a ready version."""
import asyncio
import json
import threading
from zipfile import BadZipFile
from pathlib import Path
from . import store as s,rag,jobs,object_storage

STAGES=('upload','parse','chunk','index')

def create_document(kid,name,suffix,modality,path):
    modality='image' if suffix in {'png','jpg','jpeg','webp'} else 'table' if suffix in {'xlsx','csv'} else modality
    with s.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        count=c.execute('SELECT COUNT(*) FROM documents WHERE knowledge_base_id=? AND name=?',(kid,name)).fetchone()[0]
        return c.execute('''INSERT INTO documents(knowledge_base_id,name,file_type,modality,path,status,version,created_at)
            VALUES(?,?,?,?,?,'uploading',?,?)''',(kid,name,suffix,modality,path,f'v{count+1}.0',s.now())).lastrowid

def record(did,stage,status,progress,message='',job_id=None,attempt=0):
    overall=25 if status=='pending' else min(100,STAGES.index(stage)*25+round(progress/4))
    with s.connect() as c:
        c.execute('INSERT INTO document_events(document_id,job_id,attempt,stage,status,progress,overall_progress,message,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                  (did,job_id,attempt,stage,status,progress,overall,message,s.now()))
        c.execute('UPDATE documents SET processing_stage=?,processing_progress=? WHERE id=?',(stage,overall,did))
        if job_id:
            c.execute('UPDATE jobs SET progress=? WHERE id=?',(overall,job_id))
    if job_id:
        jobs.emit(job_id,'document_stage',document_id=did,stage=stage,status=status,progress=progress,overall_progress=overall,message=message)

def details(doc):
    events=s.rows('SELECT * FROM document_events WHERE document_id=? ORDER BY id',(doc['id'],))
    stages=[]
    for stage in STAGES:
        event=next((e for e in reversed(events) if e['stage']==stage),None)
        stages.append(event or {'stage':stage,'status':'pending','progress':0,'message':''})
    job=s.one('SELECT id,status,attempts,max_attempts,error FROM jobs WHERE id=?',(doc['job_id'],)) if doc.get('job_id') else None
    return {'document_id':doc['id'],'status':doc['status'],'progress':doc['processing_progress'],
            'stages':stages,'events':events,'job':job,'error':doc['parse_error']}

def interrupted_job(row,message,status='failed'):
    if row['kind']!='ingest':
        return
    did=json.loads(row['payload'])['document_id']
    doc=s.one('SELECT * FROM documents WHERE id=?',(did,))
    if not doc or doc['status'] not in ('processing','uploading'):
        return
    stage=doc['processing_stage'] if doc['processing_stage'] in STAGES else 'parse'
    record(did,stage,status,0,message,row['id'],row['attempts'])
    s.execute("UPDATE documents SET status='processing_failed',parse_error=? WHERE id=?",(message,did))

async def run(row,payload,user,validate):
    did=payload['document_id'];stage='parse';progress=0
    stopped=threading.Event();callback_lock=threading.Lock()
    doc=s.one('SELECT * FROM documents WHERE id=?',(did,))
    if doc['status']=='ready':
        return {'document_id':did,'already_indexed':True}
    def track(name,status,value,message):
        nonlocal stage,progress
        stage=name;progress=value
        record(did,name,status,value,message,row['id'],row['attempts'])
    def measured(name,label):
        last=-1
        def update(done,total):
            nonlocal last
            with callback_lock:
                if stopped.is_set():
                    return
                value=min(99,round(done/max(1,total)*100))
                if value!=last:
                    track(name,'running',value,f'{label} {done} / {total} 个单元')
                    last=value
        return update
    try:
        validate(row)
        s.execute("UPDATE documents SET status='processing',parse_error='' WHERE id=?",(did,))
        if row['attempts']>1:
            # New attempt resets pending downstream stages without erasing historical events.
            for name in ('chunk','index'):
                record(did,name,'pending',0,'等待本次解析',row['id'],row['attempts'])
        track('parse','running',0,'正在解析原文件')
        async with object_storage.async_local_path(doc) as source:
            units=await asyncio.to_thread(rag.parse_local,source,doc['file_type'],measured('parse','已解析'))
        validate(row)
        if not any(unit['text'].strip() for unit in units):
            track('parse','needs_extraction',100,'未提取到文本；图片需视觉解析，扫描PDF需先转图片')
            s.execute("UPDATE documents SET status='needs_extraction',parse_error=? WHERE id=?",('没有可索引文本，请完成视觉解析',did))
            return {'document_id':did,'status':'needs_review'}
        track('parse','completed',100,f'已解析 {len(units)} 个文本单元')
        track('chunk','running',0,'按任务提交时的长度和重叠参数切分')
        prepared=await asyncio.to_thread(rag.prepare_index,units,measured('chunk','已切分'))
        validate(row)
        track('chunk','completed',100,f"已建立 {len(prepared['chunks'])} 个待索引片段")
        track('index','running',0,'事务写入片段索引、结构化文本与哈希')
        await rag.cache_local_embeddings([chunk[3] for chunk in prepared['chunks']])
        validate(row)
        # Short atomic commit: no await and no partially visible ready index.
        rag.commit_index(did,prepared)
        track('index','completed',100,'索引提交完成，可进入检索')
        s.audit(user,'文档索引完成','文档',object_id=did)
        return {'document_id':did}
    except BaseException as error:
        state='interrupted' if isinstance(error,asyncio.CancelledError) else 'failed'
        message=('文档处理已中断，可重试' if state=='interrupted' else
                 '文件内容不是有效的 Office 文档压缩包，请确认文件未损坏且扩展名正确后重新上传' if isinstance(error,BadZipFile) else
                 getattr(error,'detail',None) or (str(error) if isinstance(error,(ValueError,RuntimeError)) else '文件解析或索引失败，请检查文件内容'))
        with callback_lock:
            stopped.set()
            s.execute('UPDATE documents SET status=?,parse_error=? WHERE id=?',('parse_failed' if stage=='parse' else 'processing_failed',message[:800],did))
            track(stage,state,progress,message[:800])
        raise
    finally:
        stopped.set()

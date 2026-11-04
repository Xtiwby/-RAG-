import time
from contextvars import ContextVar
from . import store as s

actor_id=ContextVar('model_actor_id',default=None)
job_id=ContextVar('model_job_id',default=None)
call_kind=ContextVar('model_call_kind',default='chat')

def begin(kind,model):
    cid=s.execute('INSERT INTO model_calls(kind,model,user_id,job_id,status,started_at) VALUES(?,?,?,?,?,?)',
                  (kind,model,actor_id.get(),job_id.get(),'running',s.now()))
    return cid,time.perf_counter()

def finish(call,status,error='',status_code=None,usage=None,characters=0):
    cid,started=call
    usage=usage or {}
    s.execute('''UPDATE model_calls SET status=?,error=?,status_code=?,finished_at=?,latency_ms=?,input_tokens=?,output_tokens=?,output_characters=? WHERE id=?''',
              (status,error,status_code,s.now(),round((time.perf_counter()-started)*1000),usage.get('prompt_tokens'),usage.get('completion_tokens'),characters,cid))

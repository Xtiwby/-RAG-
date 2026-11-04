"""One filter implementation for audit queries and bounded, formula-safe exports."""
import csv
import io
import json
from datetime import datetime,timezone
from typing import Literal
from fastapi import HTTPException,Query
from fastapi.responses import Response
from . import store as s

EXPORT_LIMIT=50000

def audit_filters(user_id:int|None=None,actor:str=Query('',max_length=100),resource:str=Query('',max_length=100),
                  object_id:str=Query('',max_length=100),action:str=Query('',max_length=100),
                  result:Literal['success','failure','denied','unknown']|None=None,start:str|None=None,end:str|None=None):
    clauses=[];params=[]
    values={'user_id':user_id,'actor':actor,'resource':resource,'object_id':object_id,'action':action,'result':result,'start':start,'end':end}
    if user_id is not None:
        clauses.append('user_id=?');params.append(user_id)
    for name,value in [('resource',resource),('object_id',object_id),('action',action),('result',result)]:
        if value:
            clauses.append(name+'=?');params.append(value)
    if actor:
        clauses.append('(instr(actor,?)>0 OR instr(username,?)>0)');params.extend([actor,actor])
    times=[]
    for name,value,operator in [('start',start,'>='),('end',end,'<=')]:
        if value:
            try:
                parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError()
            except ValueError:
                raise HTTPException(422,'时间必须是包含时区的 ISO 日期时间') from None
            iso=parsed.astimezone(timezone.utc).isoformat();times.append(parsed)
            values[name]=iso
            clauses.append('julianday(created_at)'+operator+'julianday(?)');params.append(iso)
    if len(times)==2 and times[0]>times[1]:
        raise HTTPException(422,'开始时间不能晚于结束时间')
    return (' WHERE '+' AND '.join(clauses) if clauses else ''),params,{key:value for key,value in values.items() if value is not None and value!=''}

def safe_cell(value):
    value='' if value is None else str(value)
    if value.lstrip('\ufeff \t\r\n').startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')):
        return "'"+value
    return value

def export_logs(filters,format,user):
    where,params,selected=filters
    with s.connect() as c:
        c.execute('BEGIN')
        count=c.execute('SELECT COUNT(*) FROM audit_logs'+where,params).fetchone()[0]
        if count>EXPORT_LIMIT:
            raise HTTPException(422,f'符合条件的日志超过{EXPORT_LIMIT}条，请缩小筛选范围后导出，不会截断数据')
        rows=[dict(row) for row in c.execute('SELECT * FROM audit_logs'+where+' ORDER BY id DESC',params)]
    if format=='json':
        content=json.dumps({'exported_at':s.now(),'timezone':'UTC','filters':selected,'count':len(rows),'items':rows},ensure_ascii=False).encode()
        mime='application/json'
    else:
        fields=[('id','ID'),('created_at','时间（UTC）'),('user_id','用户ID'),('username','账号'),('actor','操作者'),('resource','对象类型'),
                ('object_id','对象ID'),('action','动作'),('result','结果'),('status_code','HTTP状态'),('source','来源'),('detail','详情')]
        buffer=io.StringIO(newline='');writer=csv.writer(buffer)
        writer.writerow([title for _,title in fields])
        for row in rows:
            writer.writerow([safe_cell(row.get(key)) for key,_ in fields])
        content=buffer.getvalue().encode('utf-8-sig');mime='text/csv; charset=utf-8'
    s.audit(user,'导出审计日志','审计日志',f'{format}；{len(rows)}条；导出前快照；筛选条件已应用')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="audit-{stamp}.{format}"',
        'X-Export-Count':str(len(rows)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

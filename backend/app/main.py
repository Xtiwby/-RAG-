from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
from . import store as s
from . import db_postgres
from . import rag
from . import settings,jobs,telemetry,blueprints,report_export,ingestion,grounding,template_docx,report_grounding,object_storage,presentation_preview

@asynccontextmanager
async def lifespan(app):
    await jobs.start()
    try:
        yield
    finally:
        await jobs.stop()

app = FastAPI(title='知源 RAG API', version='0.11.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173','http://localhost:4173','http://127.0.0.1:4173','http://127.0.0.1:4174'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
if os.getenv('RAG_WORKER_PROCESS')!='1':
    rag.backfill_legacy()
    # API restarts may interrupt in-flight streams; worker imports must not alter them.
    s.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")
    if os.getenv('RAG_WORKER_MODE','embedded')!='external':
        s.execute("UPDATE report_tasks SET status='interrupted',error='服务重启导致生成中断，请重新生成' WHERE status='generating'")
        s.execute("UPDATE report_sections SET status='interrupted' WHERE status='generating'")
    s.execute("UPDATE report_tasks SET status='needs_review' WHERE status='completed' AND EXISTS (SELECT 1 FROM report_sections WHERE task_id=report_tasks.id AND status='needs_review')")
ACTIVE_CONVERSATIONS: set[int] = set()

@app.middleware('http')
async def audit_requests(request,call_next):
    actor={'name':'匿名','username':''}
    try:
        actor=current_user(request.headers.get('authorization'))
    except HTTPException:
        pass
    actor_token=telemetry.actor_id.set(actor.get('id'))
    config_token=settings.active.set(settings.get())
    code=500
    try:
        response=await call_next(request)
        code=response.status_code
        return response
    finally:
        if request.url.path.startswith('/api/') and (request.method not in ('GET','HEAD','OPTIONS') or code>=400):
            segments=request.url.path.split('/')[2:]
            resource={'users':'用户','roles':'角色','knowledge-bases':'知识库','documents':'文档','auth':'账户','templates':'报告模板',
                      'report-tasks':'报告','tasks':'任务','settings':'系统配置','operations':'运行状态','search':'检索','qa':'问答'}.get(segments[0] if segments else '','系统')
            action={'POST':'执行','PUT':'修改','PATCH':'修改','DELETE':'删除','GET':'读取'}.get(request.method,request.method)
            object_id=next((part for part in segments[1:] if part.isdigit()),'')
            s.audit(actor,action,resource,f'{request.method} {request.url.path}',result='success' if code<400 else 'denied' if code in (401,403) else 'failure',
                    object_id=object_id,status_code=code,source='request')
        settings.active.reset(config_token)
        telemetry.actor_id.reset(actor_token)

def current_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401,'请先登录')
    row=s.one('''SELECT u.* FROM sessions t JOIN users u ON u.id=t.user_id
                 WHERE t.token_hash=? AND t.expires_at>? AND u.status='active' ''',(s.digest(authorization[7:]),s.now()))
    if not row:
        raise HTTPException(401,'登录已过期、账户已停用或密码已重置，请重新登录')
    return s.public_user(row)

def require(user,permission):
    if not user['system_admin'] and permission not in user['permissions']:
        raise HTTPException(403,'缺少权限：'+s.PERMISSIONS.get(permission,permission))

def grant_level(user,kb_id,allow_inactive=False):
    row=s.one("SELECT id,status FROM knowledge_bases WHERE id=? AND status<>'deleted'",(kb_id,))
    if not row:
        raise HTTPException(404,'知识库不存在')
    if user['system_admin']:
        return 'write'
    grant=s.one('SELECT access FROM kb_grants WHERE user_id=? AND kb_id=?',(user['id'],kb_id))
    return grant['access'] if grant else None

def check_kb(user,kb_id,write=False,allow_inactive=False):
    require(user,'kb.write' if write else 'kb.read')
    access=grant_level(user,kb_id,allow_inactive)
    if not access or (write and access!='write'):
        raise HTTPException(403,'无此知识库的'+('维护' if write else '读取')+'授权')
    if not allow_inactive and s.one('SELECT status FROM knowledge_bases WHERE id=?',(kb_id,))['status']!='active':
        raise HTTPException(409,'知识库已停用或归档，不能用于新问答、上传或生成任务')

def accessible_ids(user):
    require(user,'kb.read')
    if user['system_admin']:
        return [r['id'] for r in s.rows("SELECT id FROM knowledge_bases WHERE status='active'")]
    return [r['kb_id'] for r in s.rows("SELECT g.kb_id FROM kb_grants g JOIN knowledge_bases k ON k.id=g.kb_id WHERE g.user_id=? AND k.status='active'",(user['id'],))]

def historical_ids(user):
    require(user,'kb.read')
    if user['system_admin']:
        return [r['id'] for r in s.rows("SELECT id FROM knowledge_bases WHERE status<>'deleted'")]
    return [r['kb_id'] for r in s.rows("SELECT g.kb_id FROM kb_grants g JOIN knowledge_bases k ON k.id=g.kb_id WHERE g.user_id=? AND k.status<>'deleted'",(user['id'],))]

def selected_ids(user,ids=None,single=None,required=False):
    selected=list(dict.fromkeys(ids or ([single] if single else [])))
    if not selected:
        if required:
            raise HTTPException(422,'请选择一个或多个已授权知识库')
        selected=accessible_ids(user)
    for kb_id in selected:
        check_kb(user,kb_id)
    return selected

def document(user,doc_id,write=False):
    row=s.one('SELECT * FROM documents WHERE id=?',(doc_id,))
    if not row:
        raise HTTPException(404,'文档不存在或已删除')
    if write and row['deleted']:
        raise HTTPException(409,'此版本已归档，不能再次修改')
    check_kb(user,row['knowledge_base_id'],write,allow_inactive=not write)
    return row

def conversation(user,cid):
    require(user,'qa.use')
    row=s.one('SELECT * FROM conversations WHERE id=? AND user_id=?',(cid,user['id']))
    if not row:
        raise HTTPException(404,'会话不存在')
    for kb_id in json.loads(row['knowledge_base_ids']):
        check_kb(user,kb_id,allow_inactive=True)
    return row

def safe_doc(row):
    return {k:v for k,v in row.items() if k not in {'path','content','structured','object_key','object_version_id','storage_bucket'}} | {'has_original':bool(row.get('object_key')) if row.get('storage_backend')=='s3' else bool(row.get('path') and Path(row['path']).is_file())}

class Login(BaseModel):
    username: str
    password: str

class UserCreate(BaseModel):
    username: str = Field(pattern=r'^[a-zA-Z0-9_.-]{3,40}$')
    name: str = Field(min_length=1,max_length=50)
    password: str = Field(min_length=8,max_length=128)
    role_id: int

class UserUpdate(BaseModel):
    name: str = Field(min_length=1,max_length=50)
    role_id: int
    status: Literal['active','disabled']

class Profile(BaseModel):
    name: str = Field(min_length=1,max_length=50)

class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8,max_length=128)

class ResetPassword(BaseModel):
    password: str | None = Field(default=None,min_length=8,max_length=128)

class RoleBody(BaseModel):
    name: str = Field(min_length=1,max_length=50)
    permissions: list[str]

class Grant(BaseModel):
    knowledge_base_id: int
    access: Literal['read','write']

class GrantsBody(BaseModel):
    grants: list[Grant]

class KnowledgeBody(BaseModel):
    name: str = Field(min_length=2,max_length=50)
    description: str = ''
    visibility: Literal['private','team','public'] = 'private'

class KnowledgeUpdate(KnowledgeBody):
    status: Literal['active','disabled','archived'] = 'active'

class SearchBody(BaseModel):
    query: str = Field(min_length=1,max_length=4000)
    mode: Literal['vector','keyword','hybrid']|None = None
    knowledge_base_id: int | None = None
    knowledge_base_ids: list[int] = Field(default_factory=list,max_length=30)
    top_k: int|None = Field(default=None,ge=1,le=20)
    rerank: bool|None = None

class QuestionBody(SearchBody):
    conversation_id: int | None = None

class ConversationBody(BaseModel):
    title: str = Field(default='新会话',min_length=1,max_length=100)
    knowledge_base_ids: list[int] = Field(default_factory=list,max_length=30)

class TitleBody(BaseModel):
    title: str = Field(min_length=1,max_length=100)

class FeedbackBody(BaseModel):
    feedback: Literal['helpful','unhelpful','none']

class TemplateBody(BaseModel):
    name: str = Field(min_length=1,max_length=100)
    description: str = ''
    sections: list[str] = Field(min_length=1,max_length=20)
    blueprint: blueprints.ReportBlueprint|None = None

class ReportBody(BaseModel):
    title: str = Field(min_length=1,max_length=100)
    template_id: int
    knowledge_base_id: int
    requirements: str = Field(default='',max_length=5000)
    workflow_version:Literal[0,1]=0
    field_values:dict[str,str]=Field(default_factory=dict)
    period_start:str=Field(default='',max_length=10)
    period_end:str=Field(default='',max_length=10)
    allow_missing:bool=False
    preflight_id:str=Field(default='',max_length=100)

@app.get('/api/health')
def health():
    return {'status':'ok','service':'rag-knowledge-api','version':app.version,
            'data_scope':'acceptance' if s.DATA.name=='data-acceptance-v04' else 'local',
            'database':'postgresql' if os.getenv('RAG_DATABASE_URL') else 'sqlite',
            'original_storage':object_storage.backend(),
            'worker_mode':os.getenv('RAG_WORKER_MODE','embedded')}

@app.get('/api/model-status')
def model_status(user=Depends(current_user)):
    key=os.getenv('KIMI_API_KEY','').strip()
    config=settings.get()
    return {'configured':bool(key and not key.startswith('replace-')), 'provider':'Kimi','model':config.chat.model,
            'embedding':{'api':'semantic-api','local':'local-bge-m3','tfidf':'local-tfidf'}[config.embedding.provider],
            'embedding_model':config.embedding.model,'retrieval':config.retrieval.model_dump(),
            'rerank':'local-cross-encoder' if config.reranking.provider=='local' else 'rank-aware-lexical',
            'reranking_model':config.reranking.model if config.reranking.provider=='local' else None}

@app.post('/api/auth/login')
def login(body:Login):
    row=s.one('SELECT * FROM users WHERE username=?',(body.username,))
    if not row or row['status']!='active' or not s.verify_password(body.password,row['password_hash']):
        s.audit({'name':'未登录用户','username':body.username},'登录','账户','账号、密码或状态校验失败',result='failure',status_code=401)
        raise HTTPException(401,'用户名或密码错误，或账户已停用')
    s.execute('UPDATE users SET last_login=? WHERE id=?',(s.now(),row['id']))
    row=s.one('SELECT * FROM users WHERE id=?',(row['id'],))
    user=s.public_user(row)
    s.audit(user,'登录','账户')
    return {'token':s.new_session(row['id']),'user':user}

@app.post('/api/auth/logout')
def logout(authorization:str=Header(),user=Depends(current_user)):
    s.execute('DELETE FROM sessions WHERE token_hash=?',(s.digest(authorization[7:]),))
    return {'ok':True}

@app.get('/api/auth/me')
def me(user=Depends(current_user)):
    return user

@app.patch('/api/auth/me')
def profile(body:Profile,user=Depends(current_user)):
    s.execute('UPDATE users SET name=? WHERE id=?',(body.name,user['id']))
    s.audit(user,'更新显示名','本人账户')
    return s.public_user(s.one('SELECT * FROM users WHERE id=?',(user['id'],)))

@app.post('/api/auth/password')
def change_password(body:PasswordChange,user=Depends(current_user)):
    row=s.one('SELECT * FROM users WHERE id=?',(user['id'],))
    if not s.verify_password(body.current_password,row['password_hash']):
        raise HTTPException(400,'当前密码不正确')
    s.execute('UPDATE users SET password_hash=? WHERE id=?',(s.password_hash(body.new_password),user['id']))
    s.revoke(user['id'])
    s.audit(user,'修改密码','本人账户')
    return {'ok':True,'message':'密码已修改，所有登录会话已失效，请重新登录'}

@app.get('/api/users')
def users(user=Depends(current_user)):
    if not user['system_admin'] and not {'users.manage','roles.manage'} & set(user['permissions']):
        raise HTTPException(403,'无用户管理或授权配置权限')
    return [s.public_user(row) for row in s.rows('SELECT * FROM users ORDER BY id')]

def assignable_role(user,role_id):
    role=s.one('SELECT * FROM roles WHERE id=?',(role_id,))
    if not role:
        raise HTTPException(422,'角色不存在')
    if role['system_admin'] and not user['system_admin']:
        raise HTTPException(403,'仅系统管理员可以分配系统管理员角色')
    return role

@app.post('/api/users',status_code=201)
def create_user(body:UserCreate,user=Depends(current_user)):
    require(user,'users.manage')
    assignable_role(user,body.role_id)
    try:
        uid=s.execute('INSERT INTO users(username,name,password_hash,role_id,status,created_at) VALUES(?,?,?,?,?,?)',
                      (body.username,body.name,s.password_hash(body.password),body.role_id,'active',s.now()))
    except (sqlite3.IntegrityError, db_postgres.IntegrityError):
        raise HTTPException(409,'账号已存在') from None
    s.audit(user,'创建用户','账户',body.username)
    return s.public_user(s.one('SELECT * FROM users WHERE id=?',(uid,)))

@app.patch('/api/users/{uid}')
def update_user(uid:int,body:UserUpdate,user=Depends(current_user)):
    require(user,'users.manage')
    existing=s.one('SELECT * FROM users WHERE id=?',(uid,))
    if not existing:
        raise HTTPException(404,'用户不存在')
    old_role=assignable_role(user,existing['role_id'])
    new_role=assignable_role(user,body.role_id)
    if uid==user['id'] and (body.status!='active' or body.role_id!=existing['role_id']):
        raise HTTPException(409,'不能停用本人或变更本人角色，请由其他管理员操作')
    if old_role['system_admin'] and (body.status!='active' or not new_role['system_admin']):
        count=s.one("SELECT COUNT(*) n FROM users u JOIN roles r ON r.id=u.role_id WHERE r.system_admin=1 AND u.status='active'")['n']
        if existing['status']=='active' and count<=1:
            raise HTTPException(409,'至少保留一个启用的系统管理员')
    s.execute('UPDATE users SET name=?,role_id=?,status=? WHERE id=?',(body.name,body.role_id,body.status,uid))
    if body.status!='active' or existing['role_id']!=body.role_id:
        s.revoke(uid)
    s.audit(user,'更新用户','账户',existing['username'])
    return s.public_user(s.one('SELECT * FROM users WHERE id=?',(uid,)))

@app.post('/api/users/{uid}/reset-password')
def reset_password(uid:int,body:ResetPassword,user=Depends(current_user)):
    require(user,'users.manage')
    target=s.one('SELECT * FROM users WHERE id=?',(uid,))
    if not target:
        raise HTTPException(404,'用户不存在')
    assignable_role(user,target['role_id'])
    password=body.password or secrets.token_urlsafe(12)
    s.execute('UPDATE users SET password_hash=? WHERE id=?',(s.password_hash(password),uid))
    s.revoke(uid)
    s.audit(user,'重置密码','账户',target['username'])
    return {'temporary_password':password,'message':'密码已重置，原会话已失效；请将临时密码交给用户并提醒其修改'}

@app.get('/api/permissions')
def permissions(user=Depends(current_user)):
    require(user,'roles.manage')
    return [{'id':k,'name':v} for k,v in s.PERMISSIONS.items()]

@app.get('/api/roles')
def roles(user=Depends(current_user)):
    if not user['system_admin'] and not {'users.manage','roles.manage'} & set(user['permissions']):
        raise HTTPException(403,'无角色管理权限')
    return [{**r,'permissions':json.loads(r['permissions'])} for r in s.rows('SELECT * FROM roles')]

def role_values(body):
    if any(p not in s.PERMISSIONS for p in body.permissions):
        raise HTTPException(422,'包含未知权限点')
    return json.dumps(list(dict.fromkeys(body.permissions)))

@app.post('/api/roles',status_code=201)
def create_role(body:RoleBody,user=Depends(current_user)):
    require(user,'roles.manage')
    try:
        rid=s.execute('INSERT INTO roles(name,permissions) VALUES(?,?)',(body.name,role_values(body)))
    except (sqlite3.IntegrityError, db_postgres.IntegrityError):
        raise HTTPException(409,'角色名称已存在') from None
    s.audit(user,'创建角色','角色',body.name)
    return {'id':rid}

@app.put('/api/roles/{rid}')
def update_role(rid:int,body:RoleBody,user=Depends(current_user)):
    require(user,'roles.manage')
    role=s.one('SELECT * FROM roles WHERE id=?',(rid,))
    if not role:
        raise HTTPException(404,'角色不存在')
    if role['system_admin']:
        raise HTTPException(409,'系统管理员为保留角色，权限不可修改')
    try:
        s.execute('UPDATE roles SET name=?,permissions=? WHERE id=?',(body.name,role_values(body),rid))
    except (sqlite3.IntegrityError, db_postgres.IntegrityError):
        raise HTTPException(409,'角色名称已存在') from None
    s.audit(user,'修改角色','角色',body.name)
    return {'ok':True}

@app.get('/api/users/{uid}/grants')
def get_grants(uid:int,user=Depends(current_user)):
    require(user,'roles.manage')
    return s.rows('SELECT kb_id AS knowledge_base_id,access FROM kb_grants WHERE user_id=?',(uid,))

@app.put('/api/users/{uid}/grants')
def put_grants(uid:int,body:GrantsBody,user=Depends(current_user)):
    require(user,'roles.manage')
    if not s.one('SELECT id FROM users WHERE id=?',(uid,)):
        raise HTTPException(404,'用户不存在')
    if len({g.knowledge_base_id for g in body.grants})!=len(body.grants):
        raise HTTPException(422,'知识库授权重复')
    for grant in body.grants:
        if not s.one('SELECT id FROM knowledge_bases WHERE id=?',(grant.knowledge_base_id,)):
            raise HTTPException(422,'知识库不存在')
        # A delegated grant manager can only grant within their own scope.
        if not user['system_admin']:
            check_kb(user,grant.knowledge_base_id,grant.access=='write')
    with s.connect() as c:
        if not user['system_admin']:
            allowed=accessible_ids(user)
            existing=[r['kb_id'] for r in c.execute('SELECT kb_id FROM kb_grants WHERE user_id=?',(uid,))]
            if set(existing)-set(allowed):
                raise HTTPException(403,'不能修改本人管理范围以外的授权')
        c.execute('DELETE FROM kb_grants WHERE user_id=?',(uid,))
        c.executemany('INSERT INTO kb_grants VALUES(?,?,?)',[(uid,g.knowledge_base_id,g.access) for g in body.grants])
    s.audit(user,'更新知识库授权','账户',str(uid))
    return {'ok':True}

@app.get('/api/knowledge-bases')
def knowledge_bases(include_inactive:bool=False,user=Depends(current_user)):
    if include_inactive and not user['system_admin']:
        raise HTTPException(403,'仅系统管理员可管理停用和归档知识库')
    ids=accessible_ids(user)
    if include_inactive:
        ids=[r['id'] for r in s.rows("SELECT id FROM knowledge_bases WHERE status<>'deleted'")]
    if not ids:
        return []
    marks=','.join('?' for _ in ids)
    rows=s.rows(f'''SELECT k.*,COUNT(d.id) document_count,COALESCE(SUM(d.size),0) total_size FROM knowledge_bases k
        LEFT JOIN documents d ON d.knowledge_base_id=k.id AND d.deleted=0 WHERE k.id IN ({marks}) GROUP BY k.id''',ids)
    return [{**r,'access':grant_level(user,r['id'])} for r in rows]

@app.patch('/api/knowledge-bases/{kid}')
def update_kb(kid:int,body:KnowledgeUpdate,user=Depends(current_user)):
    if not user['system_admin']:
        raise HTTPException(403,'仅系统管理员可编辑、停用或归档知识库')
    old=s.one("SELECT * FROM knowledge_bases WHERE id=? AND status<>'deleted'",(kid,))
    if not old:
        raise HTTPException(404,'知识库不存在')
    if not body.name.strip():
        raise HTTPException(422,'知识库名称不能为空')
    s.execute('UPDATE knowledge_bases SET name=?,description=?,visibility=?,status=? WHERE id=?',
              (body.name.strip(),body.description,body.visibility,body.status,kid))
    s.audit(user,'编辑知识库' if old['status']==body.status else {'active':'启用知识库','disabled':'停用知识库','archived':'归档知识库'}[body.status],
            '知识库',f"状态 {old['status']} → {body.status}",object_id=kid)
    return s.one('SELECT * FROM knowledge_bases WHERE id=?',(kid,))

@app.post('/api/knowledge-bases')
def create_kb(body:KnowledgeBody,user=Depends(current_user)):
    require(user,'kb.write')
    kid=s.execute('INSERT INTO knowledge_bases(name,description,visibility,status,created_at) VALUES(?,?,?,?,?)',
                  (body.name,body.description,body.visibility,'active',s.now()))
    s.execute('INSERT INTO kb_grants VALUES(?,?,?)',(user['id'],kid,'write'))
    s.audit(user,'创建','知识库',body.name)
    return s.one('SELECT * FROM knowledge_bases WHERE id=?',(kid,))

@app.delete('/api/knowledge-bases/{kid}')
def delete_kb(kid:int,user=Depends(current_user)):
    check_kb(user,kid,True)
    if s.one('SELECT id FROM documents WHERE knowledge_base_id=? AND deleted=0',(kid,)):
        raise HTTPException(409,'请先移除知识库内文档')
    s.execute("UPDATE knowledge_bases SET status='deleted' WHERE id=?",(kid,))
    s.audit(user,'删除','知识库',str(kid))
    return {'ok':True}

@app.get('/api/documents')
def documents(knowledge_base_id:int|None=None,user=Depends(current_user)):
    ids=selected_ids(user,single=knowledge_base_id)
    if not ids:
        return []
    marks=','.join('?' for _ in ids)
    return [safe_doc(r) for r in s.rows(f'''SELECT d.*,k.name knowledge_base_name FROM documents d
        JOIN knowledge_bases k ON k.id=d.knowledge_base_id WHERE d.deleted=0 AND d.knowledge_base_id IN ({marks}) ORDER BY d.id DESC''',ids)]

@app.post('/api/documents',status_code=201)
async def upload(knowledge_base_id:int=Form(...),modality:Literal['text','image','table']=Form('text'),file:UploadFile=File(...),user=Depends(current_user)):
    check_kb(user,knowledge_base_id,True)
    name=Path((file.filename or 'upload').replace('\\','/')).name
    suffix=Path(name).suffix.lower().lstrip('.')
    if suffix not in {'pdf','docx','txt','md','csv','html','xlsx','ppt','pptx','png','jpg','jpeg','webp'}:
        raise HTTPException(415,'暂不支持此文件格式')
    target=s.UPLOADS/(secrets.token_hex(16)+'.'+suffix)
    size=0
    sha=hashlib.sha256()
    did=ingestion.create_document(knowledge_base_id,name,suffix,modality,str(target))
    stored=None
    key=None
    ingestion.record(did,'upload','running',0,'正在接收原文件')
    last_percent=-1
    try:
        with target.open('wb') as handle:
            while block:=await file.read(1024*1024):
                size+=len(block)
                if size>100*1024*1024:
                    raise HTTPException(413,'单文件不能超过100MB')
                handle.write(block)
                sha.update(block)
                percent=min(99,round(size/max(size,file.size or size)*100))
                if percent!=last_percent:
                    ingestion.record(did,'upload','running',percent,f'已接收 {size} 字节')
                    last_percent=percent
        key=object_storage.object_key('documents',did,s.one('SELECT version FROM documents WHERE id=?',(did,))['version'],suffix)
        stored=await asyncio.to_thread(object_storage.save_file,target,key,sha.hexdigest())
        s.execute("UPDATE documents SET size=?,sha256=?,status='processing',path=?,storage_backend=?,storage_bucket=?,object_key=?,object_version_id=? WHERE id=?",
                  (size,sha.hexdigest(),stored['path'],stored['storage_backend'],stored['storage_bucket'],stored['object_key'],stored['object_version_id'],did))
        if stored['storage_backend']=='s3':target.unlink(missing_ok=True)
        ingestion.record(did,'upload','completed',100,'原文件和 SHA-256 已保存')
        with s.connect() as c:
            jid=jobs.add('ingest',{'document_id':did},user,{'document_id':did},c)
            c.execute('UPDATE documents SET job_id=? WHERE id=?',(jid,did))
        ingestion.record(did,'parse','queued',0,'等待解析工作槽位',jid)
        await jobs.start()
        s.audit(user,'上传文档','文档',name,object_id=did)
        return safe_doc(s.one('SELECT * FROM documents WHERE id=?',(did,)))
    except BaseException as error:
        if not s.one('SELECT job_id FROM documents WHERE id=?',(did,))['job_id']:
            target.unlink(missing_ok=True)
            if key and os.getenv('RAG_FILE_STORAGE','local').lower()=='s3':
                try:
                    object_storage.remove_uncommitted(key,stored['object_version_id'] if stored else '')
                except Exception:
                    pass  # The read-only consistency checker reports any orphaned object.
            s.execute("UPDATE documents SET status='upload_failed',path='',parse_error=? WHERE id=?",('上传未完成',did))
            ingestion.record(did,'upload','failed',last_percent if last_percent>=0 else 0,'上传未完成')
        raise

@app.get('/api/documents/{did}')
def document_detail(did:int,compact:bool=False,offset:int=0,limit:int=40,chunk_id:int|None=None,user=Depends(current_user)):
    doc=document(user,did)
    versions=s.rows('SELECT id,version,sha256,created_at,status FROM documents WHERE name=? AND knowledge_base_id=? AND deleted=0 ORDER BY id DESC',(doc['name'],doc['knowledge_base_id']))
    if not compact:
        chunks=s.rows('SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal',(did,))
        return {**safe_doc(doc),'structured':json.loads(doc['structured']),'chunks':chunks,'versions':versions}
    if limit<1 or limit>100 or offset<0:raise HTTPException(422,'片段分页参数无效')
    total=s.one('SELECT COUNT(*) AS count FROM chunks WHERE document_id=?',(did,))['count']
    if chunk_id is not None:
        target=s.one('SELECT ordinal FROM chunks WHERE id=? AND document_id=?',(chunk_id,did))
        if not target:raise HTTPException(404,'引用片段不属于此文档版本')
        before=s.one('SELECT COUNT(*) AS count FROM chunks WHERE document_id=? AND ordinal<?',(did,target['ordinal']))['count']
        offset=min(max(0,before-limit//2),max(0,total-limit))
    offset=min(offset,max(0,total-limit))
    chunks=s.rows('SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal LIMIT ? OFFSET ?',(did,limit,offset))
    return {**safe_doc(doc),'chunks':chunks,'chunk_count':total,'chunk_offset':offset,'versions':versions}

@app.get('/api/documents/{did}/processing')
def document_processing(did:int,user=Depends(current_user)):
    doc=document(user,did)
    return ingestion.details(doc)

@app.get('/api/documents/{did}/original')
def original(did:int,request:Request,user=Depends(current_user)):
    doc=document(user,did)
    if not object_storage.exists(doc):
        raise HTTPException(404,'此历史演示记录未保存原文件，请上传真实文档')
    return object_storage.original_response(doc,doc['name'],range_header=request.headers.get('range'))


@app.get('/api/documents/{did}/presentation-preview')
def preview_presentation(did:int,user=Depends(current_user)):
    doc=document(user,did)
    return Response(presentation_preview.render(doc), media_type='application/pdf',
                    headers={'Cache-Control':'private, no-store'})

@app.post('/api/documents/{did}/extract-vision')
async def extract_vision(did:int,user=Depends(current_user)):
    doc=document(user,did,True)
    if doc['file_type'] not in {'png','jpg','jpeg','webp'} or not object_storage.exists(doc):
        raise HTTPException(422,'此入口支持图片；扫描 PDF 请先转为逐页图片上传')
    rag.llm_config()
    try:
        async with object_storage.async_local_path(doc) as source:
            units=await rag.visual_extract(source)
    except RuntimeError as error:
        raise HTTPException(502,str(error)) from None
    prepared=rag.prepare_index(units)
    await rag.cache_local_embeddings([chunk[3] for chunk in prepared['chunks']])
    document(user,did,True)
    # A new immutable version is created; historical citations keep pointing at the original text.
    with s.connect() as c:
        count=c.execute('SELECT COUNT(*) FROM documents WHERE name=? AND knowledge_base_id=?',(doc['name'],doc['knowledge_base_id'])).fetchone()[0]
        new_id=c.execute('''INSERT INTO documents(knowledge_base_id,name,file_type,modality,size,status,version,page_count,content,path,created_at,sha256,storage_backend,storage_bucket,object_key,object_version_id)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(doc['knowledge_base_id'],doc['name'],doc['file_type'],doc['modality'],doc['size'],'processing',f'v{count+1}.0',1,'',doc['path'],s.now(),doc['sha256'],doc['storage_backend'],doc['storage_bucket'],doc['object_key'],doc['object_version_id'])).lastrowid
    rag.commit_index(new_id,prepared)
    s.audit(user,'视觉解析','文档',doc['name'])
    return safe_doc(s.one('SELECT * FROM documents WHERE id=?',(new_id,)))

@app.delete('/api/documents/{did}')
def delete_document(did:int,user=Depends(current_user)):
    doc=document(user,did,True)
    s.execute('UPDATE documents SET deleted=1 WHERE id=?',(did,))
    s.audit(user,'移除文档','文档',doc['name'])
    return {'ok':True,'message':'已移出检索范围，原文件和版本保留用于追溯'}

@app.get('/api/dashboard')
def dashboard(user=Depends(current_user)):
    docs=documents(user=user)
    counts={'knowledge_bases':len(accessible_ids(user)),'documents':len(docs),'images':sum(d['modality']=='image' for d in docs),'templates':len(s.rows('SELECT id FROM report_templates'))}
    return {'counts':counts,'modalities':[{'name':m,'value':sum(d['modality']==m for d in docs)} for m in ('text','image','table')],
            'recent_documents':docs[:5],'retrieval_metrics':None,'model':model_status(user)}

@app.post('/api/search')
async def search(body:SearchBody,request:Request,user=Depends(current_user)):
    require(user,'qa.use')
    ids=selected_ids(user,body.knowledge_base_ids,body.knowledge_base_id)
    query=rag.preprocess(body.query)
    if not query:
        raise HTTPException(422,'请输入有效问题')
    start=time.perf_counter()
    results,meta=await rag.retrieve(query,ids,body.mode,body.top_k,body.rerank)
    fresh=current_user(request.headers.get('authorization'))
    for kid in ids:check_kb(fresh,kid)
    mode=body.mode or settings.get().retrieval.mode
    s.audit(user,'检索','知识库',mode)
    return {'mode':mode,'query':query,'total':len(results),'results':results,'elapsed_ms':round((time.perf_counter()-start)*1000),'pipeline':meta}

@app.get('/api/conversations')
def conversations(user=Depends(current_user)):
    require(user,'qa.use')
    permitted=set(historical_ids(user))
    return [r for r in s.rows('SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC',(user['id'],)) if set(json.loads(r['knowledge_base_ids']))<=permitted]

@app.post('/api/conversations',status_code=201)
def create_conversation(body:ConversationBody,user=Depends(current_user)):
    require(user,'qa.use')
    ids=selected_ids(user,body.knowledge_base_ids,required=True)
    cid=s.execute('INSERT INTO conversations(user_id,title,knowledge_base_ids,created_at,updated_at) VALUES(?,?,?,?,?)',
                  (user['id'],body.title,json.dumps(ids),s.now(),s.now()))
    return conversation(user,cid)

@app.get('/api/conversations/{cid}')
def get_conversation(cid:int,user=Depends(current_user)):
    row=conversation(user,cid)
    messages=s.rows('SELECT * FROM messages WHERE conversation_id=? ORDER BY id',(cid,))
    for message in messages:
        message['citations']=json.loads(message['citations'])
        message['verification']=json.loads(message['verification'])
        message['retrieval']=json.loads(message['retrieval'])
        message['coverage']=json.loads(message['coverage'])
    return {**row,'knowledge_base_ids':json.loads(row['knowledge_base_ids']),'messages':messages}

@app.patch('/api/conversations/{cid}')
def rename_conversation(cid:int,body:TitleBody,user=Depends(current_user)):
    conversation(user,cid)
    s.execute('UPDATE conversations SET title=?,updated_at=? WHERE id=?',(body.title,s.now(),cid))
    return {'ok':True}

@app.delete('/api/conversations/{cid}')
def delete_conversation(cid:int,user=Depends(current_user)):
    conversation(user,cid)
    if cid in ACTIVE_CONVERSATIONS:
        raise HTTPException(409,'会话正在生成，请先停止生成')
    s.execute('DELETE FROM conversations WHERE id=?',(cid,))
    return {'ok':True}

@app.post('/api/messages/{mid}/feedback')
def feedback(mid:int,body:FeedbackBody,user=Depends(current_user)):
    message=s.one("SELECT * FROM messages WHERE id=? AND role='assistant'",(mid,))
    if not message:
        raise HTTPException(404,'回答不存在')
    conversation(user,message['conversation_id'])
    s.execute('UPDATE messages SET feedback=? WHERE id=?',(body.feedback,mid))
    return {'ok':True}

def download_text(text,name,kind='md',format_options=None):
    from urllib.parse import quote
    if kind=='docx':
        content=report_export.docx(text,format_options or blueprints.ReportFormat())
        mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    else:
        content=text.encode('utf-8-sig')
        mime='text/markdown; charset=utf-8'
    return Response(content,media_type=mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name+'.'+kind)})

def source_lines(citations,used=None):
    return '\n'.join(f"[{i}] {c['source']} | 知识库#{c['knowledge_base_id']} {c['knowledge_base_name']} | 片段#{c['chunk_id']} | 相关度 {c['relevance']}\n> {c['text']}" for i,c in enumerate(citations,1) if used is None or i in used)

@app.get('/api/conversations/{cid}/export')
def export_conversation(cid:int,format:Literal['md','docx']='md',user=Depends(current_user)):
    row=get_conversation(cid,user)
    scope=[s.one('SELECT id,name,status FROM knowledge_bases WHERE id=?',(kid,)) for kid in row['knowledge_base_ids']]
    text='# '+row['title']+'\n\n会话创建时间：'+row['created_at']+'\n最后更新时间：'+row['updated_at']+'\n导出时间：'+s.now()+'\n时间格式：ISO 8601，UTC（+00:00）\n\n## 完整知识库范围\n'
    text+='\n'.join(f"- #{kb['id']} {kb['name']}（{kb['status']}）" for kb in scope)+'\n'
    for message in row['messages']:
        text+=f"\n## {'用户' if message['role']=='user' else '助手'} ({message['status']})\n\n消息时间：{message['created_at']}\n\n{message['content']}\n"
        if message['citations']:
            text+='\n'+source_lines(message['citations'],{int(n) for n in re.findall(r'\[(\d+)\]',message['content'])})+'\n'
    return download_text(text,row['title'],format)

async def prepare_question(body,user):
    require(user,'qa.use')
    ids=selected_ids(user,body.knowledge_base_ids,body.knowledge_base_id,True)
    query=rag.preprocess(body.query)
    if not query:
        raise HTTPException(422,'请输入有效问题')
    history=[]
    if body.conversation_id:
        existing=conversation(user,body.conversation_id)
        if set(ids)!=set(json.loads(existing['knowledge_base_ids'])):
            raise HTTPException(409,'继续会话时知识库范围须保持一致，更换范围请新建会话')
        prior=s.rows("SELECT role,content FROM messages WHERE conversation_id=? AND status='complete' ORDER BY id DESC LIMIT 8",(body.conversation_id,))
        history=list(reversed(prior))
    retrieval_query=query
    if history and (len(query)<15 or re.search('它|这个|上述|继续|那|其',query)):
        prev=next((m['content'] for m in reversed(history) if m['role']=='user'),'')
        retrieval_query=(prev+' '+query)[-4000:]
    evidence,meta=await rag.retrieve(retrieval_query,ids,body.mode,body.top_k,body.rerank)
    if evidence:
        rag.llm_config()
    return ids,query,history,evidence,meta

@app.post('/api/qa/stream')
async def qa_stream(body:QuestionBody,request:Request,user=Depends(current_user)):
    ids,query,history,evidence,meta=await prepare_question(body,user)
    fresh=current_user(request.headers.get('authorization'))
    for kid in ids:check_kb(fresh,kid)
    if body.conversation_id:
        cid=body.conversation_id
    else:
        cid=create_conversation(ConversationBody(title=query[:40],knowledge_base_ids=ids),user)['id']
    if cid in ACTIVE_CONVERSATIONS:
        raise HTTPException(409,'该会话已有生成任务')
    ACTIVE_CONVERSATIONS.add(cid)
    async def generate():
        mid=None
        text=''
        complete=False
        checks=[]
        try:
            s.execute('INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)',(cid,'user',query,s.now()))
            mid=s.execute("INSERT INTO messages(conversation_id,role,content,citations,status,created_at,retrieval) VALUES(?,?,?,?,?,?,?)",(cid,'assistant','',json.dumps(evidence,ensure_ascii=False),'streaming',s.now(),json.dumps(meta,ensure_ascii=False)))
            yield rag.event('meta',conversation_id=cid,message_id=mid,citations=evidence,pipeline=meta)
            if not evidence:
                text='当前知识库证据不足，无法可靠回答。请补充相关文档、完成文档解析，或缩小问题范围。'
                yield rag.event('token',content=text)
            else:
                async for item in grounding.verified_stream(query,evidence,history):
                    if await request.is_disconnected():
                        break
                    # Disabling a user or revoking a KB takes effect even during an active stream.
                    fresh=current_user(request.headers.get('authorization'))
                    for kid in ids:
                        check_kb(fresh,kid)
                    if item['type']=='verification':
                        checks.extend(item['checks'])
                        s.execute('UPDATE messages SET verification=? WHERE id=?',(json.dumps(checks,ensure_ascii=False),mid))
                        yield rag.event('verification',checks=checks)
                    elif item['type']=='coverage':
                        s.execute('UPDATE messages SET coverage=? WHERE id=?',(json.dumps(item['assessment'],ensure_ascii=False),mid))
                        yield rag.event('coverage',assessment=item['assessment'])
                    elif item['type']=='stage':
                        yield rag.event('stage',stage=item['stage'],message=item['message'])
                    else:
                        text+=item['content']
                        # Only checked content is persisted or sent to the browser.
                        s.execute('UPDATE messages SET content=? WHERE id=?',(text,mid))
                        yield rag.event('token',content=item['content'])
                else:
                    complete=True
                if not complete:
                    return
            complete=True
            s.execute("UPDATE messages SET content=?,status='complete' WHERE id=?",(text,mid))
            s.execute('UPDATE conversations SET updated_at=? WHERE id=?',(s.now(),cid))
            s.audit(user,'流式问答','会话',str(cid))
            yield rag.event('done',conversation_id=cid,message_id=mid)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message=error.detail if isinstance(error,HTTPException) else str(error) if isinstance(error,RuntimeError) else '生成失败，请重试'
            text=(text+'\n\n' if text else '')+'本次生成失败：'+message
            if mid:
                s.execute("UPDATE messages SET content=?,status='error' WHERE id=?",(text,mid))
            yield rag.event('error',message=message,content=text)
        finally:
            if mid and not complete:
                s.execute("UPDATE messages SET content=?,status=CASE WHEN status='error' THEN 'error' ELSE 'interrupted' END WHERE id=?",(text or '生成已中断',mid))
            ACTIVE_CONVERSATIONS.discard(cid)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.post('/api/qa')
def deprecated_qa():
    raise HTTPException(410,'旧版片段拼接问答已移除，请使用 /api/qa/stream')

@app.get('/api/templates')
def templates(deleted:bool=False,user=Depends(current_user)):
    require(user,'reports.generate')
    if deleted:require(user,'templates.manage')
    return [{**{k:v for k,v in r.items() if k not in ('structure','field_mappings')},'knowledge_base_ids':json.loads(r['knowledge_base_ids']),'sections':json.loads(r['sections']),'blueprint':blueprints.from_template(r).model_dump()} for r in s.rows('SELECT * FROM report_templates ORDER BY id DESC') if (r['lifecycle']=='deleted')==deleted and template_docx.allowed(r,user,include_deleted=deleted)]

@app.post('/api/templates',status_code=201)
def create_template(body:TemplateBody,user=Depends(current_user)):
    require(user,'templates.manage')
    if any(not section.strip() for section in body.sections):
        raise HTTPException(422,'章节标题不能为空')
    plan=body.blueprint or blueprints.ReportBlueprint(name=body.name,description=body.description,sections=[blueprints.ReportSection(title=t) for t in body.sections])
    plan.name=body.name
    plan.description=body.description
    tid=s.execute('INSERT INTO report_templates(name,description,sections,version,created_at,blueprint) VALUES(?,?,?,?,?,?)',
                  (plan.name,plan.description,json.dumps([sec.title for sec in plan.sections],ensure_ascii=False),'v1.0',s.now(),plan.model_dump_json()))
    s.audit(user,'创建模板','报告模板',body.name)
    return {'id':tid}

def report_task(user,tid):
    require(user,'reports.generate')
    row=s.one('SELECT * FROM report_tasks WHERE id=?',(tid,))
    if not row or row['user_id']!=user['id']:
        raise HTTPException(404,'报告不存在或不属于本人')
    check_kb(user,row['knowledge_base_id'],allow_inactive=True)
    return row

@app.get('/api/report-tasks')
def report_tasks(user=Depends(current_user)):
    require(user,'reports.generate')
    ids=historical_ids(user)
    return [r for r in s.rows('''SELECT r.*,t.name template_name,k.name knowledge_base_name FROM report_tasks r
        JOIN report_templates t ON t.id=r.template_id JOIN knowledge_bases k ON k.id=r.knowledge_base_id WHERE r.user_id=? ORDER BY r.id DESC''',(user['id'],)) if r['knowledge_base_id'] in ids]

@app.get('/api/report-tasks/{tid}')
def get_report(tid:int,user=Depends(current_user)):
    row=report_task(user,tid)
    row['blueprint']=json.loads(row['blueprint'])
    row['sections']=[{**r,'citations':json.loads(r['citations']),'verification':report_grounding.current(r)} for r in s.rows('SELECT * FROM report_sections WHERE task_id=? ORDER BY ordinal',(tid,))]
    return row

@app.get('/api/report-tasks/{tid}/export')
def export_report(tid:int,format:Literal['md','docx']='docx',user=Depends(current_user)):
    row=report_task(user,tid)
    if row['workflow_version']:
        raise HTTPException(409,'新版报告请使用草稿或定稿导出入口，避免绕过文档状态检查')
    if row['status'] not in {'completed','needs_review','draft'}:
        raise HTTPException(409,'报告尚未完成，请先生成或重试')
    plan=blueprints.ReportBlueprint.model_validate_json(row['blueprint']) if row['blueprint']!='{}' else None
    sections=s.rows('SELECT * FROM report_sections WHERE task_id=? ORDER BY ordinal',(tid,))
    return download_text(report_grounding.compose(row,sections,source_lines),row['title'],format,plan.format if plan else None)

from .admin_features import install
install(app,current_user,require,check_kb,document,ReportBody,source_lines)
from .report_drafts import install as install_report_drafts
install_report_drafts(app,current_user,require,check_kb,report_task,source_lines)
template_docx.install(app,current_user,require,check_kb)
from . import report_workflow
report_workflow.install(app,current_user,require,check_kb,report_task,ReportBody,source_lines)

"""SQLite migrations and authentication. Existing uploads and rows are preserved."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv('RAG_DATA_DIR', str(ROOT / 'data')))
UPLOADS = DATA / 'uploads'
UPLOADS.mkdir(parents=True, exist_ok=True)
PERMISSIONS = {
    'users.manage': '管理用户', 'roles.manage': '配置角色和知识库授权',
    'kb.read': '读取授权知识库', 'kb.write': '维护授权知识库及文档',
    'qa.use': '检索和知识问答', 'reports.generate': '生成及查看本人报告',
    'templates.manage': '维护报告模板', 'audit.read': '查看审计日志',
    'settings.manage': '配置系统参数', 'ops.read': '查看运行状态', 'ops.manage': '管理任务队列',
}

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

@contextmanager
def connect():
    if os.getenv('RAG_DATABASE_URL'):
        from . import db_postgres
        with db_postgres.connect() as conn:
            yield conn
        return
    conn = sqlite3.connect(DATA / 'rag_knowledge.db', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def rows(sql, params=()):
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params)]

def one(sql, params=()):
    result = rows(sql, params)
    return result[0] if result else None

def execute(sql, params=()):
    with connect() as c:
        return c.execute(sql, params).lastrowid

def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + digest

def verify_password(password, stored):
    try:
        salt, expected = stored.split(':')
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def audit(user, action, resource, detail='', result='success', object_id='', status_code=None, source='business'):
    execute('''INSERT INTO audit_logs(actor,action,resource,detail,created_at,user_id,username,result,object_id,status_code,source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (user.get('name','匿名'),action,resource,detail,now(),user.get('id'),user.get('username',''),result,str(object_id),status_code,source))

def public_user(user):
    role = one('SELECT * FROM roles WHERE id=?', (user['role_id'],))
    return {k: user[k] for k in ('id','username','name','status','role_id','last_login')} | {
        'role': role['name'], 'permissions': json.loads(role['permissions']), 'system_admin': bool(role['system_admin'])}

def new_session(user_id):
    token = secrets.token_urlsafe(40)
    expiry = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)', (digest(token),user_id,expiry))
    return token

def revoke(user_id):
    execute('DELETE FROM sessions WHERE user_id=?', (user_id,))

def migrate():
    with connect() as c:
        if os.getenv('RAG_DATABASE_URL'):
            c.execute('SELECT pg_advisory_xact_lock(25102027)')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS roles(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
          permissions TEXT NOT NULL, system_admin INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL, password_hash TEXT NOT NULL, role_id INTEGER NOT NULL REFERENCES roles(id),
          status TEXT NOT NULL DEFAULT 'active', last_login TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS knowledge_bases(id INTEGER PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
          visibility TEXT NOT NULL DEFAULT 'private',status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS kb_grants(user_id INTEGER NOT NULL REFERENCES users(id),kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id),
          access TEXT NOT NULL CHECK(access IN ('read','write')),PRIMARY KEY(user_id,kb_id));
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,knowledge_base_id INTEGER NOT NULL REFERENCES knowledge_bases(id),
          name TEXT NOT NULL,file_type TEXT NOT NULL,modality TEXT NOT NULL,size INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'ready',
          version TEXT NOT NULL DEFAULT 'v1',page_count INTEGER NOT NULL DEFAULT 0,content TEXT NOT NULL DEFAULT '',path TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES documents(id),
          ordinal INTEGER NOT NULL,page INTEGER,section TEXT NOT NULL,text TEXT NOT NULL,char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,
          sha256 TEXT NOT NULL,UNIQUE(document_id,ordinal));
        CREATE TABLE IF NOT EXISTS embedding_cache(namespace TEXT NOT NULL,sha256 TEXT NOT NULL,vector TEXT NOT NULL,PRIMARY KEY(namespace,sha256));
        CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),title TEXT NOT NULL,
          knowledge_base_ids TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          role TEXT NOT NULL,content TEXT NOT NULL,citations TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'complete',
          feedback TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_templates(id INTEGER PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
          sections TEXT NOT NULL,version TEXT NOT NULL DEFAULT 'v1.0',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_tasks(id INTEGER PRIMARY KEY,title TEXT NOT NULL,template_id INTEGER NOT NULL REFERENCES report_templates(id),
          knowledge_base_id INTEGER NOT NULL REFERENCES knowledge_bases(id),status TEXT NOT NULL,progress INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_sections(id INTEGER PRIMARY KEY,task_id INTEGER NOT NULL REFERENCES report_tasks(id),ordinal INTEGER NOT NULL,
          title TEXT NOT NULL,content TEXT NOT NULL DEFAULT '',citations TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,resource TEXT NOT NULL,
          detail TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS system_settings(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL,revision INTEGER NOT NULL,
          updated_at TEXT NOT NULL,updated_by INTEGER);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,user_id INTEGER NOT NULL REFERENCES users(id),
          payload TEXT NOT NULL,config TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',progress INTEGER NOT NULL DEFAULT 0,
          attempts INTEGER NOT NULL DEFAULT 0,max_attempts INTEGER NOT NULL DEFAULT 3,result TEXT NOT NULL DEFAULT '{}',
          error TEXT NOT NULL DEFAULT '',cancel_requested INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT);
        CREATE TABLE IF NOT EXISTS job_events(id INTEGER PRIMARY KEY,job_id INTEGER NOT NULL REFERENCES jobs(id),data TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS worker_heartbeats(id INTEGER PRIMARY KEY CHECK(id=1),worker_id TEXT NOT NULL,seen_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS model_calls(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,model TEXT NOT NULL,user_id INTEGER,job_id INTEGER,
          status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,latency_ms INTEGER,status_code INTEGER,error TEXT NOT NULL DEFAULT '',
          input_tokens INTEGER,output_tokens INTEGER,output_characters INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS document_events(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES documents(id),
          job_id INTEGER,attempt INTEGER NOT NULL DEFAULT 0,stage TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL,
          overall_progress INTEGER NOT NULL,message TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_section_history(id INTEGER PRIMARY KEY,section_id INTEGER NOT NULL REFERENCES report_sections(id),
          revision INTEGER NOT NULL,content TEXT NOT NULL,citations TEXT NOT NULL,status TEXT NOT NULL,confirmed INTEGER NOT NULL,
          edited INTEGER NOT NULL,reason TEXT NOT NULL,user_id INTEGER,job_id INTEGER,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS template_imports(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
          filename TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,structure TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_candidates(id INTEGER PRIMARY KEY,report_id INTEGER NOT NULL REFERENCES report_tasks(id),
          section_id INTEGER NOT NULL REFERENCES report_sections(id),expected_revision INTEGER NOT NULL,content TEXT NOT NULL,
          citations TEXT NOT NULL,verification TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',job_id INTEGER,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_releases(id INTEGER PRIMARY KEY,report_id INTEGER NOT NULL REFERENCES report_tasks(id),
          version INTEGER NOT NULL,snapshot TEXT NOT NULL,created_at TEXT NOT NULL,user_id INTEGER NOT NULL,UNIQUE(report_id,version));
        ''')
        for table, additions in {
            'report_templates': {'blueprint': "TEXT NOT NULL DEFAULT '{}'",'import_id':'INTEGER','parent_id':'INTEGER','structure':"TEXT NOT NULL DEFAULT '{}'",'field_mappings':"TEXT NOT NULL DEFAULT '[]'",'knowledge_base_ids':"TEXT NOT NULL DEFAULT '[]'",'lifecycle':"TEXT NOT NULL DEFAULT 'published'",'family_id':'INTEGER'},
            'report_tasks': {'user_id': 'INTEGER', 'content': "TEXT NOT NULL DEFAULT ''", 'error': "TEXT NOT NULL DEFAULT ''", 'blueprint': "TEXT NOT NULL DEFAULT '{}'", 'job_id':'INTEGER', 'requirements':"TEXT NOT NULL DEFAULT ''", 'revision':'INTEGER NOT NULL DEFAULT 0','updated_at':"TEXT NOT NULL DEFAULT ''"},
            'report_sections': {'revision':'INTEGER NOT NULL DEFAULT 0','edited':'INTEGER NOT NULL DEFAULT 0','confirmed':'INTEGER NOT NULL DEFAULT 0','job_id':'INTEGER','generation_status':"TEXT NOT NULL DEFAULT ''",'generation_error':"TEXT NOT NULL DEFAULT ''",'updated_at':"TEXT NOT NULL DEFAULT ''",'verification':"TEXT NOT NULL DEFAULT '{}'"},
            'report_section_history': {'verification':"TEXT NOT NULL DEFAULT '{}'"},
            'audit_logs': {'user_id':'INTEGER','username':"TEXT NOT NULL DEFAULT ''",'result':"TEXT NOT NULL DEFAULT 'unknown'",'object_id':"TEXT NOT NULL DEFAULT ''",'status_code':'INTEGER','source':"TEXT NOT NULL DEFAULT 'legacy'"},
            'documents': {'storage_backend': "TEXT NOT NULL DEFAULT 'local'", 'storage_bucket': "TEXT NOT NULL DEFAULT ''", 'object_key': "TEXT NOT NULL DEFAULT ''", 'object_version_id': "TEXT NOT NULL DEFAULT ''", 'sha256': "TEXT NOT NULL DEFAULT ''", 'structured': "TEXT NOT NULL DEFAULT '[]'", 'deleted': 'INTEGER NOT NULL DEFAULT 0', 'parse_error': "TEXT NOT NULL DEFAULT ''", 'index_config':"TEXT NOT NULL DEFAULT '{}'", 'job_id':'INTEGER', 'processing_stage':"TEXT NOT NULL DEFAULT ''", 'processing_progress':'INTEGER NOT NULL DEFAULT 0'},
            'template_imports': {'storage_backend': "TEXT NOT NULL DEFAULT 'local'", 'storage_bucket': "TEXT NOT NULL DEFAULT ''", 'object_key': "TEXT NOT NULL DEFAULT ''", 'object_version_id': "TEXT NOT NULL DEFAULT ''"},
            'messages': {'verification':"TEXT NOT NULL DEFAULT '[]'",'retrieval':"TEXT NOT NULL DEFAULT '{}'",'coverage':"TEXT NOT NULL DEFAULT '{}'"},
        }.items():
            columns = {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            for name, kind in additions.items():
                if name not in columns:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        for table, additions in {
            'report_tasks':{'workflow_version':'INTEGER NOT NULL DEFAULT 0','document_state':"TEXT NOT NULL DEFAULT 'draft'",'template_snapshot':"TEXT NOT NULL DEFAULT '{}'",'field_values':"TEXT NOT NULL DEFAULT '{}'",'period_start':"TEXT NOT NULL DEFAULT ''",'period_end':"TEXT NOT NULL DEFAULT ''",'final_release_id':'INTEGER'},
            'report_sections':{'reviewed_revision':'INTEGER NOT NULL DEFAULT -1','stage':"TEXT NOT NULL DEFAULT ''"},
        }.items():
            columns={r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            for name,kind in additions.items():
                if name not in columns:c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        if os.getenv('RAG_DATABASE_URL'):
            c.execute('CREATE EXTENSION IF NOT EXISTS vector')
            c.execute("""CREATE TABLE IF NOT EXISTS chunk_search(
                chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL, knowledge_base_id INTEGER NOT NULL,
                search_terms TEXT NOT NULL, has_zh INTEGER NOT NULL DEFAULT 0, has_en INTEGER NOT NULL DEFAULT 0,
                search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_terms)) STORED,
                embedding vector(1024))""")
            c.execute('ALTER TABLE chunk_search ADD COLUMN IF NOT EXISTS has_zh INTEGER NOT NULL DEFAULT 0')
            c.execute('ALTER TABLE chunk_search ADD COLUMN IF NOT EXISTS has_en INTEGER NOT NULL DEFAULT 0')
            c.execute('CREATE INDEX IF NOT EXISTS chunk_search_terms_idx ON chunk_search USING GIN(search_vector)')
            c.execute('CREATE INDEX IF NOT EXISTS chunk_search_vector_idx ON chunk_search USING hnsw(embedding vector_cosine_ops)')
            c.execute('CREATE INDEX IF NOT EXISTS chunk_search_kb_idx ON chunk_search(knowledge_base_id,document_id)')
        if not c.execute('SELECT 1 FROM roles').fetchone():
            c.executemany('INSERT INTO roles(id,name,permissions,system_admin) VALUES(?,?,?,?)', [
                (1,'系统管理员',json.dumps(list(PERMISSIONS)),1),
                (2,'知识库成员',json.dumps(['kb.read','kb.write','qa.use','reports.generate']),0),
                (3,'评审者',json.dumps(['kb.read','qa.use','reports.generate']),0)])
        if not c.execute('SELECT 1 FROM users').fetchone():
            for username,name,role in [('admin','系统管理员',1),('member','项目成员',2),('teacher','指导教师',3)]:
                c.execute('INSERT INTO users(username,name,password_hash,role_id,status,created_at) VALUES(?,?,?,?,?,?)',
                          (username,name,password_hash(os.getenv('INITIAL_ADMIN_PASSWORD','admin123') if username=='admin' else 'admin123'),role,'active',now()))
        c.execute('UPDATE roles SET permissions=? WHERE system_admin=1',(json.dumps(list(PERMISSIONS)),))
        if not c.execute('SELECT 1 FROM knowledge_bases').fetchone():
            c.execute('INSERT INTO knowledge_bases(name,description,visibility,status,created_at) VALUES(?,?,?,?,?)',
                      ('课程项目知识库','请上传真实项目文档后使用检索与问答','team','active',now()))
        # 首次迁移为原有非管理员账户授予课程知识库，后续以管理员配置为准。
        c.execute('CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY)')
        if not c.execute("SELECT 1 FROM migrations WHERE name='acl-v2'").fetchone():
            first = c.execute('SELECT id FROM knowledge_bases ORDER BY id LIMIT 1').fetchone()
            for u in c.execute("SELECT id,username FROM users WHERE username IN ('member','teacher')").fetchall():
                c.execute('INSERT OR IGNORE INTO kb_grants VALUES(?,?,?)',(u['id'],first['id'],'write' if u['username']=='member' else 'read'))
            c.execute("INSERT INTO migrations VALUES('acl-v2')")
        if not c.execute('SELECT 1 FROM report_templates').fetchone():
            c.execute('INSERT INTO report_templates(name,description,sections,version,created_at) VALUES(?,?,?,?,?)',
                      ('项目周报','基于证据的项目周报',json.dumps(['项目目标','本周进展','问题与风险','后续计划'],ensure_ascii=False),'v1.0',now()))
        # 原版把无正文任务也标为 completed，将其如实迁移为 legacy。
        c.execute("UPDATE report_tasks SET status='legacy',progress=0,error='旧版仅保存任务记录，请重新生成' WHERE user_id IS NULL AND content=''")
        c.execute("UPDATE report_tasks SET user_id=(SELECT id FROM users WHERE username='admin') WHERE user_id IS NULL")
        c.execute("UPDATE audit_logs SET username=COALESCE((SELECT username FROM users WHERE id=audit_logs.user_id),'') WHERE username='' AND user_id IS NOT NULL")
        if os.getenv('RAG_DATABASE_URL'):
            c.align_sequences()

migrate()

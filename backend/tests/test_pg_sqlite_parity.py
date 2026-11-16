"""db_postgres 适配层单测 + PG 模式下审计导出的已知不兼容（skipif）。

前 3 例用假 Inner 测 Connection 翻译层，不依赖 psycopg 运行时，SQLite 模式下也运行并通过。
仅最后 1 例需要真实 PostgreSQL 环境，单独 skipif。
"""
import os
import pytest
from app import db_postgres


def test_insert_or_ignore_is_translated():
    """INSERT OR IGNORE → ON CONFLICT DO NOTHING。"""
    class Inner:
        def execute(self, sql, params=()):
            self.last = sql
            class Cur:
                rowcount = 0
                def fetchone(self): return None
                def fetchall(self): return []
                def __iter__(self): return iter(())
            return Cur()
    inner = Inner()
    conn = db_postgres.Connection(inner)
    conn.execute('INSERT OR IGNORE INTO roles(id,name,permissions) VALUES(?,?,?)', (1, 'x', '[]'))
    assert 'ON CONFLICT DO NOTHING' in inner.last
    assert '?' not in inner.last


def test_begin_immediate_uses_advisory_lock():
    """BEGIN IMMEDIATE → pg_advisory_xact_lock。"""
    class Inner:
        def execute(self, sql, params=()):
            self.last = sql
            class Cur:
                rowcount = 0
                def fetchone(self): return None
                def fetchall(self): return []
                def __iter__(self): return iter(())
            return Cur()
    inner = Inner()
    conn = db_postgres.Connection(inner)
    conn.execute('BEGIN IMMEDIATE')
    assert 'pg_advisory_xact_lock' in inner.last


def test_insert_returns_id_for_id_tables():
    """ID 表 INSERT 自动追加 RETURNING id。"""
    class Inner:
        def execute(self, sql, params=()):
            self.last = sql
            class Cur:
                rowcount = 1
                def fetchone(self): return {'id': 42}
                def fetchall(self): return []
                def __iter__(self): return iter(())
            return Cur()
    inner = Inner()
    conn = db_postgres.Connection(inner)
    cur = conn.execute(
        'INSERT INTO users(username,name,password_hash,role_id,status,created_at) VALUES(?,?,?,?,?,?)',
        ('u', 'n', 'h', 2, 'active', 'now'))
    assert 'RETURNING id' in inner.last
    assert cur.lastrowid == 42


@pytest.mark.skipif(not os.getenv('RAG_DATABASE_URL'), reason='仅在 PostgreSQL 模式运行')
def test_audit_export_postgres_incompatible():
    """已知问题：audit_filters 使用 instr/julianday，PG 模式导出应失败（记录为待处理）。"""
    from test_api import client, admin, application_lifespan
    r = client.get('/api/audits/export', headers=admin, params={'format': 'csv'})
    assert r.status_code >= 500, '若 PG 已修复此问题，请更新 open-issues.md 并移除本用例'
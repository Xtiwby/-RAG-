"""PostgreSQL adapter and audit SQL contracts, plus opt-in real-server parity."""
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
    result = conn.execute('INSERT OR IGNORE INTO roles(id,name,permissions) VALUES(?,?,?)', (1, 'x', '[]'))
    assert result.lastrowid is None
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


def filters():
    from app.audit_export import audit_filters
    return audit_filters(user_id=None, actor='审计_%', resource='', object_id='',
                         action='', result=None, start='2026-11-19T08:00:00+08:00',
                         end='2026-11-19T01:00:00Z')


def test_audit_postgres_filters_preserve_literal_match_and_timezone(monkeypatch):
    monkeypatch.setenv('RAG_DATABASE_URL', 'postgresql://test-only')
    where, params, selected = filters()
    assert 'strpos(actor,?)' in where and 'strpos(username,?)' in where
    assert 'CAST(created_at AS timestamptz)' in where
    assert 'instr(' not in where and 'julianday(' not in where
    assert params[:2] == ['审计_%', '审计_%']
    assert selected['start'] == '2026-11-19T00:00:00+00:00'


@pytest.mark.skipif(not os.getenv('RAG_TEST_POSTGRES_URL'), reason='需独立配置 RAG_TEST_POSTGRES_URL')
def test_audit_filters_against_real_postgres(monkeypatch):
    import psycopg
    monkeypatch.setenv('RAG_DATABASE_URL', os.environ['RAG_TEST_POSTGRES_URL'])
    # A session-local temporary table cannot change business data.
    with psycopg.connect(os.environ['RAG_TEST_POSTGRES_URL'], connect_timeout=3, row_factory=psycopg.rows.dict_row) as raw:
        raw.execute('CREATE TEMP TABLE audit_logs(actor TEXT,username TEXT,created_at TEXT)')
        raw.execute('INSERT INTO audit_logs VALUES(%s,%s,%s)', ('审计_%', 'demo', '2026-11-19T00:30:00Z'))
        raw.execute('INSERT INTO audit_logs VALUES(%s,%s,%s)', ('审计xx', 'demo', '2026-11-19T00:30:00Z'))
        where, params, _ = filters()
        row = db_postgres.Connection(raw).execute('SELECT COUNT(*) AS count FROM audit_logs'+where, params).fetchone()
        assert row[0] == 1
        raw.rollback()

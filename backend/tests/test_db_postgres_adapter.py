"""Database adapter contract tests without a running PostgreSQL server."""
from app import db_postgres


class FakeCursor:
    def __init__(self, row=None):
        self.row=row
        self.rowcount=1

    def fetchone(self):
        value=self.row
        self.row=None
        return value

    def fetchall(self):
        return [self.row] if self.row is not None else []

    def __iter__(self):
        if self.row is not None:
            yield self.row


class FakeConnection:
    def __init__(self):
        self.calls=[]

    def execute(self, sql, params=()):
        self.calls.append((sql,params))
        return FakeCursor({'id':37} if 'RETURNING id' in sql else {'name':'sha256'} if 'information_schema.columns' in sql else None)

    def cursor(self):
        return FakeBatchCursor(self)


class FakeBatchCursor(FakeCursor):
    def __init__(self, owner):
        super().__init__()
        self.owner=owner

    def executemany(self, sql, values):
        self.owner.calls.append((sql,values))


def test_insert_id_parameters_and_boolean_conversion():
    inner=FakeConnection()
    conn=db_postgres.Connection(inner)
    assert conn.execute('INSERT INTO users(username,status) VALUES(?,?)',('demo',True)).lastrowid==37
    assert inner.calls[-1]==('INSERT INTO users(username,status) VALUES(%s,%s) RETURNING id',('demo',1))


def test_sqlite_upserts_are_explicit_postgres_conflicts():
    inner=FakeConnection()
    conn=db_postgres.Connection(inner)
    conn.execute('INSERT OR REPLACE INTO embedding_cache VALUES(?,?,?)',('m','hash','[1,2]'))
    assert 'ON CONFLICT (namespace,sha256) DO UPDATE' in inner.calls[-1][0]
    conn.executemany('INSERT OR REPLACE INTO embedding_cache VALUES(?,?,?)',[('m','a','[1]')])
    assert 'ON CONFLICT (namespace,sha256) DO UPDATE' in inner.calls[-1][0]
    conn.execute('INSERT OR IGNORE INTO kb_grants VALUES(?,?,?)',(1,2,'read'))
    assert inner.calls[-1][0].endswith('ON CONFLICT DO NOTHING')


def test_schema_introspection_and_critical_transaction_lock():
    inner=FakeConnection()
    conn=db_postgres.Connection(inner)
    assert conn.execute('PRAGMA table_info(documents)').fetchone()['name']=='sha256'
    conn.execute('BEGIN IMMEDIATE')
    assert 'pg_advisory_xact_lock' in inner.calls[-1][0]

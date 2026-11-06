"""One-time SQLite to empty PostgreSQL migration. Stop API and worker first."""
import argparse
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent/'.env')

TABLES=['roles','users','sessions','knowledge_bases','kb_grants','documents','chunks',
        'embedding_cache','conversations','messages','template_imports','report_templates',
        'report_tasks','report_sections','audit_logs','system_settings','jobs','job_events',
        'model_calls','document_events','report_section_history','report_candidates',
        'report_releases','report_preflights','migrations']


def run(source,apply):
    if not os.getenv('RAG_DATABASE_URL'):
        raise RuntimeError('请先配置 RAG_DATABASE_URL')
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as old:
        old.row_factory=sqlite3.Row
        present={r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        omitted=present-set(TABLES)-{'worker_heartbeats'}
        if omitted:
            raise RuntimeError(f'源库存在未列入迁移的业务表：{sorted(omitted)}')
        counts={table:old.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in TABLES if table in present}
        print('源库：'+', '.join(f'{name}={count}' for name,count in counts.items()))
        if not apply:
            return
        from app import store as s
        from app import report_workflow  # Create report_preflights on the target.
        with s.connect() as new:
            # The application seeds a fresh database. Refuse any target with real content.
            for table in ('documents','chunks','messages','report_tasks','audit_logs','jobs','template_imports'):
                if new.execute(f'SELECT COUNT(*) n FROM {table}').fetchone()['n']:
                    raise RuntimeError('目标库不是空的新库，已拒绝覆盖')
            users={row['username'] for row in new.execute('SELECT username FROM users')}
            if users!={'admin','member','teacher'} or new.execute('SELECT COUNT(*) n FROM knowledge_bases').fetchone()['n']!=1 or new.execute('SELECT COUNT(*) n FROM report_templates').fetchone()['n']!=1:
                raise RuntimeError('目标库不是初始演示状态，已拒绝覆盖')
            new.execute('TRUNCATE '+','.join(TABLES+['chunk_search','worker_heartbeats'])+' RESTART IDENTITY CASCADE')
            for table in TABLES:
                if table not in present:
                    continue
                target_columns={row['name'] for row in new.execute(f'PRAGMA table_info({table})')}
                source_columns=[r['name'] for r in old.execute(f'PRAGMA table_info({table})')]
                missing_columns=[name for name in source_columns if name not in target_columns]
                if missing_columns:
                    raise RuntimeError(f'{table} 目标库缺少字段 {missing_columns}，已拒绝丢弃数据')
                columns=source_columns
                if not columns:
                    continue
                marks=','.join('?' for _ in columns)
                sql=f'INSERT INTO {table}('+','.join(columns)+f') VALUES({marks})'
                for row in old.execute(f'SELECT '+','.join(columns)+f' FROM {table}'):
                    new.execute(sql,tuple(row))
                if new.execute(f'SELECT COUNT(*) n FROM {table}').fetchone()['n']!=counts[table]:
                    raise RuntimeError(f'{table} 行数校验失败，事务将回滚')
            if 'migrations' not in present:
                new.execute("INSERT INTO migrations(name) VALUES('acl-v2')")
            new.align_sequences()
    print('迁移完成；请运行 rebuild_search_index.py，再启动 API 和 worker。')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    run(args.source.resolve(),args.apply)

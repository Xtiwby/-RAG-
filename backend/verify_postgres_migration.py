"""Read-only primary-key parity check after SQLite to PostgreSQL cutover."""
import argparse
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')
from app import db_postgres
from migrate_postgres import TABLES

POST_MIGRATION_APPEND_ONLY = {'embedding_cache', 'model_calls'}


def verify(source: Path):
    marker = object()
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as old, db_postgres.connect() as new:
        old.row_factory = sqlite3.Row
        present = {row['name'] for row in old.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        total = 0
        for table in TABLES:
            if table not in present:
                continue
            info = list(old.execute(f'PRAGMA table_info({table})'))
            columns = [row['name'] for row in info]
            key = [row['name'] for row in sorted(info, key=lambda row: row['pk']) if row['pk']]
            if not key:
                raise RuntimeError(f'{table} 没有主键，无法逐行核对')
            selection = ','.join(columns)
            target_rows = {}
            for target_row in new.execute(f'SELECT {selection} FROM {table}'):
                identity = tuple(target_row[name] for name in key)
                if identity in target_rows:
                    raise RuntimeError(f'{table} 目标库出现重复主键')
                target_rows[identity] = dict(target_row)
            count = 0
            for source_row in old.execute(f'SELECT {selection} FROM {table}'):
                count += 1
                identity = tuple(source_row[name] for name in key)
                target_row = target_rows.pop(identity, marker)
                if target_row is marker:
                    raise RuntimeError(f'{table} 源库第 {count} 行在目标库缺失')
                source_data = dict(source_row)
                if source_data != target_row:
                    changed = [name for name in columns if source_data[name] != target_row[name]]
                    raise RuntimeError(f'{table} 源库第 {count} 行字段不同：{changed}')
            extra = len(target_rows)
            if extra and table not in POST_MIGRATION_APPEND_ONLY:
                raise RuntimeError(f'{table} 目标库有 {extra} 条非预期新增行')
            total += count
            print(f'{table}: 原有 {count} 行一致' + (f'，回填新增 {extra} 行' if extra else ''))
        chunks = new.execute('SELECT COUNT(*) n FROM chunks').fetchone()['n']
        indexed = new.execute('SELECT COUNT(*) n FROM chunk_search').fetchone()['n']
        missing_vectors = new.execute('SELECT COUNT(*) n FROM chunk_search WHERE embedding IS NULL').fetchone()['n']
        print(f'逐行一致总数 {total}；索引 {indexed}/{chunks}；缺少向量 {missing_vectors}')
        if indexed != chunks or missing_vectors:
            raise RuntimeError('索引尚未完整回填')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(__file__).parent / 'data/rag_knowledge.db')
    arguments = parser.parse_args()
    verify(arguments.source.resolve())

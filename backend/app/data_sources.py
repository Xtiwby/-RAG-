"""Import snapshots from deployment-configured sources."""
import asyncio
import csv
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import SpooledTemporaryFile
from time import monotonic
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from . import db_postgres

MAX_BYTES = 100 * 1024 * 1024


class Source(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    label: str
    knowledge_base_id: int
    kind: Literal['web', 'api', 'object', 'drive', 'database']
    filename: str
    url: str = ''
    headers: dict[str, str] = Field(default_factory=dict)
    sqlite_path: str = ''
    dsn: str = ''
    query: str = ''


def configured(knowledge_base_id):
    path = os.getenv('RAG_DATA_SOURCES_FILE')
    if not path:
        return {}
    try:
        sources = TypeAdapter(list[Source]).validate_json(Path(path).read_bytes())
        if len({source.id for source in sources}) != len(sources):
            raise ValueError('Duplicate source ID')
    except (OSError, ValidationError, ValueError):
        raise HTTPException(503, '数据源配置无法读取或格式错误') from None
    return {source.id: source for source in sources if source.knowledge_base_id == knowledge_base_id}


def write_block(output, block):
    if output.tell() + len(block) > MAX_BYTES:
        raise HTTPException(413, '数据源内容超过100MB')
    output.write(block)


def database_snapshot(source, output):
    if not source.query or bool(source.sqlite_path) == bool(source.dsn):
        raise ValueError('Invalid database source')
    if source.sqlite_path:
        uri = Path(source.sqlite_path).resolve().as_uri() + '?mode=ro'
        connection = sqlite3.connect(uri, uri=True)
    else:
        if db_postgres.psycopg is None:
            raise ValueError('PostgreSQL driver unavailable')
        connection = db_postgres.psycopg.connect(
            source.dsn, connect_timeout=10,
            options='-c statement_timeout=30000 -c default_transaction_read_only=on',
        )
    with closing(connection):
        if source.sqlite_path:
            connection.execute('PRAGMA query_only=ON')
            deadline = monotonic() + 30
            connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            cursor = connection.cursor()
        else:
            cursor = connection.cursor(name='rag_source_snapshot')

        class CsvOutput:
            def write(self, value):
                write_block(output, value.encode('utf-8'))

        with closing(cursor):
            cursor.execute(source.query)
            first = cursor.fetchone()
            if first is None:
                raise HTTPException(422, '数据库查询没有返回记录')
            writer = csv.writer(CsvOutput())
            writer.writerow([column[0] for column in cursor.description])
            writer.writerow(first)
            writer.writerows(cursor)


async def http_snapshot(source, output):
    url = httpx.URL(source.url)
    if url.scheme not in ('http', 'https') or not url.host or url.userinfo:
        raise ValueError('Invalid source URL')
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        async with client.stream('GET', url, headers=source.headers) as response:
            response.raise_for_status()
            async for block in response.aiter_bytes(64 * 1024):
                write_block(output, block)


async def fetch(source):
    # Keep the spool open until a database thread exits, even if the request is cancelled.
    output = SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        if source.kind == 'database':
            task = asyncio.create_task(asyncio.to_thread(database_snapshot, source, output))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise
        else:
            await asyncio.wait_for(http_snapshot(source, output), timeout=60)
        size = output.tell()
        if not size:
            raise HTTPException(422, '数据源返回空内容')
        output.seek(0)
        return output, size
    except BaseException as error:
        output.close()
        if isinstance(error, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(502, '读取数据源失败，请检查连接、凭据和来源配置') from None

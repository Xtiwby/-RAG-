"""Import snapshots from deployment-configured sources.

Sources live in the file named by ``RAG_DATA_SOURCES_FILE``; the database never
stores their URLs or credentials. Secrets are referenced by environment variable
name (``url_env``/``dsn_env``/``auth_env``) so the JSON file can be shared.
"""
import asyncio
import csv
import hashlib
import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import SpooledTemporaryFile
from time import monotonic
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from . import db_postgres, ingestion, jobs, object_storage, settings
from . import store as s

MAX_BYTES = 100 * 1024 * 1024


class Source(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    label: str
    knowledge_base_id: int
    kind: Literal['web', 'api', 'object', 'drive', 'database']
    filename: str
    url: str = ''
    url_env: str = ''
    headers: dict[str, str] = Field(default_factory=dict)
    auth_env: str = ''
    sqlite_path: str = ''
    dsn: str = ''
    dsn_env: str = ''
    query: str = ''


class ImportBody(settings.Strict):
    source_id: str
    knowledge_base_id: int


def resolve(source):
    """Read secrets from the environment so the config file stays shareable."""
    updates = {}
    if source.url_env:
        updates['url'] = os.getenv(source.url_env, '')
    if source.dsn_env:
        updates['dsn'] = os.getenv(source.dsn_env, '')
    if source.auth_env:
        token = os.getenv(source.auth_env, '')
        if token:
            updates['headers'] = {**source.headers, 'Authorization': token if ' ' in token else f'Bearer {token}'}
    return source.model_copy(update=updates) if updates else source


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
            if 300 <= response.status_code < 400:
                # Redirects could forward credentials to another host.
                raise HTTPException(502, '数据源返回重定向，请直接配置最终地址')
            response.raise_for_status()
            async for block in response.aiter_bytes(64 * 1024):
                write_block(output, block)


def object_snapshot(source, output):
    parsed = urlparse(source.url)
    if not parsed.netloc or not parsed.path.lstrip('/'):
        raise ValueError('Invalid object URL')
    options = {'Bucket': parsed.netloc, 'Key': parsed.path.lstrip('/')}
    if parsed.query:
        options['VersionId'] = parsed.query.removeprefix('versionId=')
    object_storage.client().download_fileobj(options['Bucket'], options['Key'], output)


async def fetch(source):
    # Keep the spool open until a database thread exits, even if the request is cancelled.
    source = resolve(source)
    output = SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        if source.kind == 'database':
            task = asyncio.create_task(asyncio.to_thread(database_snapshot, source, output))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise
        elif source.kind == 'object' and urlparse(source.url).scheme == 's3':
            await asyncio.wait_for(asyncio.to_thread(object_snapshot, source, output), timeout=120)
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


def snapshot_name(source):
    name = (source.filename or source.id).replace('\\', '/').rsplit('/', 1)[-1].strip()
    if not name or '\0' in name or name.startswith('.'):
        raise HTTPException(422, '数据源文件名无效，请在配置中提供正常文件名')
    return name


async def import_document(source, user):
    """Download one snapshot and hand it to the normal parse/chunk/index pipeline."""
    name = snapshot_name(source)
    suffix = Path(name).suffix.lower().lstrip('.')
    if suffix not in ingestion.SUPPORTED_TYPES:
        raise HTTPException(422, '数据源文件扩展名不受支持，请检查配置中的 filename')
    config = settings.get()
    output, size = await fetch(source)
    did = None
    staged = None
    stored = None
    try:
        staged = s.UPLOADS / secrets.token_hex(16) / name
        staged.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with staged.open('xb') as handle:
            while block := output.read(1024 * 1024):
                digest.update(block)
                handle.write(block)
        output.close()
        sha = digest.hexdigest()
        did = ingestion.create_document(source.knowledge_base_id, name, suffix, 'text', str(staged))
        ingestion.record(did, 'upload', 'running', 0, '正在保存数据源快照')
        version = s.one('SELECT version FROM documents WHERE id=?', (did,))['version']
        key = object_storage.object_key('documents', did, version, suffix)
        stored = await asyncio.to_thread(object_storage.save_file, staged, key, sha)
        s.execute("""UPDATE documents SET size=?,sha256=?,status='processing',path=?,storage_backend=?,
            storage_bucket=?,object_key=?,object_version_id=?,source_ref=? WHERE id=?""",
            (size, sha, stored['path'], stored['storage_backend'], stored['storage_bucket'],
             stored['object_key'], stored['object_version_id'], source.id, did))
        if stored['storage_backend'] == 's3':
            staged.unlink(missing_ok=True)
            staged.parent.rmdir()
            staged = None
        ingestion.record(did, 'upload', 'completed', 100, '数据源快照与 SHA-256 已保存')
        with s.connect() as c:
            jid = jobs.add('ingest', {'document_id': did}, user, {'document_id': did}, c, config=config)
            c.execute('UPDATE documents SET job_id=? WHERE id=?', (jid, did))
        ingestion.record(did, 'parse', 'queued', 0, '等待解析工作槽位', jid)
        await jobs.start()
        s.audit(user, '导入数据源', '文档', f'{source.kind}:{source.label}', object_id=did)
        return did
    except BaseException as error:
        if staged is not None and staged.exists():
            staged.unlink(missing_ok=True)
            if staged.parent.exists():
                staged.parent.rmdir()
        if stored and os.getenv('RAG_FILE_STORAGE', 'local').lower() == 's3':
            try:
                object_storage.remove_uncommitted(stored['object_key'], stored['object_version_id'])
            except Exception:
                pass
        if did is not None:
            s.execute("UPDATE documents SET status='upload_failed',path='',parse_error=? WHERE id=?",
                      ('数据源导入未完成', did))
            ingestion.record(did, 'upload', 'failed', 0, '数据源导入未完成')
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(502, '导入数据源失败，请检查来源配置与文件内容') from None


def install(app, current_user, require, check_kb, safe_doc):
    @app.get('/api/data-sources')
    def list_sources(knowledge_base_id: int, user=Depends(current_user)):
        check_kb(user, knowledge_base_id)
        return [{'id': source.id, 'label': source.label, 'kind': source.kind}
                for source in configured(knowledge_base_id).values()]

    @app.post('/api/data-sources/import', status_code=201)
    async def import_source(body: ImportBody, user=Depends(current_user)):
        check_kb(user, body.knowledge_base_id, True)
        source = configured(body.knowledge_base_id).get(body.source_id)
        if not source:
            raise HTTPException(404, '数据源不存在或不属于此知识库')
        did = await import_document(source, user)
        return safe_doc(s.one('SELECT * FROM documents WHERE id=?', (did,)))

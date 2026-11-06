"""Original files on local disk or private S3-compatible object storage."""
from contextlib import contextmanager, asynccontextmanager
import asyncio
from pathlib import Path
from urllib.parse import quote
import hashlib
import mimetypes
import os
import re
import secrets
import tempfile

from fastapi.responses import FileResponse, StreamingResponse
from . import store as s


def backend():
    value = os.getenv('RAG_FILE_STORAGE', 'local').lower()
    if value not in {'local', 's3'}:
        raise RuntimeError('RAG_FILE_STORAGE 必须是 local 或 s3')
    return value


def bucket():
    value = os.getenv('RAG_S3_BUCKET', '').strip()
    if not value:
        raise RuntimeError('对象存储未配置 RAG_S3_BUCKET')
    return value


def client():
    try:
        import boto3
    except ImportError:
        raise RuntimeError('对象存储需安装 requirements-server.txt') from None
    return boto3.client('s3', endpoint_url=os.getenv('RAG_S3_ENDPOINT') or None,
                        region_name=os.getenv('RAG_S3_REGION', 'us-east-1'),
                        aws_access_key_id=os.getenv('RAG_S3_ACCESS_KEY') or None,
                        aws_secret_access_key=os.getenv('RAG_S3_SECRET_KEY') or None)


def object_key(kind, identifier, version, suffix):
    if kind not in {'documents', 'templates'}:
        raise ValueError('无效对象类别')
    return f'{kind}/{identifier}/{version}/{secrets.token_hex(16)}.{suffix}'


def file_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def save_file(path, key, sha256):
    """Save a hashed staging file; commit its database pointer only after success."""
    path = Path(path)
    if backend() == 'local':
        return {'storage_backend': 'local', 'storage_bucket': '', 'object_key': '', 'object_version_id': '', 'path': str(path)}
    store = client()
    store.upload_file(str(path), bucket(), key, ExtraArgs={'Metadata': {'sha256': sha256}})
    head = store.head_object(Bucket=bucket(), Key=key)
    if head['ContentLength'] != path.stat().st_size or head.get('Metadata', {}).get('sha256') != sha256:
        raise RuntimeError('对象存储上传校验失败')
    return {'storage_backend': 's3', 'storage_bucket': bucket(), 'object_key': key,
            'object_version_id': head.get('VersionId', ''), 'path': ''}


def save_bytes(data, key, sha256):
    if backend() == 'local':
        path = s.UPLOADS / key.rsplit('/', 1)[-1]
        path.write_bytes(data)
        return {'storage_backend': 'local', 'storage_bucket': '', 'object_key': '', 'object_version_id': '', 'path': str(path)}
    store = client()
    result = store.put_object(Bucket=bucket(), Key=key, Body=data, Metadata={'sha256': sha256})
    head = store.head_object(Bucket=bucket(), Key=key)
    if head['ContentLength'] != len(data) or head.get('Metadata', {}).get('sha256') != sha256:
        options = {'Bucket': bucket(), 'Key': key}
        if result.get('VersionId'):
            options['VersionId'] = result['VersionId']
        store.delete_object(**options)
        raise RuntimeError('对象存储模板上传校验失败')
    return {'storage_backend': 's3', 'storage_bucket': bucket(), 'object_key': key,
            'object_version_id': head.get('VersionId', result.get('VersionId', '')), 'path': ''}


def locator(row):
    if row.get('storage_backend') == 's3' and row.get('object_key'):
        result = {'Bucket': row.get('storage_bucket') or bucket(), 'Key': row['object_key']}
        if row.get('object_version_id'):
            result['VersionId'] = row['object_version_id']
        return result
    return None


def exists(row):
    options = locator(row)
    if options:
        try:
            client().head_object(**options)
            return True
        except Exception as error:
            code=getattr(error,'response',{}).get('Error',{}).get('Code','')
            if str(code) in {'404','NoSuchKey','NoSuchVersion','NotFound'}:
                return False
            raise
    return bool(row.get('path') and Path(row['path']).is_file())


@contextmanager
def local_path(row):
    """Current parsers take a Path, so materialize S3 objects only for a job."""
    options = locator(row)
    if not options:
        if not row.get('path') or not Path(row['path']).is_file():
            raise FileNotFoundError('原文件不存在')
        yield Path(row['path'])
        return
    suffix = '.' + row.get('file_type', 'bin').lstrip('.')
    with tempfile.TemporaryDirectory(prefix='rag-original-') as folder:
        path = Path(folder) / ('original' + suffix)
        args = {'ExtraArgs': {'VersionId': options['VersionId']}} if 'VersionId' in options else {}
        with path.open('wb') as stream:
            client().download_fileobj(options['Bucket'], options['Key'], stream, **args)
        expected = row.get('sha256')
        if expected and file_sha256(path) != expected:
            raise RuntimeError('对象存储原文件哈希校验失败')
        yield path


@asynccontextmanager
async def async_local_path(row):
    manager=local_path(row)
    path=await asyncio.to_thread(manager.__enter__)
    try:
        yield path
    finally:
        await asyncio.to_thread(manager.__exit__,None,None,None)


def read_bytes(row):
    with local_path(row) as path:
        return path.read_bytes()


def original_response(row, filename, media_type=None, range_header=None):
    media_type = media_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    options = locator(row)
    if not options:
        return FileResponse(row['path'], filename=filename, content_disposition_type='inline', media_type=media_type)
    if range_header and re.fullmatch(r'bytes=\d*-\d*', range_header):
        options['Range'] = range_header
    result = client().get_object(**options)
    body = result['Body']
    def stream():
        try:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            body.close()
    headers={'Content-Disposition': "inline; filename*=UTF-8''" + quote(filename),
             'Accept-Ranges':'bytes'}
    if result.get('ContentRange'):
        headers['Content-Range']=result['ContentRange']
    if result.get('ContentLength') is not None:
        headers['Content-Length']=str(result['ContentLength'])
    return StreamingResponse(stream(), media_type=media_type,
        status_code=206 if result.get('ContentRange') else 200, headers=headers)


def remove_uncommitted(key, version_id=''):
    if backend() == 's3':
        options = {'Bucket': bucket(), 'Key': key}
        if version_id:
            options['VersionId'] = version_id
        client().delete_object(**options)

import json

import pytest
from fastapi import HTTPException

from test_api import admin, application_lifespan, client, kb
from app import data_sources


def write_config(monkeypatch, tmp_path, sources):
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps(sources), encoding='utf-8')
    monkeypatch.setenv('RAG_DATA_SOURCES_FILE', str(path))
    return path


def test_env_indirection_keeps_secrets_out_of_the_config(monkeypatch):
    monkeypatch.setenv('RAG_SRC_URL', 'https://example.invalid/report.html')
    monkeypatch.setenv('RAG_SRC_TOKEN', 'secret-token')
    source = data_sources.Source(id='a', label='A', knowledge_base_id=1, kind='web',
                                 filename='a.html', url_env='RAG_SRC_URL', auth_env='RAG_SRC_TOKEN')
    resolved = data_sources.resolve(source)
    assert resolved.url == 'https://example.invalid/report.html'
    assert resolved.headers['Authorization'] == 'Bearer secret-token'


def test_missing_env_indirection_leaves_empty_url(monkeypatch):
    monkeypatch.delenv('RAG_SRC_MISSING', raising=False)
    source = data_sources.Source(id='a', label='A', knowledge_base_id=1, kind='web',
                                 filename='a.html', url_env='RAG_SRC_MISSING')
    assert data_sources.resolve(source).url == ''


def test_invalid_configuration_reports_service_unavailable(monkeypatch, tmp_path):
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps([
        {'id': 'dup', 'label': 'A', 'knowledge_base_id': 1, 'kind': 'web', 'filename': 'a.html'},
        {'id': 'dup', 'label': 'B', 'knowledge_base_id': 1, 'kind': 'web', 'filename': 'b.html'},
    ]), encoding='utf-8')
    monkeypatch.setenv('RAG_DATA_SOURCES_FILE', str(path))
    with pytest.raises(HTTPException) as info:
        data_sources.configured(1)
    assert info.value.status_code == 503


def test_snapshot_name_rejects_paths_and_dotfiles():
    source = data_sources.Source(id='a', label='A', knowledge_base_id=1, kind='web', filename='../x/报告.html')
    assert data_sources.snapshot_name(source) == '报告.html'
    with pytest.raises(HTTPException) as info:
        data_sources.snapshot_name(data_sources.Source(id='a', label='A', knowledge_base_id=1,
                                                       kind='web', filename='.hidden'))
    assert info.value.status_code == 422


def test_unsupported_snapshot_suffix_is_refused(monkeypatch, tmp_path):
    write_config(monkeypatch, tmp_path, [])
    source = data_sources.Source(id='bad', label='bad', knowledge_base_id=1, kind='web', filename='x.exe')
    with pytest.raises(HTTPException) as info:
        import asyncio
        asyncio.run(data_sources.import_document(source, {'id': 1}))
    assert info.value.status_code == 422


def test_listing_requires_read_access_to_the_library(admin, monkeypatch, tmp_path):
    write_config(monkeypatch, tmp_path, [
        {'id': 'web', 'label': '网页', 'kind': 'web', 'knowledge_base_id': 999, 'filename': 'a.html'},
    ])
    response = client.get('/api/data-sources', headers=admin, params={'knowledge_base_id': 999})
    assert response.status_code == 404
    assert client.post('/api/data-sources/import', headers=admin,
                       json={'source_id': 'web', 'knowledge_base_id': 999}).status_code == 404

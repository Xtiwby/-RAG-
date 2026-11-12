import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_api import admin, application_lifespan, client, finish_upload, kb, upload
from app import blueprints, presentation_preview, report_export


@pytest.mark.parametrize(('filename', 'content', 'expected'), [
    ('source.txt', '纯文本验收记录', '纯文本验收记录'),
    ('source.md', '# 验收\n\nMarkdown 验收记录', 'Markdown 验收记录'),
    ('source.html', '<p>网页<strong>正文</strong></p><script>ignore_script()</script>', '网页正文'),
])
def test_text_file_ingestion(admin, filename, content, expected):
    doc = upload(admin, kb(admin), filename, content)
    assert doc['status'] == 'ready'
    indexed = ' '.join(chunk['text'] for chunk in doc['chunks'])
    assert expected in indexed and 'ignore_script' not in indexed


@pytest.mark.parametrize(('modern', 'legacy', 'modality'), [
    ('docx', 'doc', 'text'), ('xlsx', 'xls', 'table'),
])
def test_legacy_office_ingestion(admin, monkeypatch, tmp_path, modern, legacy, modality):
    source = tmp_path / f'fixture.{modern}'
    expected = '旧版 Office 验收记录'
    if modern == 'docx':
        source.write_bytes(report_export.docx(expected, blueprints.ReportFormat()))
    else:
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.append([expected])
        workbook.save(source)
    original = b'legacy office fixture'

    def convert(executable, staged, target):
        assert staged.suffix == '.' + legacy
        assert staged.read_bytes() == original
        assert target == modern
        staged.with_suffix('.' + target).write_bytes(source.read_bytes())
        return 0

    monkeypatch.setattr(presentation_preview, '_soffice', lambda: 'test-converter')
    monkeypatch.setattr(presentation_preview, '_convert_office', convert)
    response = client.post('/api/documents', headers=admin, data={'knowledge_base_id': kb(admin)},
                           files={'file': (f'fixture.{legacy}', original)})
    assert response.status_code == 201, response.text
    doc = finish_upload(admin, response.json())
    assert doc['status'] == 'ready' and doc['modality'] == modality, doc
    assert any(expected in chunk['text'] for chunk in doc['chunks'])


def test_import_configured_sources(admin, monkeypatch, tmp_path):
    responses = {
        '/web': '<h1>网页正文</h1><style>ignore_style</style>',
        '/api': '{"message":"接口记录"}',
        '/object': '对象存储原件',
        '/drive': '企业网盘原件',
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/web')
                self.end_headers()
                return
            if self.headers.get('Authorization') != 'Bearer fixture-token':
                self.send_error(403)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(responses[self.path].encode())

    kid = kb(admin)
    other = kb(admin)
    database = tmp_path / 'source.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE entries (title TEXT)')
        connection.execute('INSERT INTO entries VALUES (?)', ('数据库记录',))
    config = tmp_path / 'sources.json'
    monkeypatch.setenv('RAG_DATA_SOURCES_FILE', str(config))

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            sources = [
                dict(id=name, label=name, kind=kind, knowledge_base_id=kid,
                     filename=f'{name}.{suffix}', url=f'http://127.0.0.1:{server.server_port}/{name}',
                     headers={'Authorization': 'Bearer fixture-token'})
                for name, kind, suffix in [('web', 'web', 'html'), ('api', 'api', 'json'),
                                           ('object', 'object', 'txt'), ('drive', 'drive', 'txt'),
                                           ('redirect', 'web', 'html')]
            ]
            sources.append(dict(id='db', label='db', kind='database', knowledge_base_id=kid,
                                filename='db.csv', sqlite_path=str(database), query='SELECT title FROM entries'))
            config.write_text(json.dumps(sources), encoding='utf-8')
            response = client.get('/api/data-sources', headers=admin, params={'knowledge_base_id': kid})
            assert response.status_code == 200
            assert len(response.json()) == len(sources)
            assert all(set(item) == {'id', 'label', 'kind'} for item in response.json())

            def submit(source_id, library=kid):
                return client.post('/api/data-sources/import', headers=admin,
                                   json={'source_id': source_id, 'knowledge_base_id': library})

            assert submit('web', other).status_code == 404
            assert submit('redirect').status_code == 502
            for name, expected in [('web', '网页正文'), ('api', '接口记录'), ('object', '对象存储原件'),
                                   ('drive', '企业网盘原件'), ('db', '数据库记录')]:
                response = submit(name)
                assert response.status_code == 201, response.text
                doc = finish_upload(admin, response.json())
                assert doc['status'] == 'ready', doc
                assert any(expected in chunk['text'] for chunk in doc['chunks'])
            assert submit('db').json()['version'] == 'v2.0'
        finally:
            server.shutdown()
            worker.join()

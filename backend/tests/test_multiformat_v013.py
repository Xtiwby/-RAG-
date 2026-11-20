"""多格式接入边界回归；隔离库、mock 模型，不发起计费调用。"""
import io
import struct
import zipfile
import pytest
from test_api import client, admin, application_lifespan, kb, upload, finish_upload, create_member, configured
from app import rag, ingestion, settings
from pathlib import Path
import pytest

SAMPLE_ROOT = Path(__file__).resolve().parents[2] / 'tests' / 'data' / 'multiformat-2026-11' / 'corpus'

def _sample(rel: str):
    """返回样本路径；不存在则 skip（方案 A：二进制样本未落盘）。"""
    path = SAMPLE_ROOT / rel
    if not path.is_file():
        pytest.skip(f"样本未落盘：{rel}（见 tests/data/multiformat-2026-11/README.md 样本状态）")
    return path.read_bytes()


def _upload_raw(headers, kid, name, data, content_type=None):
    files = {'file': (name, data, content_type)} if content_type else {'file': (name, data)}
    return client.post('/api/documents', headers=headers, data={'knowledge_base_id': kid}, files=files)


def test_encrypted_ppt_is_rejected(admin):
    """加密 PPT：Current User 流含加密标记 → parse_failed，错误可读。"""
    kid = kb(admin)
    # 最小 CFB 头 + 伪造 Current User 加密标记，足以触发 presentations.CompoundFile 的加密分支
    header = bytearray(512)
    header[:8] = bytes.fromhex('D0CF11E0A1B11AE1')
    struct.pack_into('<H', header, 26, 3)          # major v3
    struct.pack_into('<H', header, 30, 9)          # sector shift 9
    header[28:30] = b'\xfe\xff'
    header[32:34] = b'\x06\x00'
    raw = bytes(header) + b'\0' * 512 * 8
    resp = _upload_raw(admin, kid, 'encrypted.ppt', raw)
    assert resp.status_code == 201
    doc = finish_upload(admin, resp.json())
    assert doc['status'] == 'parse_failed'
    detail = client.get(f"/api/documents/{doc['id']}/processing", headers=admin).json()
    assert detail['error']


def test_gb18030_text_is_decoded(admin):
    """GB18030 编码 TXT 应正确解码并索引。"""
    kid = kb(admin)
    text = '混合检索采用 BM25 与 RRF 融合。'
    raw = text.encode('gb18030')
    resp = _upload_raw(admin, kid, 'gb18030.txt', raw, 'text/plain')
    assert resp.status_code == 201
    doc = finish_upload(admin, resp.json())
    assert doc['status'] == 'ready'
    assert any('混合检索' in c['text'] for c in doc['chunks'])


def test_json_is_flattened(admin):
    """JSON 按 key: value 展平为多行片段。"""
    kid = kb(admin)
    raw = b'{"a": {"b": 1}, "list": ["x", "y"]}'
    resp = _upload_raw(admin, kid, 'data.json', raw, 'application/json')
    assert resp.status_code == 201
    doc = finish_upload(admin, resp.json())
    assert doc['status'] == 'ready'
    joined = '\n'.join(c['text'] for c in doc['chunks'])
    assert 'a.b: 1' in joined
    assert 'list[1]: x' in joined


def test_invalid_json_reports_readable_error(admin):
    """非法 JSON 应给出可读错误。"""
    kid = kb(admin)
    resp = _upload_raw(admin, kid, 'bad.json', b'{not json', 'application/json')
    assert resp.status_code == 201
    doc = finish_upload(admin, resp.json())
    assert doc['status'] == 'parse_failed'
    detail = client.get(f"/api/documents/{doc['id']}/processing", headers=admin).json()
    assert 'JSON' in detail['error']


def test_video_frame_ocr_failure_keeps_transcript(admin, configured, monkeypatch):
    """视频帧 OCR 全部失败时，转写片段仍应保留并 ready。"""
    kid = kb(admin)
    from app import media, ocr
    from test_api import save_config
    save_config(admin, lambda cfg: cfg['media'].update(frame_ocr=True))
    seen = []
    async def transcribe(path):
        return '转写文本', [{'start': 0, 'end': 1, 'text': '转写文本'}], 'mock-asr'

    async def failing_ocr(path, section='', page=1):
        seen.append(path)
        raise RuntimeError('帧 OCR 失败')

    monkeypatch.setattr(media, 'asr_configured', lambda: True)
    monkeypatch.setattr(media, 'extract_audio', lambda path, folder: path)
    monkeypatch.setattr(media, 'probe', lambda path: 1.0)
    monkeypatch.setattr(media, 'transcribe', transcribe)
    monkeypatch.setattr(media, 'frames', lambda *args: ['frame-1.png', 'frame-2.png'])
    monkeypatch.setattr(ocr, 'extract', failing_ocr)
    # 用视频后缀走 media 分支；文档最终应 ready 且片段来自转写
    resp = _upload_raw(admin, kid, 'sample.mp4', b'\0' * 16, 'video/mp4')
    assert resp.status_code == 201
    doc = finish_upload(admin, resp.json())
    assert doc['status'] == 'ready', doc
    assert seen == ['frame-1.png', 'frame-2.png']
    assert doc['media_meta']['frames'] == 2
    assert any('转写文本' in chunk['text'] for chunk in doc['chunks'])

# 以下为"依赖磁盘样本"的用例模板，样本落盘后取消注释并改 _sample() 路径。
#
# def test_pdf_two_pages(admin):
#     kid = kb(admin)
#     raw = _sample('pdf/pages.pdf')
#     resp = _upload_raw(admin, kid, 'pages.pdf', raw, 'application/pdf')
#     assert resp.status_code == 201
#     doc = finish_upload(admin, resp.json())
#     assert doc['page_count'] == 2
#     assert {c['page'] for c in doc['chunks']} == {1, 2}
#
# def test_pptx_two_slides(admin):
#     kid = kb(admin)
#     raw = _sample('pptx/demo.pptx')
#     ...

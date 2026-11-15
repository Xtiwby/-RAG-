import io
import sys
import wave
from pathlib import Path

import pytest
from fastapi import HTTPException

from test_api import admin, application_lifespan, client, finish_upload, kb, wait_job
from app import media, ocr, rag


def wav_bytes(seconds=2.0, rate=8000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b'\x00\x00' * int(rate * seconds))
    return buffer.getvalue()


async def fake_transcribe(path):
    return '语音验收内容', [{'start': 0.0, 'end': 1.5, 'text': '语音验收内容'},
                            {'start': 1.5, 'end': 2.0, 'text': '第二段内容'}], 'test-asr'


def submit(admin, kid, name, content, mime):
    response = client.post('/api/documents', headers=admin, data={'knowledge_base_id': kid},
                           files={'file': (name, content, mime)})
    assert response.status_code == 201, response.text
    return response.json()


def test_audio_upload_transcribes_into_timestamped_chunks(admin, monkeypatch):
    monkeypatch.setattr(media, 'asr_configured', lambda: True)
    monkeypatch.setattr(media, 'transcribe', fake_transcribe)
    doc = finish_upload(admin, submit(admin, kb(admin), '访谈录音.wav', wav_bytes(), 'audio/wav'))
    assert doc['status'] == 'ready' and doc['modality'] == 'audio', doc
    assert doc['chunks'][0]['section'].startswith('00:00:00')
    assert '语音验收内容' in doc['chunks'][0]['text']
    assert '第二段内容' in [chunk['text'] for chunk in doc['chunks']]
    assert doc['media_meta']['kind'] == 'audio'
    assert doc['media_meta']['asr_model'] == 'test-asr'
    assert doc['media_meta']['duration_seconds'] >= 1.9


def test_audio_without_service_waits_for_explicit_transcription(admin, monkeypatch):
    monkeypatch.setattr(media, 'asr_configured', lambda: False)
    doc = finish_upload(admin, submit(admin, kb(admin), '待转写.wav', wav_bytes(), 'audio/wav'))
    assert doc['status'] == 'needs_extraction'
    path = f"/api/documents/{doc['id']}"
    state = client.get(path + '/processing', headers=admin).json()
    assert state['can_continue_transcription'] and not state['can_continue_vision']
    assert '开始语音转写' in doc['parse_error']
    assert client.post(path + '/transcribe', headers=admin).status_code == 503

    monkeypatch.setattr(media, 'asr_configured', lambda: True)
    monkeypatch.setattr(media, 'transcribe', fake_transcribe)
    submitted = client.post(path + '/transcribe', headers=admin)
    assert submitted.status_code == 202, submitted.text
    monkeypatch.setattr(media, 'asr_configured', lambda: True)
    assert wait_job(admin, submitted.json()['job_id'])['status'] == 'completed'
    updated = client.get(path, headers=admin).json()
    assert updated['status'] == 'ready' and '语音验收内容' in updated['chunks'][0]['text']


def test_video_uses_extracted_audio(admin, monkeypatch, tmp_path):
    extracted = {}

    def fake_extract(path, folder):
        target = Path(folder) / 'audio.mp3'
        target.write_bytes(b'audio-bytes')
        extracted['called'] = True
        return target

    monkeypatch.setattr(media, 'asr_configured', lambda: True)
    monkeypatch.setattr(media, 'extract_audio', fake_extract)
    monkeypatch.setattr(media, 'probe', lambda path: 42.0)
    monkeypatch.setattr(media, 'transcribe', fake_transcribe)
    doc = finish_upload(admin, submit(admin, kb(admin), '演示视频.mp4', b'\x00' * 2048, 'video/mp4'))
    assert extracted.get('called'), doc
    assert doc['status'] == 'ready' and doc['modality'] == 'video', doc
    assert doc['media_meta']['kind'] == 'video' and doc['media_meta']['audio_extracted']
    assert len(doc['chunks']) >= 2


def test_video_without_ffmpeg_reports_actionable_error(admin, monkeypatch):
    monkeypatch.setattr(media, 'asr_configured', lambda: True)
    monkeypatch.setattr(media, 'ffmpeg', lambda: None)
    monkeypatch.setattr(media, 'ffprobe', lambda: None)
    doc = finish_upload(admin, submit(admin, kb(admin), '无ffmpeg.mp4', b'\x00' * 1024, 'video/mp4'))
    assert doc['status'] == 'parse_failed'
    assert 'ffmpeg' in doc['parse_error']
    assert client.get(f"/api/documents/{doc['id']}/processing", headers=admin).json()['can_continue_transcription']


def test_overlong_media_is_rejected(monkeypatch, tmp_path):
    import asyncio
    from app import settings
    path = tmp_path / 'long.wav'
    path.write_bytes(wav_bytes(1))
    config = settings.get().model_copy(update={'media': settings.MediaSettings(max_duration_seconds=60)})
    monkeypatch.setattr(settings, 'get', lambda: config)
    monkeypatch.setattr(media, 'probe', lambda target: 3600.0)
    with pytest.raises(ValueError, match='上限'):
        asyncio.run(media.units(path, 'wav'))


def test_duration_is_parsed_from_the_ffmpeg_banner():
    assert media._parse_duration('Duration: 00:01:23.45, start: 0.000000, bitrate: 128 kb/s') == 83.45
    assert media._parse_duration('no duration here') == 0.0


def test_bmp_image_is_normalized_to_png_before_ocr(admin, monkeypatch):
    from PIL import Image
    image = io.BytesIO()
    Image.new('RGB', (12, 12), 'white').save(image, format='BMP')
    doc = finish_upload(admin, submit(admin, kb(admin), '扫描件.bmp', image.getvalue(), 'image/bmp'))
    assert doc['status'] == 'needs_extraction'
    seen = {}

    async def recognized(messages):
        seen['url'] = messages[1]['content'][1]['image_url']['url']
        yield 'BMP 识别结果'

    monkeypatch.setattr(rag, 'llm_stream', recognized)
    submitted = client.post(f"/api/documents/{doc['id']}/vision-processing", headers=admin)
    assert submitted.status_code == 202, submitted.text
    assert wait_job(admin, submitted.json()['job_id'])['status'] == 'completed'
    assert seen['url'].startswith('data:image/png;base64,')
    assert 'BMP 识别结果' in client.get(f"/api/documents/{doc['id']}", headers=admin).json()['chunks'][0]['text']


def test_local_ocr_provider_requires_optional_package(monkeypatch):
    monkeypatch.setitem(sys.modules, 'rapidocr_onnxruntime', None)
    monkeypatch.setattr(ocr, '_engine', None)
    with pytest.raises(HTTPException) as info:
        ocr.local_engine()
    assert info.value.status_code == 503
    assert 'rapidocr' in info.value.detail

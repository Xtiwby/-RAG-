"""Audio and video ingestion: ffmpeg probing, optional frame OCR and ASR.

Only an OpenAI-compatible ``/audio/transcriptions`` endpoint is used. The
service address and model live in runtime settings, the key stays in
``RAG_ASR_API_KEY``.
"""
import asyncio
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import httpx

from . import settings

AUDIO_TYPES = {'mp3', 'm4a', 'wav', 'ogg', 'flac', 'aac'}
VIDEO_TYPES = {'mp4', 'mov', 'mkv', 'webm', 'avi'}
MEDIA_TYPES = AUDIO_TYPES | VIDEO_TYPES


def media_type(suffix):
    return 'video' if suffix in VIDEO_TYPES else 'audio' if suffix in AUDIO_TYPES else ''


def ffmpeg():
    configured = os.getenv('RAG_FFMPEG_PATH', '').strip()
    if configured:
        found = shutil.which(configured) or (configured if Path(configured).is_file() else None)
        if found:
            return found
    found = shutil.which('ffmpeg') or shutil.which('ffmpeg.exe')
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def ffprobe():
    executable = ffmpeg()
    if not executable:
        return None
    candidate = Path(executable).with_name(Path(executable).stem.replace('ffmpeg', 'ffprobe') + Path(executable).suffix)
    if candidate.is_file():
        return str(candidate)
    return shutil.which('ffprobe')


def _run(command, timeout=180):
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, **options)


def _missing_ffmpeg():
    return ValueError('未找到 ffmpeg，请安装并加入 PATH（choco install ffmpeg），或在 backend/.env 设置 RAG_FFMPEG_PATH')


def _parse_duration(text):
    for token in text.split():
        token = token.strip(',;()[]')
        if token.count(':') == 2:
            hours, minutes, seconds = token.split(':')
            try:
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            except ValueError:
                continue
    return 0.0


def probe(path):
    """Return the media duration in seconds; WAV files need no ffmpeg."""
    path = Path(path)
    if path.suffix.lower() == '.wav':
        try:
            with wave.open(str(path)) as stream:
                if stream.getframerate():
                    return stream.getnframes() / stream.getframerate()
        except (wave.Error, EOFError):
            pass
    executable = ffprobe()
    if executable:
        result = _run([executable, '-v', 'error', '-show_entries', 'format=duration',
                       '-of', 'default=noprint_wrappers=1:nokey=1', str(path)], timeout=60)
        try:
            return float(result.stdout.decode('utf-8', 'ignore').strip())
        except ValueError:
            return 0.0
    executable = ffmpeg()
    if not executable:
        raise _missing_ffmpeg()
    result = _run([executable, '-hide_banner', '-i', str(path)], timeout=60)
    return _parse_duration(result.stderr.decode('utf-8', 'ignore'))


def extract_audio(path, folder):
    """Extract a mono 16 kHz mp3 track; videos must go through here."""
    executable = ffmpeg()
    if not executable:
        raise _missing_ffmpeg()
    target = Path(folder) / 'audio.mp3'
    result = _run([executable, '-y', '-i', str(path), '-vn', '-ac', '1', '-ar', '16000',
                   '-b:a', '64k', str(target)])
    if result.returncode != 0 or not target.is_file() or not target.stat().st_size:
        raise ValueError('无法从视频中提取音轨，请确认文件包含音频且格式未损坏')
    return target


def frames(path, folder, interval, maximum):
    """Sample at most ``maximum`` stills, one every ``interval`` seconds."""
    executable = ffmpeg()
    if not executable:
        raise _missing_ffmpeg()
    target = Path(folder) / 'frames'
    target.mkdir(exist_ok=True)
    result = _run([executable, '-y', '-i', str(path), '-vf', f'fps=1/{int(interval)}',
                   '-frames:v', str(int(maximum)), '-q:v', '3', str(target / '%03d.jpg')])
    if result.returncode != 0:
        raise ValueError('视频抽帧失败，请检查文件是否可正常播放')
    return sorted(target.glob('*.jpg'))


def stamp(seconds):
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def asr_configured():
    config = settings.get().asr
    if config.provider=='local':
        from . import local_asr
        return local_asr.available()
    return bool(config.provider == 'api' and config.base_url and config.model
                and os.getenv('RAG_ASR_API_KEY', '').strip())


def _asr_error(status):
    return {401: '语音转写密钥无效', 402: '语音转写余额不足', 403: '语音转写访问被拒绝',
            429: '语音转写限流或额度不足'}.get(status, f'语音转写服务返回 HTTP {status}')


async def transcribe(path):
    """Return ``(text, segments, model)`` from an OpenAI-compatible ASR service."""
    config = settings.get().asr
    if config.provider=='local':
        from . import local_asr
        return await local_asr.transcribe(path,config)
    key = os.getenv('RAG_ASR_API_KEY', '').strip()
    if not asr_configured():
        raise RuntimeError('尚未配置语音转写服务，请在 backend/.env 设置 RAG_ASR_BASE_URL、RAG_ASR_MODEL 和 RAG_ASR_API_KEY')
    data = {'model': config.model, 'response_format': 'verbose_json'}
    if config.language:
        data['language'] = config.language
    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            with Path(path).open('rb') as stream:
                response = await client.post(config.base_url + '/audio/transcriptions',
                                             headers={'Authorization': f'Bearer {key}'},
                                             files={'file': (Path(path).name, stream, 'application/octet-stream')},
                                             data=data)
            if response.status_code >= 400:
                raise RuntimeError(_asr_error(response.status_code))
            payload = response.json()
    except httpx.TimeoutException:
        raise RuntimeError('语音转写超时，请缩短音频或稍后重试') from None
    except httpx.RequestError:
        raise RuntimeError('无法连接语音转写服务，请检查网络与服务地址') from None
    segments = []
    for segment in payload.get('segments') or []:
        text = str(segment.get('text') or '').strip()
        if text:
            segments.append({'start': float(segment.get('start') or 0), 'end': float(segment.get('end') or 0), 'text': text})
    text = str(payload.get('text') or '').strip()
    if not segments and text:
        segments = [{'start': 0.0, 'end': 0.0, 'text': text}]
    return text, segments, config.model


def units_from_segments(segments):
    units = []
    for index, segment in enumerate(segments):
        end = segment['end'] or segment['start']
        label = stamp(segment['start']) if not end or end <= segment['start'] else f"{stamp(segment['start'])} – {stamp(end)}"
        units.append({'page': index + 1, 'section': label, 'text': segment['text']})
    return units


async def units(path, suffix, progress=None):
    """Parse one audio/video original into text units plus media metadata."""
    from . import ocr

    config = settings.get()
    kind = media_type(suffix)
    meta = {'kind': kind}
    folder = tempfile.mkdtemp(prefix='rag-media-')
    try:
        source = Path(path)
        if kind == 'video':
            source = await asyncio.to_thread(extract_audio, source, folder)
            meta['audio_extracted'] = True
        duration = await asyncio.to_thread(probe, source)
        meta['duration_seconds'] = round(duration, 2)
        if duration and duration > config.media.max_duration_seconds:
            raise ValueError(f'音视频时长超过 {config.media.max_duration_seconds} 秒上限，请分段后上传')
        if progress:
            progress(0, 3)
        text, segments, model = await transcribe(source)
        meta['asr_model'] = model
        if progress:
            progress(2, 3)
        result = units_from_segments(segments)
        if kind == 'video' and config.media.frame_ocr:
            stills = await asyncio.to_thread(frames, path, folder, config.media.frame_interval_seconds, config.media.max_frames)
            meta['frames'] = len(stills)
            for index, still in enumerate(stills):
                seconds = index * config.media.frame_interval_seconds
                try:
                    result.extend(await ocr.extract(still, section=f'视频画面 {stamp(seconds)}'))
                except (RuntimeError, ValueError):
                    # A single unreadable frame must not discard the transcript.
                    continue
        if not result:
            raise ValueError('语音转写没有返回可索引文本')
        if progress:
            progress(3, 3)
        return result, meta
    finally:
        shutil.rmtree(folder, ignore_errors=True)

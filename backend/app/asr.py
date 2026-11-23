"""Replaceable speech-to-text providers.

Local inference is the default deployment target.  Models must be prepared
before the worker starts so an upload never triggers a network download.
The cloud provider keeps compatibility with OpenAI-style
``/audio/transcriptions`` services.
"""
import asyncio
import importlib.util
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from . import settings


MODEL_ROOT = Path(__file__).resolve().parents[1] / 'models' / 'faster-whisper'
LOCAL_MODELS = {'tiny', 'base', 'small', 'medium', 'large-v3', 'turbo'}
LEGACY_TINY = 'Systran/faster-whisper-tiny'
_lock = threading.Lock()
_loaded = {}
_state = {}


class ASRProvider(ABC):
    """Common speech-to-text contract used by media ingestion."""

    @abstractmethod
    def configured(self, config):
        raise NotImplementedError

    @abstractmethod
    async def transcribe(self, path, config):
        """Return ``(text, segments, provider_model)``."""
        raise NotImplementedError


def local_model_path(model):
    if model == LEGACY_TINY:
        legacy = Path(__file__).resolve().parents[1] / 'models' / 'faster-whisper-tiny'
        return legacy if legacy.exists() else MODEL_ROOT / 'tiny'
    if model not in LOCAL_MODELS:
        raise RuntimeError('本地语音转写模型不受支持')
    return MODEL_ROOT / model


def _resolve_device(config):
    if config.device != 'auto':
        return config.device
    try:
        import ctranslate2
        return 'cuda' if ctranslate2.get_cuda_device_count() else 'cpu'
    except (ImportError, RuntimeError):
        return 'cpu'


def _compute_type(config, device):
    if config.compute_type != 'auto':
        return config.compute_type
    return 'float16' if device == 'cuda' else 'int8'


def _load_local(config):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError('本地语音转写依赖未安装，请运行 pip install -r requirements-asr.txt') from None
    path = local_model_path(config.model)
    if not (path / 'model.bin').is_file():
        raise RuntimeError(f'本地语音模型 {config.model} 未准备，请运行 prepare_asr_model.py --model {config.model}')
    requested = _resolve_device(config)
    attempts = [requested]
    if config.device == 'auto' and requested == 'cuda':
        attempts.append('cpu')
    error = None
    for device in attempts:
        compute = _compute_type(config, device)
        key = (config.model, device, compute)
        try:
            if key not in _loaded:
                _loaded.clear()
                _loaded[key] = WhisperModel(str(path), device=device, compute_type=compute,
                                            local_files_only=True)
            _state.update({'ready': True, 'model': config.model, 'device': device,
                           'compute_type': compute, 'fallback': device != requested})
            return _loaded[key], device, compute
        except Exception as caught:
            error = caught
    raise RuntimeError('本地语音模型加载失败，请检查模型文件、CUDA运行库或改用CPU模式') from error


def _transcribe_once(path, config):
    model, device, _ = _load_local(config)
    language = config.language.strip() or None
    values, info = model.transcribe(str(path), language=language,
                                    beam_size=config.beam_size, vad_filter=True)
    segments = []
    for value in values:
        text = str(value.text or '').strip()
        if text:
            segments.append({'start': float(value.start or 0), 'end': float(value.end or 0), 'text': text})
    text = ''.join(segment['text'] for segment in segments).strip()
    detected = getattr(info, 'language', '') or language or ''
    _state.update({'language': detected, 'device': device})
    return text, segments, f'faster-whisper:{config.model}'


def _local_transcribe(path, config):
    with _lock:
        try:
            return _transcribe_once(path, config)
        except RuntimeError as error:
            message = str(error).lower()
            cuda_failure = any(token in message for token in ('cuda', 'cublas', 'cudnn', 'cufft'))
            if config.device != 'auto' or not cuda_failure:
                raise
            _loaded.clear()
            fallback = config.model_copy(update={'device': 'cpu', 'compute_type': 'int8'})
            result = _transcribe_once(path, fallback)
            _state['fallback'] = True
            return result


class FasterWhisperProvider(ASRProvider):
    def configured(self, config):
        try:
            path = local_model_path(config.model)
        except RuntimeError:
            return False
        return importlib.util.find_spec('faster_whisper') is not None and (path / 'model.bin').is_file()

    async def transcribe(self, path, config):
        try:
            return await asyncio.wait_for(asyncio.to_thread(_local_transcribe, path, config),
                                          config.timeout_seconds)
        except TimeoutError:
            raise RuntimeError('本地语音转写超时，请缩短音频、使用更小模型或检查推理设备') from None


def _api_error(response):
    code = ''
    message = ''
    try:
        error = response.json().get('error') or {}
        code = str(error.get('code') or error.get('type') or '')
        message = str(error.get('message') or '')
    except (ValueError, AttributeError):
        pass
    if code in {'credit_balance_exhausted', 'insufficient_quota'}:
        return '语音转写服务余额不足'
    if code in {'project_spend_limit_exceeded', 'organization_spend_limit_exceeded',
                'organization_usage_limit_exceeded'}:
        return '语音转写服务已达到项目或组织使用上限'
    if response.status_code == 429:
        return '语音转写服务请求过快，请稍后重试' if 'rate' in message.lower() else '语音转写限流或额度不足'
    return {401: '语音转写密钥无效', 402: '语音转写余额不足',
            403: '语音转写访问被拒绝', 404: '语音转写接口或模型不存在'}.get(
                response.status_code, f'语音转写服务返回 HTTP {response.status_code}')


class OpenAICompatibleProvider(ASRProvider):
    def configured(self, config):
        return bool(config.base_url and config.model and os.getenv('RAG_ASR_API_KEY', '').strip())

    async def transcribe(self, path, config):
        key = os.getenv('RAG_ASR_API_KEY', '').strip()
        if not self.configured(config):
            raise RuntimeError('云端语音转写需要配置服务地址、模型和 RAG_ASR_API_KEY')
        # Newer gpt-*-transcribe models accept ``json`` but not the legacy
        # ``verbose_json`` format. Whisper-compatible services generally keep
        # the timestamped legacy response, so select the safest format by model.
        response_format = 'json' if 'transcribe' in config.model.lower() else 'verbose_json'
        data = {'model': config.model, 'response_format': response_format}
        if config.language:
            data['language'] = config.language
        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                with Path(path).open('rb') as stream:
                    response = await client.post(config.base_url + '/audio/transcriptions',
                        headers={'Authorization': f'Bearer {key}'},
                        files={'file': (Path(path).name, stream, 'application/octet-stream')}, data=data)
                if response.status_code >= 400:
                    raise RuntimeError(_api_error(response))
                payload = response.json()
        except httpx.TimeoutException:
            raise RuntimeError('语音转写超时，请缩短音频或稍后重试') from None
        except httpx.RequestError:
            raise RuntimeError('无法连接语音转写服务，请检查网络与服务地址') from None
        segments = []
        for segment in payload.get('segments') or []:
            text = str(segment.get('text') or '').strip()
            if text:
                segments.append({'start': float(segment.get('start') or 0),
                                 'end': float(segment.get('end') or 0), 'text': text})
        text = str(payload.get('text') or '').strip()
        if not segments and text:
            segments = [{'start': 0.0, 'end': 0.0, 'text': text}]
        return text, segments, config.model


class DisabledProvider(ASRProvider):
    def configured(self, config):
        return False

    async def transcribe(self, path, config):
        raise RuntimeError('语音转写已关闭，请在系统配置中选择本地或云端转写')


def provider(config=None):
    config = config or settings.get().asr
    return {'local': FasterWhisperProvider, 'api': OpenAICompatibleProvider,
            'disabled': DisabledProvider}[config.provider]()


def configured(config=None):
    config = config or settings.get().asr
    return provider(config).configured(config)


async def transcribe(path, config=None):
    config = config or settings.get().asr
    return await provider(config).transcribe(path, config)


def status(config=None):
    config = config or settings.get().asr
    result = {'provider': config.provider, 'configured': configured(config), 'model': config.model}
    if config.provider == 'local':
        result.update(_state)
        result['model_path'] = str(local_model_path(config.model)) if config.model in LOCAL_MODELS | {LEGACY_TINY} else ''
    return result

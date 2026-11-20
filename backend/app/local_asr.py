"""Optional offline Whisper transcription; no request-time downloads."""
import asyncio
import threading
from pathlib import Path
from . import telemetry

ROOT=Path(__file__).resolve().parents[1]/'models/faster-whisper-tiny'
_lock=threading.Lock()
_engine=None


def available():
    return all((ROOT/name).is_file() for name in ('model.bin','config.json','tokenizer.json'))


def infer(path,language):
    global _engine
    if not available():
        raise RuntimeError('本地语音模型未准备完整，请先下载 Systran/faster-whisper-tiny 到 backend/models/faster-whisper-tiny')
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError('本地转写需要安装 requirements-asr.txt') from None
    with _lock:
        if _engine is None:
            _engine=WhisperModel(str(ROOT),device='cpu',compute_type='int8',cpu_threads=4,local_files_only=True)
        segments,_=_engine.transcribe(str(path),language=language or None,beam_size=5,vad_filter=False)
        result=[{'start':float(x.start),'end':float(x.end),'text':x.text.strip()} for x in segments if x.text.strip()]
        return ' '.join(x['text'] for x in result),result,'Systran/faster-whisper-tiny'


async def transcribe(path,config):
    call=telemetry.begin('transcription','Systran/faster-whisper-tiny')
    try:
        result=await asyncio.wait_for(asyncio.to_thread(infer,path,config.language),config.timeout_seconds)
        telemetry.finish(call,'success',characters=len(result[0]))
        return result
    except asyncio.CancelledError:
        telemetry.finish(call,'interrupted','本地转写已中断');raise
    except Exception:
        telemetry.finish(call,'failure','本地转写失败，请检查模型文件和运行日志');raise

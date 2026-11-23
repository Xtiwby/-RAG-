"""Download a selected faster-whisper model before starting the worker."""
import argparse
from pathlib import Path

from faster_whisper.utils import download_model

from app.asr import LOCAL_MODELS, MODEL_ROOT


def prepare(model):
    if model not in LOCAL_MODELS:
        raise SystemExit('模型可选：' + '、'.join(sorted(LOCAL_MODELS)))
    target = MODEL_ROOT / model
    target.mkdir(parents=True, exist_ok=True)
    download_model(model, output_dir=str(target))
    if not (target / 'model.bin').is_file():
        raise SystemExit('模型下载不完整：缺少 model.bin')
    print(f'本地语音模型已就绪：{model} -> {target}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='small', choices=sorted(LOCAL_MODELS))
    prepare(parser.parse_args().model)

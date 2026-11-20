"""Real ffmpeg audio/video decoding and offline ASR on synthetic speech."""
import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-results/remaining-2026-11-19/media'


async def run():
    from app import media,settings,presentation_preview,report_export,blueprints,rag
    config=settings.defaults();config.asr=settings.AsrSettings(provider='local',model='Systran/faster-whisper-tiny',language='en')
    settings.active.set(config)
    ffmpeg=media.ffmpeg();assert ffmpeg
    video=OUT/'speech.mp4'
    command=[ffmpeg,'-y','-f','lavfi','-i','color=c=white:s=640x360:r=1','-i',str(OUT/'speech.wav'),'-c:v','libx264','-c:a','aac','-shortest',str(video)]
    result=media._run(command);assert result.returncode==0,result.stderr.decode(errors='replace')[-1000:]
    results=[]
    for path in (OUT/'speech.wav',video):
        start=time.perf_counter();units,meta=await media.units(path,path.suffix.lstrip('.'))
        text=' '.join(unit['text'] for unit in units).lower()
        passed='cooling' in text and 'power' in text
        results.append({'file':path.name,'units':units,'meta':meta,'seconds':time.perf_counter()-start,'pass':passed})
        print('Real media',path.name,passed,flush=True)
    word=OUT/'mixed-layout.docx'
    word.write_bytes(report_export.docx('# 知源格式验收\n\n中文与 English mixed text，数值为 21 天。\n\n## 引用来源\n\n设备采用被动散热。[1]',blueprints.ReportFormat()))
    executable=presentation_preview._soffice();assert executable
    converted=media._run([executable,'--headless','--norestore','--convert-to','pdf','--outdir',str(OUT),str(word)])
    assert converted.returncode==0 and word.with_suffix('.pdf').exists()
    units=rag.parse_local(word.with_suffix('.pdf'),'pdf')
    text=''.join(unit['text'] for unit in units)
    assert '知源' in text and 'English' in text and '21' in text
    result={'pass':all(item['pass'] for item in results),'media':results,'libreoffice_pdf_pass':True,'office_pages':len(units)}
    (OUT.parent/'real-media-office.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['pass']


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='rag-real-media-') as folder:
        os.environ.update(PYTHON_DOTENV_DISABLED='1',RAG_DATA_DIR=folder,RAG_DATABASE_URL='',RAG_FILE_STORAGE='local')
        asyncio.run(run())

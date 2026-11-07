"""Render PowerPoint originals as PDFs for the document viewer."""

import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from threading import BoundedSemaphore

from fastapi import HTTPException

from . import object_storage


POWERPOINT_TYPES = {'ppt', 'pptx'}
_slots = BoundedSemaphore(2)
_profile_settings = '''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
  </item>
</oor:items>'''


def _soffice():
    configured = os.getenv('RAG_SOFFICE_PATH', '').strip()
    if configured:
        return shutil.which(configured)

    if os.name == 'nt':
        portable = Path(__file__).resolve().parents[1] / '.model-tools' / 'libreoffice'
        roots = [portable, portable / 'LibreOffice']
        roots.extend(Path(value) / 'LibreOffice' for key in
                     ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)')
                     if (value := os.environ.get(key)))
        for root in roots:
            candidate = root / 'program' / 'soffice.com'
            if candidate.is_file():
                return str(candidate)
        return shutil.which('soffice.com') or shutil.which('soffice.exe')

    return shutil.which('soffice') or shutil.which('libreoffice')


def _convert(command, workdir):
    options = ({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt'
               else {'start_new_session': True})
    with subprocess.Popen(command, cwd=workdir, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          **options) as process:
        try:
            return process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            # The console launcher starts another process to render the file.
            if os.name == 'nt':
                taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'taskkill.exe'
                try:
                    subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if process.poll() is None:
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            raise


def render(document):
    if document['file_type'] not in POWERPOINT_TYPES:
        raise HTTPException(415, '此接口只支持 PPT 和 PPTX 预览。')
    if not object_storage.exists(document):
        raise HTTPException(404, '找不到原始幻灯片文件。')
    executable = _soffice()
    if not executable:
        raise HTTPException(503, '幻灯片预览需要后端安装 LibreOffice。可先下载原文件查看。')
    if not _slots.acquire(blocking=False):
        raise HTTPException(503, '幻灯片预览正在处理其他文件，请稍后重试。')

    try:
        with tempfile.TemporaryDirectory(prefix='rag-slide-preview-') as directory:
            workdir = Path(directory).resolve()
            profile = workdir / 'profile'
            (profile / 'user').mkdir(parents=True)
            (profile / 'user' / 'registrymodifications.xcu').write_text(_profile_settings, encoding='utf-8')
            source = workdir / ('slides.' + document['file_type'])
            with object_storage.local_path(document) as original:
                shutil.copyfile(original, source)

            output = workdir / 'slides.pdf'
            pdf_filter = 'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}'
            command = [executable, '-env:UserInstallation=' + profile.as_uri(),
                       '--headless', '--norestore', '--convert-to', pdf_filter,
                       '--outdir', str(workdir), str(source)]
            if _convert(command, workdir) or not output.is_file():
                raise HTTPException(422, '无法生成幻灯片预览，文件可能已加密或损坏。')
            if output.stat().st_size > 100 * 1024 * 1024:
                raise HTTPException(422, '幻灯片预览过大，请下载原文件查看。')
            data = output.read_bytes()
            if not data.startswith(b'%PDF-'):
                raise HTTPException(422, '生成的幻灯片预览无法读取。')
            return data
    except subprocess.TimeoutExpired:
        raise HTTPException(504, '生成幻灯片预览超时，请重试。') from None
    except FileNotFoundError:
        raise HTTPException(503, '原文件或幻灯片转换器暂不可用，请重试。') from None
    except OSError:
        raise HTTPException(503, '幻灯片预览暂不可用，请重试。') from None
    finally:
        _slots.release()

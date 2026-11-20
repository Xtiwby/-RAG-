"""Page-preserving OCR for scanned and mixed PDFs, with bounded raster memory."""
import asyncio
import tempfile
from pathlib import Path

from . import ocr, settings


def uniform_page(path):
    from PIL import Image
    with Image.open(path) as image:
        low,high=image.convert('L').getextrema()
        return high-low<=2


def render_page(source, index, target):
    import pypdfium2 as pdfium
    from .citation_images import _pdfium_lock
    with _pdfium_lock, pdfium.PdfDocument(str(source)) as document:
        page = document[index]
        try:
            width, height = page.get_size()
            scale = min(2.5, (4_000_000 / max(1, width * height)) ** .5)
            bitmap = page.render(scale=scale)
            try:
                bitmap.to_pil().save(target, format='PNG')
            finally:
                bitmap.close()
        finally:
            page.close()


async def extract(source, units, authorize, progress=None):
    """Keep text layers unchanged, OCR only pages without usable text."""
    limit = settings.get().ocr.pdf_max_pages
    if len(units) > limit:
        raise ValueError(f'PDF 共 {len(units)} 页，超过单次 OCR {limit} 页上限，请拆分后重试')
    result = []
    with tempfile.TemporaryDirectory(prefix='rag-pdf-ocr-') as folder:
        target = Path(folder) / 'page.png'
        for index, unit in enumerate(units):
            authorize()
            if unit['text'].strip():
                result.append(unit)
            else:
                await asyncio.to_thread(render_page, source, index, target)
                authorize()
                if await asyncio.to_thread(uniform_page,target):
                    # Preserve the physical page without sending a blank image
                    # to OCR or treating its empty result as a failed document.
                    result.append(unit)
                    if progress:progress(index+1,len(units))
                    continue
                try:
                    extracted = await ocr.extract(target, page=index + 1,
                        section=f'第 {index + 1} 页（OCR 转录，需人工核对）')
                except RuntimeError as error:
                    raise RuntimeError(f'PDF 第 {index + 1} 页 OCR 失败：{error}') from None
                authorize()
                result.extend(extracted)
            if progress:
                progress(index + 1, len(units))
    return result

"""Capture the original source page and mark the cited text in place."""
from dataclasses import dataclass
from io import BytesIO
from threading import Lock

import pypdfium2 as pdfium
from pypdfium2 import raw
from PIL import Image, ImageDraw, ImageOps

from . import evidence_matching, object_storage, presentation_preview


# PDFium requires exclusive access even when rendering different documents.
_pdfium_lock = Lock()


@dataclass(frozen=True)
class Capture:
    label: str
    width: int
    height: int
    data: bytes


def capture(image, label):
    buffer = BytesIO()
    image.save(buffer, 'PNG', optimize=True)
    return Capture(label, image.width, image.height, buffer.getvalue())


def matched_spans(source, target):
    normalized, offsets = evidence_matching.canonical(source)

    def unique_span(needle):
        start = normalized.find(needle)
        if needle and start >= 0 and normalized.find(needle, start + 1) < 0:
            return offsets[start][0], offsets[start + len(needle) - 1][1]
        return None

    normalized_target, _ = evidence_matching.canonical(target)
    if span := unique_span(normalized_target):
        return [span]
    if not normalized_target or normalized_target in normalized:
        return []

    # Reading order may differ on multi-column pages. Only mark substantial
    # lines that occur once, and require most of the excerpt to match.
    matched = []
    lines = {evidence_matching.canonical(line)[0] for line in target.splitlines()}
    for line in lines:
        if len(line) >= 10 and (span := unique_span(line)):
            matched.append(span)
    merged = []
    for start, end in sorted(matched):
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(merged[-1][1], end)
        else:
            merged.append((start, end))
    covered = sum(len(evidence_matching.canonical(source[start:end])[0]) for start, end in merged)
    return merged if covered >= 0.65 * len(normalized_target) else []


def highlight_boxes(page, bitmap, text):
    text_page = page.get_textpage()
    try:
        source = ''.join(chr(raw.FPDFText_GetUnicode(text_page, index)) for index in range(text_page.count_chars()))
        coordinates = bitmap.get_posconv(page)
        boxes = []
        for start, end in matched_spans(source, text):
            for index in range(text_page.count_rects(start, end - start)):
                left, bottom, right, top = text_page.get_rect(index)
                x1, y1 = coordinates.to_bitmap(left, top)
                x2, y2 = coordinates.to_bitmap(right, bottom)
                boxes.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
        return boxes
    finally:
        text_page.close()


def pdf_capture(path, page_number, text, presentation=False):
    with _pdfium_lock, pdfium.PdfDocument(path) as document:
        if not 1 <= page_number <= len(document):
            return None
        page = document[page_number - 1]
        bitmap = page.render(scale=1800 / max(page.get_size()))
        try:
            image = bitmap.to_pil().convert('RGB')
            try:
                boxes = highlight_boxes(page, bitmap, text)
            except (pdfium.PdfiumError, ValueError):
                boxes = []
        finally:
            bitmap.close()
    draw = ImageDraw.Draw(image, 'RGBA')
    for box in boxes:
        draw.rectangle(box, fill=(255, 212, 0, 85))
    label = f'原版幻灯片 · 第 {page_number} 张' if presentation else f'原版 PDF · 第 {page_number} 页'
    return capture(image, label)


def original_capture(document, chunk, presentation_pdf):
    page = chunk.get('page')
    file_type = document['file_type']
    try:
        if page and file_type == 'pdf':
            with object_storage.local_path(document) as path:
                return pdf_capture(path, page, chunk['text'])
        if page and file_type in presentation_preview.POWERPOINT_TYPES:
            if path := presentation_pdf(document['id']):
                return pdf_capture(path, page, chunk['text'], presentation=True)
        if file_type in {'png', 'jpg', 'jpeg', 'webp'}:
            with object_storage.local_path(document) as path, Image.open(path) as original:
                image = ImageOps.exif_transpose(original)
                image.thumbnail((1800, 1800))
                return capture(image.convert('RGB'), '原始图片')
    except (OSError, ValueError, RuntimeError, pdfium.PdfiumError, Image.DecompressionBombError):
        return None
    return None

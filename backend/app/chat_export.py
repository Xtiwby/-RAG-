"""Export questions, answers and original source pages as a Word document."""
from functools import lru_cache
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from fastapi import HTTPException

from . import citation_images, presentation_preview, report_export, store
from .blueprints import ReportFormat


REFERENCE = re.compile(r'\[([0-9]{1,9})\]')


def export(messages, read_document, *, title='', knowledge_bases=()):
    with TemporaryDirectory(prefix='rag-chat-export-') as directory:
        @lru_cache(maxsize=None)
        def presentation_pdf(document_id):
            try:
                path = Path(directory) / f'{document_id}.pdf'
                path.write_bytes(presentation_preview.render(read_document(document_id)))
                return path
            except (HTTPException, OSError):
                return None

        @lru_cache(maxsize=None)
        def load_image(document_id, chunk_id):
            try:
                document = read_document(document_id)
            except HTTPException:
                return None
            chunk = store.one('SELECT page,text FROM chunks WHERE id=? AND document_id=?', (chunk_id, document_id))
            return citation_images.original_capture(document, chunk, presentation_pdf) if chunk else None

        def original_image(citation):
            ids = [str(citation.get(key, '')) for key in ('document_id', 'chunk_id')]
            if all(re.fullmatch(r'[1-9][0-9]{0,18}', value) for value in ids):
                return load_image(*map(int, ids))
            return None

        return render(messages, original_image, title=title, knowledge_bases=knowledge_bases)


def render(messages, original_image, *, title='', knowledge_bases=()):
    lines, images = [], {}
    if title:
        lines.extend((f'# {title}', ''))
    names = [str(name).strip() for name in knowledge_bases if str(name).strip()]
    if names:
        lines.extend((f"知识库范围：{'、'.join(names)}", ''))
    turn = 0
    for message in messages:
        content = message['content'].strip()
        if not content or message['role'] not in ('user', 'assistant'):
            continue
        question = message['role'] == 'user'
        if question:
            turn += 1
        lines.append(f"## {'提问' if question else '回答'} {max(turn, 1):02d}")
        if message.get('created_at'):
            lines.append(f"消息时间：{message['created_at']}")
        lines.extend(content.splitlines())

        citations = [] if question else message['citations']
        used = sorted({int(number) for number in REFERENCE.findall(content) if 1 <= int(number) <= len(citations)})
        if not used:
            continue
        lines.append('### 引用索引')
        for number in used:
            citation = citations[number - 1]
            source = ' · '.join(filter(None, (citation.get('name'), citation.get('section'), citation.get('version'))))
            lines.append(f"[{number}] {' '.join(source.split()) or '原文'}")
            if citation.get('relevance') is not None:
                lines.append(f"相关度：{citation['relevance']}")
            snippet = str(citation.get('text') or citation.get('snippet') or '').strip()
            if snippet:
                lines.append(snippet)
            image = original_image(citation)
            if image is None:
                lines.append('此引用暂无可用的原文页面。')
            else:
                images[len(lines)] = image
                lines.append('')

    return report_export.docx('\n'.join(lines) + '\n', ReportFormat(font='等线', page_numbers=False), images=images)

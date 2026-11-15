"""Structure-aware HTML extraction shared by uploads and web data sources.

The extractor keeps what a reader would see: headings become section names,
table rows become one text block per row, list items and paragraphs stay
separate, image alt text is kept and the whole ``script``/``style`` subtree is
dropped instead of ending up in the search index.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

MAX_CHARS = 2_000_000
HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
SKIP = {'script', 'style', 'noscript', 'template', 'svg', 'iframe', 'object', 'embed', 'canvas'}
BLOCKS = {'p', 'div', 'li', 'blockquote', 'pre', 'dd', 'dt', 'section', 'article', 'aside',
          'figcaption', 'caption', 'address', 'summary', 'header', 'footer', 'main', 'nav'}
CELLS = {'td', 'th'}
SPACES = re.compile(r'[ \t\r\f\v\u00a0]+')
CHARSET = re.compile(r'(?i)<meta[^>]+charset\s*=\s*["\']?\s*([a-z0-9_\-]+)')


def normalize(text):
    text = SPACES.sub(' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def decode(raw):
    """Honour a declared charset, then fall back to UTF-8 and GB18030."""
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw.decode('utf-8-sig', errors='replace')
    for name in CHARSET.findall(raw[:4096].decode('latin-1', 'ignore')):
        name = {'gbk': 'gb18030', 'gb2312': 'gb18030', 'x-gbk': 'gb18030'}.get(name.lower(), name.lower())
        try:
            return raw.decode(name)
        except (LookupError, UnicodeDecodeError):
            continue
    for name in ('utf-8', 'gb18030'):
        try:
            return raw.decode(name)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.units = []
        self.section = '正文'
        self.title = ''
        self.buffer = []
        self.cell = None
        self.row = []
        self.skip = 0
        self.in_title = False
        self.heading = None
        self.characters = 0
        self.truncated = False

    def _add(self, text):
        if self.truncated or not text:
            return
        remaining = MAX_CHARS - self.characters
        if remaining <= 0:
            self.truncated = True
            return
        text = text[:remaining]
        self.characters += len(text)
        (self.cell if self.cell is not None else self.buffer).append(text)

    def _emit(self, text):
        text = normalize(text)
        if text:
            self.units.append({'page': None, 'section': self.section or '正文', 'text': text})

    def _flush(self):
        if self.cell is not None:
            text = normalize(''.join(self.cell))
            self.cell = None
            if text:
                self.row.append(text)
            return
        text = ''.join(self.buffer)
        self.buffer = []
        self._emit(text)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == 'title':
            self.in_title = True
        elif tag in HEADINGS:
            self._flush()
            self.heading = tag
        elif tag == 'tr':
            self._flush()
            self.row = []
        elif tag in CELLS:
            self._flush()
            self.cell = []
        elif tag == 'br':
            self._add('\n')
        elif tag == 'img':
            alt = (dict(attrs).get('alt') or '').strip()
            if alt:
                self._add(' ' + alt + ' ')
        elif tag in BLOCKS:
            self._flush()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == 'title':
            self.in_title = False
        elif tag in HEADINGS:
            text = normalize(''.join(self.buffer))
            self.buffer = []
            self.heading = None
            if text:
                self.section = text
                self._emit(text)
        elif tag in CELLS:
            self._flush()
        elif tag == 'tr':
            self._flush()
            row = [value for value in self.row if value]
            self.row = []
            if row:
                self._emit(' | '.join(row))
        elif tag in BLOCKS:
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title += data
            return
        self._add(data)

    def close(self):
        super().close()
        self._flush()


def parse_bytes(raw):
    extractor = _Extractor()
    extractor.feed(decode(raw))
    extractor.close()
    units = extractor.units
    if not units and normalize(extractor.title):
        units = [{'page': None, 'section': '正文', 'text': normalize(extractor.title)}]
    return units


def parse(path):
    return parse_bytes(Path(path).read_bytes())

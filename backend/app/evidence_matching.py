"""Conservative layout matching with offsets into the unchanged source text."""
import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from decimal import Decimal,InvalidOperation


def canonical(text):
    chars=[];spans=[]
    # Remove only an alphabetic line-wrap hyphen, never a numeric minus sign.
    wraps={m.start() for m in re.finditer(r'(?<=[A-Za-z])-\s*\n\s*(?=[a-z])',text)}
    for match in re.finditer(r'.[\u0300-\u036f]*',text,re.S):
        if match.start() in wraps:continue
        for char in unicodedata.normalize('NFKC',match.group()):
            if char.isspace() or char in '\u00ad\u200b\ufeff':continue
            chars.append(char);spans.append((match.start(),match.end()))
    return ''.join(chars),spans


def locate(quote,source):
    query,_=canonical(quote)
    if len(query)<2:return None
    text,spans=canonical(source)
    pos=text.find(query)
    if pos<0:return None
    start,end=spans[pos][0],spans[pos+len(query)-1][1]
    actual=source[start:end]
    return {'quote':actual,'char_start':start,'char_end':end,
            'match':'exact' if actual==quote else 'layout-normalized',
            'source_sha256':hashlib.sha256(source.encode()).hexdigest()}


def locate_near_exact(quote,source,threshold=.92):
    """Locate a quote with only minor OCR/punctuation errors.

    This is intentionally much stricter than semantic similarity and is used
    only by the low-latency citation-first path.  The returned quote is always
    the unchanged source substring.
    """
    exact=locate(quote,source)
    if exact:return exact
    query,_=canonical(quote);text,spans=canonical(source)
    if len(query)<8 or not text:return None
    best=None
    for size in range(max(8,len(query)-3),min(len(text),len(query)+3)+1):
        for start in range(0,len(text)-size+1):
            ratio=SequenceMatcher(None,query,text[start:start+size],autojunk=False).ratio()
            if best is None or ratio>best[0]:best=(ratio,start,size)
    if best is None or best[0]<threshold:return None
    _,pos,size=best;start,end=spans[pos][0],spans[pos+size-1][1]
    return {'quote':source[start:end],'char_start':start,'char_end':end,
            'match':'near-exact','similarity':round(best[0],4),
            'source_sha256':hashlib.sha256(source.encode()).hexdigest()}


def normalized_number(value):
    percent=value.endswith('%')
    try:return str(Decimal(value.rstrip('%')).normalize())+('%' if percent else '')
    except InvalidOperation:return value


NUMBER=r'[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?'

def numeric_text(text):
    text=unicodedata.normalize('NFKC',text).replace('−','-')
    text=re.sub(r'\[\d+\]','',text)
    text=re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))','',text)
    text=re.sub(r'(?<=[0-9%])-(?=[0-9])',' 至 ',text)
    text=re.sub(r'(?<=[0-9%])\s*[–—]\s*(?=[0-9])',' 至 ',text)
    text=re.sub(r'\btop[-\s]+(?=\d)','top ',text,flags=re.I)
    return text


def numbers(text):
    text=numeric_text(text)
    # BM25 / FTS5 / k1 / v2.0 are identifiers. The model still judges their
    # meaning; this supplemental numeric guard must not mistake them for data.
    text=re.sub(r'(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_.-]*',' ',text)
    return {normalized_number(m.group()) for m in re.finditer(r'(?<![A-Za-z0-9_.])'+NUMBER+r'(?![A-Za-z0-9_]|\.\d)',text)}


def bindings(text):
    result={}
    for key,value in re.findall(r'([A-Za-z][A-Za-z0-9_]*)\s*[:=]\s*('+NUMBER+r')',numeric_text(text)):
        result.setdefault(key.lower().replace('_',''),set()).add(normalized_number(value))
    return result


def source_years(titles):
    """Only years in cited document titles can supply a row's missing year."""
    years=set()
    for title in titles:
        normalized=unicodedata.normalize('NFKC',title)
        for match in re.finditer(r'(?<![A-Za-z0-9])(?:19|20)\d{2}(?=\s*年|[^A-Za-z0-9]|$)',normalized):
            years.add(normalized_number(match.group()))
    return years


def claim_years(text):
    normalized=unicodedata.normalize('NFKC',text)
    return {normalized_number(match.group()) for match in re.finditer(
        r'(?<![A-Za-z0-9])(?:19|20)\d{2}(?=\s*年|[^A-Za-z0-9]|$)',normalized)}


def numeric_support(claim,quotes,source_titles=()):
    source='\n'.join(quotes)
    missing=numbers(claim)-numbers(source)
    # A list row can omit the year stated in its document title. Other
    # numbers in filenames cannot substantiate numerical claims about rows.
    if missing-(claim_years(claim)&source_years(source_titles)):return False
    available=bindings(source)
    # Check associations when the quoted source expresses an explicit binding.
    return all(not available.get(key) or values<=available[key] for key,values in bindings(claim).items())

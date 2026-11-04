"""Conservative layout matching with offsets into the unchanged source text."""
import hashlib
import re
import unicodedata
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


def numeric_support(claim,quotes):
    source='\n'.join(quotes)
    if not numbers(claim)<=numbers(source):return False
    available=bindings(source)
    # Check associations when the quoted source expresses an explicit binding.
    return all(not available.get(key) or values<=available[key] for key,values in bindings(claim).items())

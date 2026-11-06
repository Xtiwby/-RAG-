"""One section-first retrieval policy shared by preflight and regeneration."""
import json
import re
from . import rag

POLICY='section-focus-v4'
GENERIC={'概述','概念与依据','背景','项目背景','技术背景','需求分析','实施方案','技术方案','总结','结论','建议','工程验证建议','待验证事项'}

def template_section_hint(structure,block_id):
    """Use fixed source/topic notes adjacent to an imported DOCX heading."""
    if not block_id or not structure:return ''
    if isinstance(structure,str):
        try:structure=json.loads(structure)
        except (TypeError,ValueError):return ''
    blocks={block.get('id'):block for block in structure.get('blocks',[])}
    order=structure.get('body_order',[])
    if block_id not in order:return ''
    notes=[]
    for ident in order[order.index(block_id)+1:]:
        block=blocks.get(ident,{})
        if block.get('type')=='heading':break
        if block.get('table') or block.get('type')!='paragraph':continue
        value=block.get('text','').strip()
        if value.startswith('来源定位：'):
            match=re.search(r'对应题目[：:]\s*(.+)',value)
            if match:notes.append('对应题目：'+match.group(1).rstrip('。'))
        elif value.startswith('本节覆盖：'):
            notes.append(value)
    return ' '.join(notes)[:800]


def scoped_requirements(requirements,title,hint=''):
    """Keep global writing constraints without repeating a focused topic in every chapter."""
    scope=(title+' '+hint).lower()
    clauses=[part.strip() for part in re.split(r'(?<=[。；;\n])',requirements or '') if part.strip()]
    result=[]
    for clause in clauses:
        if re.search(r'重点|着重|尤其|特别关注|专门讨论',clause):
            identifiers=[token.lower() for token in re.findall(r'[A-Za-z][A-Za-z0-9_]*',clause) if '_' in token]
            if identifiers and not any(token in scope for token in identifiers):
                continue
        result.append(clause)
    return ' '.join(result)[:2400]


async def retrieve_section(kb_id,spec,*,purpose='',report_title='',requirements='',period_start='',period_end='',instructions='',template_structure=None):
    title=spec['title'].strip()
    # Writing/format constraints remain in the generation prompt, not blindly
    # prepended to every section's search. Never include arbitrary user fields
    # (names, dates, owners) in every section's lexical query.
    hint=template_section_hint(template_structure,spec.get('block_id'))
    focus=' '.join(v.strip() for v in (title,hint,spec.get('instructions',''),instructions) if v.strip())[:2200]
    context=' '.join(v for v in (purpose,report_title) if v)[:700]
    contextual=title in GENERIC and not spec.get('instructions','').strip()
    query=(focus+' '+context).strip() if contextual else focus
    anchored=bool(re.search(r'对应题目[：:]\s*[^。]*[A-Za-z][A-Za-z0-9_]*',hint))
    evidence,params=await rag.retrieve(query,[kb_id],expand_queries=not anchored)
    attempts=[{'query':query,'reason':'generic-section-context' if contextual else 'section-focus','retrieval':params}]
    # Fall back only if a focused search has no eligible evidence. A successful
    # focused result cannot be drowned out by the whole report's title/policy.
    if not evidence and context and not contextual:
        fallback=(focus+' '+context).strip()
        evidence,params=await rag.retrieve(fallback,[kb_id],expand_queries=not anchored)
        attempts.append({'query':fallback,'reason':'empty-focus-fallback','retrieval':params})
    return evidence,{'policy':POLICY,'section_title':title,'template_hint':hint,'attempts':attempts,
        'period_start':period_start,'period_end':period_end,
        'note':'业务日期约束由生成和核验执行；不是按文件上传日期过滤。写作总要求仅用于生成，不与章节主题等权拼接。'}

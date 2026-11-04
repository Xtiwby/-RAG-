"""One section-first retrieval policy shared by preflight and regeneration."""
import re
from . import rag

POLICY='section-focus-v2'
GENERIC={'概述','概念与依据','背景','项目背景','技术背景','需求分析','实施方案','技术方案','总结','结论','建议','工程验证建议','待验证事项'}

async def retrieve_section(kb_id,spec,*,purpose='',report_title='',requirements='',period_start='',period_end='',instructions=''):
    title=spec['title'].strip()
    # Writing/format constraints remain in the generation prompt, not blindly
    # prepended to every section's search. Never include arbitrary user fields
    # (names, dates, owners) in every section's lexical query.
    focus=' '.join(v.strip() for v in (title,spec.get('instructions',''),instructions) if v.strip())[:2200]
    context=' '.join(v for v in (purpose,report_title) if v)[:700]
    contextual=title in GENERIC and not spec.get('instructions','').strip()
    query=(focus+' '+context).strip() if contextual else focus
    evidence,params=await rag.retrieve(query,[kb_id])
    attempts=[{'query':query,'reason':'generic-section-context' if contextual else 'section-focus','retrieval':params}]
    # Fall back only if a focused search has no eligible evidence. A successful
    # focused result cannot be drowned out by the whole report's title/policy.
    if not evidence and context and not contextual:
        fallback=(focus+' '+context).strip()
        evidence,params=await rag.retrieve(fallback,[kb_id])
        attempts.append({'query':fallback,'reason':'empty-focus-fallback','retrieval':params})
    return evidence,{'policy':POLICY,'section_title':title,'attempts':attempts,
        'period_start':period_start,'period_end':period_end,
        'note':'业务日期约束由生成和核验执行；不是按文件上传日期过滤。写作总要求仅用于生成，不与章节主题等权拼接。'}

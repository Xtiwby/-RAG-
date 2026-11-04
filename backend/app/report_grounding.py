"""Report-specific proof records. Raw candidate text is never published as a report."""
import hashlib
import json
from . import grounding,rag,store as s

POLICY='sentence-evidence-v2'

def evidence_digest(evidence):
    value=[{'reference':i,'document_id':e.get('document_id'),'chunk_id':e.get('chunk_id'),'version':e.get('version'),
            'text_sha256':hashlib.sha256(e.get('text',e.get('snippet','')).encode()).hexdigest()} for i,e in enumerate(evidence,1)]
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def record(content,checks,state=None,evidence=None,coverage=None):
    accepted=sum(c['status']=='supported' for c in checks);rejected=len(checks)-accepted
    missing=bool(coverage and any(i['status'] in ('missing','insufficient') for i in coverage.get('items',[])))
    return {'policy':POLICY,'state':state or ('verified' if accepted and not rejected and not missing else 'partial' if accepted else 'insufficient'),
            'content_sha256':hashlib.sha256(content.encode()).hexdigest(),'checked_at':s.now(),
            'evidence_sha256':evidence_digest(evidence or []),'accepted_count':accepted,'rejected_count':rejected,'checks':checks,'coverage':coverage or {}}

def manual(content):
    return record(content,[],'unverified_manual')

def current(section):
    raw=section.get('verification') or '{}'
    value=json.loads(raw) if isinstance(raw,str) else raw
    citations=section.get('citations') or '[]'
    citations=json.loads(citations) if isinstance(citations,str) else citations
    if value.get('content_sha256')!=hashlib.sha256(section['content'].encode()).hexdigest() or (value.get('state') not in ('unverified_manual',None) and value.get('evidence_sha256')!=evidence_digest(citations)):
        return {'state':'unverified_manual' if section.get('edited') else 'legacy','checks':[]}
    return value

def label(section):
    v=current(section)
    title={'verified':'逐句证据支撑核验通过','partial':'部分候选句被拦截，需评审','insufficient':'证据不足，未获得可发布事实',
           'unverified_manual':'人工编辑草稿（未执行章节事实支撑核验）','legacy':'历史章节未执行事实支撑核验'}.get(v.get('state'),'未核验')
    if v.get('policy'):
        title+=f"；通过 {v['accepted_count']}，拦截 {v['rejected_count']}；核验时间 {v['checked_at']}；正文SHA256 {v['content_sha256']}"
    if v.get('coverage'):
        coverage=v['coverage']
        title+='；关键点覆盖（模型评估）：'+('已覆盖' if coverage.get('complete') else '待复核或证据不足')
    return title

async def generate(instruction,evidence,authorize):
    content='';checks=[];coverage={}
    if not evidence:
        content='当前知识库证据不足，待补充资料。'
        yield {'type':'token','content':content}
    else:
        async for item in grounding.verified_stream(instruction+' 正文只写带句末引用的事实句，不输出标题、表格、列表序号或未经证据支持的建议。',evidence):
            authorize()
            if item['type']=='verification':checks.extend(item['checks'])
            if item['type']=='coverage':coverage=item['assessment']
            if item['type']=='token':content+=item['content']
            yield item
    authorize()
    yield {'type':'result','content':content,'verification':record(content,checks,evidence=evidence,coverage=coverage)}

async def verify_existing(content,evidence,authorize):
    units=[unit for para in content.splitlines() if para.strip() for unit in grounding.sentences(para)]
    if not units or len(units)>64 or len(content)>32000:
        raise ValueError('核验仅支持1至64个句段且不超过32000字，请精简或拆分章节；未跳过任何句段')
    checks=[]
    for i in range(0,len(units),8):
        authorize()
        _,batch=await grounding.check_batch(units[i:i+8],evidence,i+1)
        authorize();checks.extend(batch)
    return record(content,checks,evidence=evidence)

def compose(report,sections,source_lines):
    from . import blueprints
    plan=blueprints.ReportBlueprint.model_validate_json(report['blueprint']) if report['blueprint']!='{}' else None
    text='# '+report['title']+'\n\n> 新生成正文仅发布通过独立逐句证据核验的事实。模型核验可能误判，仍需人工复核；确认状态只表示防覆盖。\n'
    for i,section in enumerate(sections):
        level=min(6,(plan.sections[i].level if plan and i<len(plan.sections) else 1)+1)
        text+='\n'+'#'*level+' '+section['title']+'\n\n> '+label(section)+'\n\n'+section['content']+'\n'
        v=current(section)
        if v.get('coverage'):
            names={'covered':'已覆盖（模型评估）','supplemented':'已补充并通过事实核验','missing':'仍有遗漏','insufficient':'证据不足'}
            text+='\n本章关键点检查（模型评估，不保证绝对完整）\n'
            for item in v['coverage'].get('items',[]):
                text+=f"- {item['requirement']}：{names.get(item['status'],'待复核')}\n"
        # Hashes/reasons and verbatim evidence, never rejected raw candidate claims.
        if v.get('checks'):
            text+='\n核验记录\n'
            for check in v['checks']:
                text+=f"- 句段 {check['claim_id']}：{check['status']}；{check['reason']}；SHA256 {check['sha256']}\n"
                for q in check.get('quotes',[]):text+=f"  证据[{q['reference']}]原文：{q['quote']}\n"
        if not plan or plan.format.include_sources:text+='\n### 本章来源\n'+source_lines(json.loads(section['citations']) if isinstance(section['citations'],str) else section['citations'])+'\n'
    return text

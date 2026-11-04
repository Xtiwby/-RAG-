"""Quality gates are deterministic here; real-model results are reported separately."""
import asyncio
import hashlib
import json
import pytest
from test_api import client,admin,application_lifespan,kb,upload,configured,save_config,events,response_text,create_member
from app import grounding,rag,settings,evidence_matching as match,report_grounding,store

@pytest.mark.parametrize('claim,source,expected',[
 ('BM25中k1=1.2，b=.75。','The constants are 1.2 and 0.75 respectively.',True),
 ('阈值为1.20。','阈值为1.2。',True),
 ('容量为1000。','容量为1,000。',True),
 ('温度为-12。','温度为12。',False),
 ('误差为80%。','误差为80。',False),
 ('阈值为9999。','阈值为10。',False),
 ('k1=0.75，b=1.2。','k1=1.2，b=0.75。',False),
 ('FTS5使用BM25。','文档使用该检索函数。',True), # numeric guard only; semantic judge still required
 ('前20段的提升为9%至19%。','top-20 passage retrieval improves 9%-19%.',True),
 ('最低温度-12。','最高温度12。',False),
 ('第二个值为2。','数值依次为1 -2。',False),
])
def test_numbers_and_parameter_bindings(claim,source,expected):
    assert match.numeric_support(claim,[source]) is expected

@pytest.mark.parametrize('quote,source,expected',[
 ('multilingual retrieval','multi-\nlingual retrieval',True),
 ('dual-encoder framework','dual-encoder framework',True),
 ('结果正确','结\u200b果 正确',True),
 ('温度12','温度-12',False),
 ('not permitted','permitted',False),
 ('  ','真实的原文内容',False),
 ('结果正确','结果错误',False),
])
def test_quote_offsets_are_into_unchanged_original(quote,source,expected):
    result=match.locate(quote,source)
    assert bool(result) is expected
    if result:
        assert result['quote']==source[result['char_start']:result['char_end']]
        assert result['source_sha256']==hashlib.sha256(source.encode()).hexdigest()

def enable(admin,repair=True):
    save_config(admin,lambda cfg:cfg['answer_quality'].update(enabled=True,repair=repair))

def model_factory(coverage,*,bad_supplement=False,fail_coverage=False):
    async def model(messages,max_tokens=4096):
        data=json.loads(messages[-1]['content'])
        if 'coverage_schema' in data:
            if fail_coverage:yield 'not JSON';return
            yield json.dumps({'items':coverage},ensure_ascii=False);return
        if 'verification_schema' in data:
            yield json.dumps({'verdicts':[{'claim_id':c['claim_id'],
                'verdict':'contradicted' if bad_supplement and '9999' in c['text'] else 'supported',
                'quotes':[{'reference':r,'quote':next(e['text'] for e in data['evidence_json'] if e['reference']==r)} for r in c['references']]} for c in data['claims']]});return
        yield '控制器采用被动散热。[1]\n'
    return model

def run(admin,monkeypatch,coverage,**kwargs):
    kid=kb(admin);upload(admin,kid,text='控制器采用被动散热。机壳使用陶瓷散热片，不配备转动风扇。')
    monkeypatch.setattr(rag,'llm_stream',model_factory(coverage,**kwargs))
    stream=events(client.post('/api/qa/stream',headers=admin,json={'query':'控制器怎样散热，使用什么材料？','knowledge_base_ids':[kid]}))
    row=client.get(f"/api/conversations/{stream[0]['conversation_id']}",headers=admin).json()['messages'][-1]
    return stream,row

def missing(text='机壳使用陶瓷散热片。[1]'):
    return [{'requirement':'散热方式','status':'covered','references':[1],'supplement':''},
        {'requirement':'散热片材料','status':'missing','references':[1],'supplement':text}]

def test_missing_core_fact_supplement_is_checked_persisted_and_streamed(admin,configured,monkeypatch):
    enable(admin);stream,row=run(admin,monkeypatch,missing())
    assert '陶瓷' in row['content'] and row['coverage']['complete']
    assert row['coverage']['items'][1]['status']=='supplemented'
    extra=next(c for c in row['verification'] if c.get('origin')=='coverage_supplement')
    assert extra['status']=='supported'
    verify_index=next(i for i,e in enumerate(stream) if e['type']=='verification' and any(c.get('origin')=='coverage_supplement' for c in e['checks']))
    token_index=next(i for i,e in enumerate(stream) if e['type']=='token' and '陶瓷' in e['content'])
    assert verify_index<token_index and 'coverage' in {e['type'] for e in stream}

def test_unsupported_supplement_cannot_leak_into_events_or_storage(admin,configured,monkeypatch):
    enable(admin);stream,row=run(admin,monkeypatch,missing('机壳已服务9999用户。[1]'),bad_supplement=True)
    assert '9999' not in json.dumps(stream,ensure_ascii=False) and '9999' not in row['content']
    assert row['coverage']['items'][1]['status']=='missing' and not row['coverage']['complete']

def test_repair_off_keeps_coverage_but_does_not_append_candidate(admin,configured,monkeypatch):
    enable(admin,False);stream,row=run(admin,monkeypatch,missing())
    assert '陶瓷' not in row['content'] and not row['coverage']['repair_attempted']
    assert not any(c.get('origin') for c in row['verification'])

def test_coverage_failure_retains_supported_answer_and_marks_unavailable(admin,configured,monkeypatch):
    enable(admin);stream,row=run(admin,monkeypatch,[],fail_coverage=True)
    assert '被动散热' in row['content'] and row['coverage']['status']=='unavailable'
    assert not row['coverage']['complete'] and stream[-1]['type']=='done'

def test_partial_verdict_never_means_supported(admin,configured,monkeypatch):
    async def judge(messages,max_tokens=None):
        yield json.dumps({'verdicts':[{'claim_id':1,'verdict':'partial','quotes':[{'reference':1,'quote':'只支持部分事实'}]}]})
    monkeypatch.setattr(rag,'llm_stream',judge)
    accepted,checks=asyncio.run(grounding.check_batch(['部分事实加编造。[1]'],[{'text':'只支持部分事实'}],1))
    assert accepted==[] and checks[0]['status']=='rejected' and checks[0]['reason_code']=='partial'

def test_judge_failure_does_not_trigger_unverified_repair(admin,configured,monkeypatch):
    enable(admin);calls=[]
    async def broken(messages,max_tokens=None):
        data=json.loads(messages[-1]['content']);calls.append(data)
        if 'verification_schema' in data:yield 'invalid';return
        if 'coverage_schema' in data:raise AssertionError('No repair after failed judge')
        yield '机壳使用陶瓷散热片。[1]'
    kid=kb(admin);upload(admin,kid,text='机壳使用陶瓷散热片。')
    monkeypatch.setattr(rag,'llm_stream',broken)
    stream=events(client.post('/api/qa/stream',headers=admin,json={'query':'机壳是什么材料？','knowledge_base_ids':[kid]}))
    assert response_text(stream)==grounding.REFUSAL and len(calls)==2

def test_fact_report_reuses_coverage_without_overwriting_saved_report(admin,configured,monkeypatch):
    from app import report_workflow
    enable(admin);monkeypatch.setattr(rag,'llm_stream',model_factory(missing()))
    evidence=[{'text':'控制器采用被动散热。机壳使用陶瓷散热片，不配备转动风扇。','name':'合成资料','section':'正文','page':None,'version':'v1'}]
    content,record=asyncio.run(report_workflow.generate_section('说明散热方式和材料',evidence,lambda:None))
    assert '陶瓷' in content and record['coverage']['items'][1]['status']=='supplemented'

def test_layout_quote_and_identifier_numeric_guard_with_full_judge(admin,configured,monkeypatch):
    async def judge(messages,max_tokens=None):
        yield json.dumps({'verdicts':[{'claim_id':1,'verdict':'supported','quotes':[{'reference':1,'quote':'Constants are 1.2 and 0.75 for the multilingual model.'}]}]})
    monkeypatch.setattr(rag,'llm_stream',judge)
    text='Constants are 1.2 and 0.75 for the multi-\nlingual model.'
    accepted,checks=asyncio.run(grounding.check_batch(['BM25的k1=1.2，b=.75。[1]'],[{'text':text}],1))
    assert accepted and checks[0]['quotes'][0]['match']=='layout-normalized'

@pytest.mark.parametrize('text,refs',[
 ('采用双编码器框架。[1,2]',[1,2]),
 ('**采用双编码器框架。[1，2]**',[1,2]),
 ('**采用双编码器框架 [1][2]。**',[1,2]),
 ('伪造引用。[1,99]',None),
 ('数组值为 `[1,2]`。',None),
 ('数组值为 `[1]`。',None),
])
def test_grouped_citations_and_markdown_closing(text,refs):
    units=grounding.sentences(text)
    assert len(units)==1
    assert grounding.claim_references(units[0],2)==refs

def test_sentence_split_preserves_code_attributes_and_does_not_borrow_citations():
    units=grounding.sentences('`UploadFile.file` 是文件对象。[1] 未引用的另一事实。')
    assert len(units)==2 and 'UploadFile.file' in units[0]
    assert grounding.claim_references(units[0],1)==[1] and grounding.claim_references(units[1],1) is None

def test_adjacent_context_has_own_identity_and_does_not_cross_scope(admin,configured):
    kid=kb(admin)
    upload(admin,kid,text='主功能说明：\n\n该装置通过散热片排出热量，不含转动部件。')
    enable(admin);save_config(admin,lambda cfg:cfg['answer_quality'].update(context_neighbors=4))
    result=client.post('/api/search',headers=admin,json={'query':'主功能说明','mode':'keyword','top_k':1,'knowledge_base_ids':[kid]}).json()
    assert result['pipeline']['main_result_count']==1 and result['pipeline']['context_result_count']==1
    main,context=result['results']
    assert context['chunk_id']!=main['chunk_id'] and context['context_for']==main['chunk_id']
    assert context['kind']=='adjacent-context' and context['sha256']==hashlib.sha256(context['text'].encode()).hexdigest()
    assert context['relevance']==0 and context['knowledge_base_id']==kid

def test_revocation_during_coverage_blocks_supplement_before_publish(admin,configured,monkeypatch):
    kid=kb(admin);upload(admin,kid,text='控制器采用被动散热。机壳使用陶瓷散热片，不配备转动风扇。')
    enable(admin);user,headers=create_member(admin)
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[{'knowledge_base_id':kid,'access':'read'}]})
    base=model_factory(missing())
    async def revoke(messages,max_tokens=4096):
        if 'coverage_schema' in json.loads(messages[-1]['content']):
            store.execute('DELETE FROM kb_grants WHERE user_id=? AND kb_id=?',(user['id'],kid))
        async for token in base(messages,max_tokens):yield token
    monkeypatch.setattr(rag,'llm_stream',revoke)
    stream=events(client.post('/api/qa/stream',headers=headers,json={'query':'散热方式和材料','knowledge_base_ids':[kid]}))
    assert stream[-1]['type']=='error' and '陶瓷' not in response_text(stream)
    assert not any(e['type']=='coverage' for e in stream)

"""Versioned FAQ knowledge and confidence routing for knowledge question answering."""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import defaultdict

from fastapi import HTTPException

from . import rag
from . import settings
from . import store as s


def _json(value, default):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def serialize(row, *, include_metrics=False):
    item = dict(row)
    item['keywords'] = _json(item.get('keywords'), [])
    item['evidence_refs'] = _json(item.get('evidence_refs'), [])
    item['variants'] = [r['question'] for r in s.rows(
        'SELECT question FROM faq_variants WHERE faq_id=? ORDER BY id', (item['id'],)
    )]
    if include_metrics:
        metrics = s.one('''SELECT COUNT(*) uses,
            SUM(CASE WHEN helpful=1 THEN 1 ELSE 0 END) helpful,
            SUM(CASE WHEN helpful=0 THEN 1 ELSE 0 END) unhelpful,
            AVG(latency_ms) average_latency_ms FROM faq_feedback WHERE faq_id=?''', (item['id'],))
        item['metrics'] = metrics or {'uses': 0, 'helpful': 0, 'unhelpful': 0, 'average_latency_ms': 0}
    return item


def get(faq_id):
    row = s.one('SELECT * FROM faq_items WHERE id=?', (faq_id,))
    if not row:
        raise HTTPException(404, '问答对不存在')
    return row


def list_items(kb_ids, *, include_all=False, status=None, query=''):
    if not kb_ids:
        return []
    marks = ','.join('?' * len(kb_ids))
    clauses = [f'f.knowledge_base_id IN ({marks})']
    params = list(kb_ids)
    if status:
        clauses.append('f.status=?')
        params.append(status)
    elif not include_all:
        clauses.append("f.status='published'")
    if query.strip():
        clauses.append('(LOWER(f.standard_question) LIKE ? OR LOWER(f.standard_answer) LIKE ? OR LOWER(f.category) LIKE ?)')
        pattern = '%' + query.strip().lower() + '%'
        params.extend([pattern, pattern, pattern])
    rows = s.rows(f'''SELECT f.*,k.name knowledge_base_name,u.name created_by_name
        FROM faq_items f JOIN knowledge_bases k ON k.id=f.knowledge_base_id
        JOIN users u ON u.id=f.created_by WHERE {' AND '.join(clauses)}
        ORDER BY f.updated_at DESC,f.id DESC''', params)
    return [serialize(row, include_metrics=include_all) for row in rows]


def _replace_variants(connection, faq_id, variants):
    connection.execute('DELETE FROM faq_variants WHERE faq_id=?', (faq_id,))
    normalized = []
    seen = set()
    for value in variants:
        question = ' '.join(str(value).split()).strip()
        if question and question not in seen:
            seen.add(question)
            normalized.append(question)
    connection.executemany('INSERT INTO faq_variants(faq_id,question,created_at) VALUES(?,?,?)',
                           [(faq_id, question, s.now()) for question in normalized])


def create(data, user_id):
    now = s.now()
    with s.connect() as connection:
        faq_id = connection.execute('''INSERT INTO faq_items(
            family_id,parent_id,knowledge_base_id,standard_question,standard_answer,keywords,category,
            evidence_refs,status,version,created_by,created_at,updated_at) VALUES(NULL,NULL,?,?,?,?,?,?,?,1,?,?,?)''',
            (data['knowledge_base_id'], data['standard_question'].strip(), data['standard_answer'].strip(),
             json.dumps(data.get('keywords', []), ensure_ascii=False), data.get('category', '').strip(),
             json.dumps(data.get('evidence_refs', []), ensure_ascii=False), 'draft', user_id, now, now)).lastrowid
        connection.execute('UPDATE faq_items SET family_id=? WHERE id=?', (faq_id, faq_id))
        _replace_variants(connection, faq_id, data.get('variants', []))
    return serialize(get(faq_id), include_metrics=True)


def update(faq_id, data):
    row = get(faq_id)
    if row['status'] == 'published':
        raise HTTPException(409, '已发布版本不可直接修改，请先创建新版本')
    if row['status'] == 'archived':
        raise HTTPException(409, '已归档版本不可修改')
    expected = data.get('expected_updated_at')
    if expected and expected != row['updated_at']:
        raise HTTPException(409, '该问答对已被其他人修改，请刷新后重试')
    now = s.now()
    with s.connect() as connection:
        connection.execute('''UPDATE faq_items SET knowledge_base_id=?,standard_question=?,standard_answer=?,
            keywords=?,category=?,evidence_refs=?,updated_at=? WHERE id=?''',
            (data['knowledge_base_id'], data['standard_question'].strip(), data['standard_answer'].strip(),
             json.dumps(data.get('keywords', []), ensure_ascii=False), data.get('category', '').strip(),
             json.dumps(data.get('evidence_refs', []), ensure_ascii=False), now, faq_id))
        _replace_variants(connection, faq_id, data.get('variants', []))
    return serialize(get(faq_id), include_metrics=True)


def new_version(faq_id, user_id):
    source = serialize(get(faq_id))
    latest = s.one('SELECT MAX(version) version FROM faq_items WHERE family_id=?', (source['family_id'],))
    now = s.now()
    with s.connect() as connection:
        new_id = connection.execute('''INSERT INTO faq_items(
            family_id,parent_id,knowledge_base_id,standard_question,standard_answer,keywords,category,
            evidence_refs,status,version,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (source['family_id'], source['id'], source['knowledge_base_id'], source['standard_question'],
             source['standard_answer'], json.dumps(source['keywords'], ensure_ascii=False), source['category'],
             json.dumps(source['evidence_refs'], ensure_ascii=False), 'draft', int(latest['version'] or 0) + 1,
             user_id, now, now)).lastrowid
        _replace_variants(connection, new_id, source['variants'])
    return serialize(get(new_id), include_metrics=True)


def change_status(faq_id, status):
    row = get(faq_id)
    if status == 'published':
        if not row['standard_question'].strip() or not row['standard_answer'].strip():
            raise HTTPException(422, '发布前必须填写标准问题和标准答案')
        if not _json(row['evidence_refs'], []):
            raise HTTPException(422, '发布前至少关联一个来源证据片段')
        with s.connect() as connection:
            connection.execute("UPDATE faq_items SET status='inactive',updated_at=? WHERE family_id=? AND status='published' AND id<>?",
                               (s.now(), row['family_id'], faq_id))
            connection.execute("UPDATE faq_items SET status='published',updated_at=? WHERE id=?", (s.now(), faq_id))
    elif status == 'inactive':
        s.execute('UPDATE faq_items SET status=?,updated_at=? WHERE id=?', (status, s.now(), faq_id))
    else:
        raise HTTPException(422, '不支持的问答对状态')
    return serialize(get(faq_id), include_metrics=True)


def delete_family(faq_id):
    """Permanently delete one FAQ and every version in the same family."""
    row = get(faq_id)
    family_id = row.get('family_id') or row['id']
    versions = s.rows('SELECT id FROM faq_items WHERE family_id=? OR id=? ORDER BY id',
                      (family_id, family_id))
    ids = [version['id'] for version in versions]
    marks = ','.join('?' * len(ids))
    with s.connect() as connection:
        # Feedback has no ON DELETE CASCADE, so remove it before FAQ rows.
        connection.execute(f'DELETE FROM faq_feedback WHERE faq_id IN ({marks})', ids)
        connection.execute(f'DELETE FROM faq_variants WHERE faq_id IN ({marks})', ids)
        connection.execute(f'DELETE FROM faq_items WHERE id IN ({marks})', ids)
    return {'ok': True, 'family_id': family_id, 'deleted_versions': len(ids)}


def evidence_from_chunk_ids(chunk_ids, knowledge_base_id):
    ids = list(dict.fromkeys(int(value) for value in chunk_ids))
    if not ids:
        return []
    marks = ','.join('?' * len(ids))
    rows = s.rows(f'''SELECT c.id chunk_id,c.document_id,c.page,c.section,c.text,d.name source,d.version,
        d.knowledge_base_id,k.name knowledge_base_name FROM chunks c JOIN documents d ON d.id=c.document_id
        JOIN knowledge_bases k ON k.id=d.knowledge_base_id WHERE c.id IN ({marks}) AND d.deleted=0''', ids)
    found = {row['chunk_id']: row for row in rows}
    missing = [value for value in ids if value not in found]
    if missing:
        raise HTTPException(422, '证据片段不存在：' + '、'.join(map(str, missing)))
    wrong = [value for value in ids if found[value]['knowledge_base_id'] != knowledge_base_id]
    if wrong:
        raise HTTPException(422, '证据片段必须属于所选知识库：' + '、'.join(map(str, wrong)))
    return [{**found[value], 'snippet': found[value]['text']} for value in ids]


def _search_text(item):
    values = [item['standard_question'], *item['variants'], *item['keywords']]
    return '\n'.join(value for value in values if value)


def _normalize(values):
    if not values:
        return []
    low, high = min(values), max(values)
    if high <= low:
        return [1.0 if high > 0 else 0.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


async def _vector_scores(query, items):
    config = settings.get().embedding
    texts = [_search_text(item) for item in items]
    if config.provider == 'local':
        query_vector = (await rag.local_query_embeddings(config, [query]))[query]
        vectors = await rag.cache_local_embeddings(texts)
        query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1
        return [sum(a * b for a, b in zip(query_vector, vector)) /
                (query_norm * (math.sqrt(sum(value * value for value in vector)) or 1)) for vector in vectors], 'local-bge'
    if config.provider == 'api':
        prepared = [{'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()} for text in texts]
        return await rag.semantic_vectors(query, prepared), 'semantic-api'
    ranker = rag.LexicalRanker([{'name': item['standard_question'], 'text': text} for item, text in zip(items, texts)], tfidf=True)
    _, values = ranker.rank(rag.chunk_terms(query))
    return values or [0.0] * len(items), 'local-tfidf'


async def search(query, kb_ids, mode='hybrid', top_k=5, rerank=True):
    started = time.perf_counter()
    query = rag.preprocess(query)
    items = list_items(kb_ids)
    if not query or not items:
        return {'query': query, 'mode': mode, 'route': 'low', 'results': [], 'elapsed_ms': 0,
                'thresholds': thresholds(), 'pipeline': {'embedding': 'not-used', 'rerank': 'none'}}
    documents = [{'name': item['standard_question'], 'text': _search_text(item)} for item in items]
    lexical_ranker = rag.LexicalRanker(documents, tfidf=False)
    lexical_raw, _ = lexical_ranker.rank(rag.chunk_terms(query))
    lexical = _normalize(lexical_raw)
    vector = [0.0] * len(items)
    embedding = 'not-used'
    if mode != 'keyword':
        vector, embedding = await _vector_scores(query, items)
        vector = [max(0.0, min(1.0, value)) for value in vector]

    ranks = defaultdict(float)
    signals = [lexical] if mode == 'keyword' else [vector] if mode == 'vector' else [lexical, vector]
    for signal in signals:
        for rank, index in enumerate(sorted(range(len(items)), key=lambda i: signal[i], reverse=True), 1):
            if signal[index] > 0:
                ranks[index] += 1 / (60 + rank)
    max_rrf = max(ranks.values(), default=1)
    base_scores = [lexical[index] if mode == 'keyword' else vector[index] if mode == 'vector' else (
        0.42 * lexical[index] + 0.48 * vector[index] + 0.10 * ranks[index] / max_rrf)
        for index in range(len(items))]
    neural = {}
    neural_meta = {}
    if rerank and settings.get().reranking.provider == 'local':
        from . import local_models
        config = settings.get().reranking
        pool = sorted(range(len(items)), key=lambda index: base_scores[index], reverse=True)[:config.candidate_k]
        values, neural_meta = await local_models.rerank(config, query, [_search_text(items[index]) for index in pool])
        if len(values) != len(pool) or any(not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise HTTPException(502, '问答对重排模型返回无效得分；未静默改用词项重排')
        neural = dict(zip(pool, values))
    query_fold = ''.join(query.lower().split())
    results = []
    for index, item in enumerate(items):
        questions = [item['standard_question'], *item['variants']]
        exact = any(query_fold == ''.join(question.lower().split()) for question in questions)
        containment = any(query_fold in ''.join(question.lower().split()) or ''.join(question.lower().split()) in query_fold
                          for question in questions if question.strip())
        base = base_scores[index]
        if index in neural:
            base = 0.25 * base + 0.75 * neural[index]
        score = 0.99 if exact else max(base, 0.86 if containment and lexical[index] > 0 else 0)
        if rerank:
            coverage = len(set(rag.chunk_terms(query)) & set(rag.chunk_terms(_search_text(item)))) / max(1, len(set(rag.chunk_terms(query))))
            score = min(1.0, score * 0.9 + coverage * 0.1)
        if score <= 0:
            continue
        citations = [{**ref, 'text': ref.get('text') or ref.get('snippet', ''), 'relevance': round(score, 4)}
                     for ref in item['evidence_refs']]
        results.append({**item, 'score': round(score, 4), 'keyword_score': round(lexical[index], 4),
                        'vector_score': round(vector[index], 4), 'citations': citations})
    results.sort(key=lambda value: value['score'], reverse=True)
    results = results[:top_k]
    limits = thresholds()
    best = results[0]['score'] if results else 0
    margin = best - (results[1]['score'] if len(results) > 1 else 0)
    top_keyword_hits = 0
    if results:
        # A paraphrase can score below the generic semantic threshold while
        # still naming the FAQ's entity and action exactly (for example
        # “机油” + “等待”).  Two explicit configured keywords, traceable
        # evidence and no competing answer are sufficient for a safe direct
        # route; one generic word alone is never enough.
        top_keyword_hits = sum(
            1 for keyword in results[0].get('keywords', [])
            if len(''.join(str(keyword).casefold().split())) >= 2
            and ''.join(str(keyword).casefold().split()) in query_fold
        )
    unambiguous = len(results) == 1 or margin >= limits['margin']
    verified_keyword_match = bool(limits['high'] <= .80 and results and results[0].get('citations') and top_keyword_hits >= 2
                                  and results[0]['keyword_score'] >= .8
                                  and best >= max(limits['medium'], .70))
    route = 'high' if unambiguous and (best >= limits['high'] or verified_keyword_match) else \
            'medium' if best >= limits['medium'] else 'low'
    return {'query': query, 'mode': mode, 'route': route, 'results': results,
            'elapsed_ms': round((time.perf_counter() - started) * 1000), 'thresholds': limits,
            'pipeline': {'embedding': embedding,
                         'rerank': 'local-cross-encoder' if neural else 'question-match' if rerank else 'none',
                         'reranking': ({'model': settings.get().reranking.model,
                                        'candidate_count': len(neural), **neural_meta} if neural else None),
                         'policy': 'faq-rrf-confidence-v1', 'margin': round(margin, 4),
                         'keyword_hits': top_keyword_hits}}


def thresholds():
    def value(name, default):
        try:
            return float(os.getenv(name, str(default)))
        except ValueError:
            return default
    return {'high': value('RAG_FAQ_HIGH_THRESHOLD', 0.80),
            'medium': value('RAG_FAQ_MEDIUM_THRESHOLD', 0.50),
            'margin': value('RAG_FAQ_MIN_MARGIN', 0.04)}


def record_use(result, user_id, query, mode, latency_ms=0, helpful=None, reason=''):
    if not result:
        return None
    return s.execute('''INSERT INTO faq_feedback(faq_id,faq_version,user_id,query,retrieval_mode,score,helpful,reason,latency_ms,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)''', (result['id'], result['version'], user_id, query, mode, result.get('score', 0),
                                       helpful, reason, latency_ms, s.now()))


def update_feedback(feedback_id, user_id, helpful, reason=''):
    row = s.one('SELECT * FROM faq_feedback WHERE id=? AND user_id=?', (feedback_id, user_id))
    if not row:
        raise HTTPException(404, '反馈记录不存在')
    s.execute('UPDATE faq_feedback SET helpful=?,reason=? WHERE id=?', (helpful, reason.strip(), feedback_id))
    return {'ok': True}

"""Indexed candidate retrieval for the optional PostgreSQL + pgvector deployment."""
import json
import os
import re

from . import settings, store as s


def enabled():
    return bool(os.getenv('RAG_DATABASE_URL'))


def index_document(conn, document_id):
    if not enabled():
        return
    from . import rag, local_models
    doc=conn.execute('SELECT knowledge_base_id,name FROM documents WHERE id=?',(document_id,)).fetchone()
    conn.execute('DELETE FROM chunk_search WHERE document_id=?',(document_id,))
    namespace=local_models.namespace(settings.get().embedding) if settings.get().embedding.provider=='local' else None
    for chunk in conn.execute('SELECT id,text,sha256 FROM chunks WHERE document_id=?',(document_id,)):
        vector=None
        if namespace:
            cached=conn.execute('SELECT vector FROM embedding_cache WHERE namespace=? AND sha256=?',
                                (namespace,chunk['sha256'])).fetchone()
            if cached:
                values=json.loads(cached['vector'])
                if len(values)==1024:
                    vector=json.dumps(values)
        search_terms=' '.join(rag.terms(doc['name']+' '+chunk['text']))
        plain=re.sub(r'https?://\S+','',chunk['text'])
        zh=len(re.findall(r'[\u4e00-\u9fff]',plain))
        en=len(re.findall(r'[a-zA-Z]',plain))
        conn.execute('''INSERT INTO chunk_search(chunk_id,document_id,knowledge_base_id,search_terms,has_zh,has_en,embedding)
            VALUES(?,?,?,?,?,?,CAST(? AS vector(1024)))''',
            (chunk['id'],document_id,doc['knowledge_base_id'],search_terms,int(zh>=12),int(en>=100 and zh<en*.12),vector))


def complete():
    if not enabled():
        return False
    return s.one('SELECT COUNT(*) n FROM chunk_search')['n']==s.one('SELECT COUNT(*) n FROM chunks')['n']


def ready(mode):
    if not enabled() or not complete():
        return False
    config=settings.get().embedding
    if mode!='keyword' and config.provider!='local':
        return False
    if mode!='keyword' and s.one('SELECT COUNT(*) n FROM chunk_search WHERE embedding IS NULL')['n']:
        return False
    return True


def language_samples(kb_ids):
    if not kb_ids:
        return []
    marks=','.join('?' for _ in kb_ids)
    samples=[]
    for flag in ('has_zh','has_en'):
        row=s.one(f'''SELECT c.text FROM chunk_search x JOIN chunks c ON c.id=x.chunk_id
            JOIN documents d ON d.id=x.document_id
            WHERE x.knowledge_base_id IN ({marks}) AND x.{flag}=1
            AND d.deleted=0 AND d.status='ready'
            AND d.id=(SELECT MAX(d2.id) FROM documents d2 WHERE d2.name=d.name
                AND d2.knowledge_base_id=d.knowledge_base_id AND d2.deleted=0) LIMIT 1''',kb_ids)
        if row:samples.append(row)
    return samples


async def candidates(queries,kb_ids,mode,top_k):
    """Return indexed candidates already filtered by knowledge base and version."""
    if not ready(mode):
        return None
    from . import rag, local_models
    config=settings.get().embedding
    marks=','.join('?' for _ in kb_ids)
    scope=f'''FROM chunk_search x JOIN documents d ON d.id=x.document_id
        WHERE d.deleted=0 AND d.status='ready' AND x.knowledge_base_id IN ({marks})
        AND d.id=(SELECT MAX(d2.id) FROM documents d2 WHERE d2.name=d.name
          AND d2.knowledge_base_id=d.knowledge_base_id AND d2.deleted=0)'''
    limit=max(200,top_k*40)
    result=set()
    query_vectors={}
    for variant in queries:
        if mode!='vector':
            words=list(dict.fromkeys(rag.terms(variant['query'])))[:64]
            if words:
                expression=' | '.join(words)
                found=s.rows(f'''SELECT x.chunk_id {scope} AND x.search_vector @@ to_tsquery('simple',?)
                    ORDER BY ts_rank(x.search_vector,to_tsquery('simple',?)) DESC LIMIT ?''',
                    [*kb_ids,expression,expression,limit])
                result.update(row['chunk_id'] for row in found)
        for anchor in rag.query_anchors(variant['query']):
            found=s.rows(f'''SELECT x.chunk_id {scope} AND x.search_vector @@ to_tsquery('simple',?)
                ORDER BY x.chunk_id DESC LIMIT 40''', [*kb_ids,anchor])
            result.update(row['chunk_id'] for row in found)
        if mode!='keyword':
            vector=(await local_models.embed(config,[variant['query']]))[0]
            query_vectors[variant['query']]=vector
            local_models.validate_vectors([vector],1)
            if len(vector)!=1024:
                raise RuntimeError('pgvector 索引要求 BGE-M3 的 1024 维向量')
            with s.connect() as connection:
                # Filtered HNSW queries may need to scan beyond the first ANN batch.
                connection.execute('SET LOCAL hnsw.iterative_scan = strict_order')
                connection.execute('SET LOCAL hnsw.ef_search = 200')
                found=connection.execute(f'''SELECT x.chunk_id {scope} AND x.embedding IS NOT NULL
                    ORDER BY x.embedding <=> CAST(? AS vector(1024)) LIMIT ?''',
                    [*kb_ids,json.dumps(vector),limit]).fetchall()
            result.update(row['chunk_id'] for row in found)
    # Preserve adjacent original context used by citation inspection.
    selected=list(result)
    for start in range(0,len(selected),300):
        batch=selected[start:start+300]
        marks=','.join('?' for _ in batch)
        neighbors=s.rows(f'''SELECT DISTINCT n.id FROM chunks n JOIN chunks c
            ON n.document_id=c.document_id AND n.ordinal BETWEEN c.ordinal-1 AND c.ordinal+1
            WHERE c.id IN ({marks})''',batch)
        result.update(row['id'] for row in neighbors)
    return result,query_vectors

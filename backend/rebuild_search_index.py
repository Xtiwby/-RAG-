"""Rebuild PostgreSQL FTS and pgvector rows from immutable chunks."""
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent/'.env')
from app import store as s, settings, rag, search_index


async def run():
    if not os.getenv('RAG_DATABASE_URL'):
        raise RuntimeError('需要 PostgreSQL 模式')
    docs=s.rows('SELECT id FROM documents ORDER BY id')
    for index,doc in enumerate(docs,1):
        chunks=s.rows('SELECT text FROM chunks WHERE document_id=? ORDER BY ordinal',(doc['id'],))
        if settings.get().embedding.provider=='local' and chunks:
            await rag.cache_local_embeddings([row['text'] for row in chunks])
        with s.connect() as conn:
            search_index.index_document(conn,doc['id'])
        if index%50==0:
            print(f'已重建 {index}/{len(docs)} 份文档')
    print(f'完成：{len(docs)} 份文档，索引完整={search_index.complete()}')

if __name__=='__main__':
    asyncio.run(run())

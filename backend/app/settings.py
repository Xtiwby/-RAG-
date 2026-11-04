"""Validated, revisioned runtime settings. Credentials remain in the environment."""
import json
import os
from contextvars import ContextVar
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from . import store as s

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class ChatSettings(Strict):
    base_url: str = 'https://api.moonshot.cn/v1'
    model: str = Field(default='kimi-k2.6',min_length=1,max_length=100)
    timeout_seconds: int = Field(default=120,ge=10,le=600)
    max_tokens: int = Field(default=4096,ge=256,le=32768)

    @field_validator('base_url')
    @classmethod
    def url(cls,value):
        parts=urlsplit(value)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('地址必须是无凭据、无查询参数的 HTTP(S) 服务地址')
        return value.rstrip('/')

class EmbeddingSettings(Strict):
    provider: Literal['tfidf','api','local'] = 'tfidf'
    base_url: str = ''
    model: str = Field(default='',max_length=100)
    timeout_seconds: int = Field(default=90,ge=10,le=600)
    device: Literal['auto','cpu','cuda'] = 'auto'
    batch_size: int = Field(default=8,ge=1,le=32)
    max_length: int = Field(default=2048,ge=128,le=8192)

    @model_validator(mode='after')
    def validate_api(self):
        if self.provider=='api':
            if not self.model.strip():
                raise ValueError('API 向量模式必须填写模型名')
            self.base_url=ChatSettings.url(self.base_url)
        elif self.provider=='local':
            if self.model!='BAAI/bge-m3':
                raise ValueError('本地语义模型目前支持 BAAI/bge-m3；请先运行模型准备脚本')
        elif self.base_url:
            self.base_url=ChatSettings.url(self.base_url)
        return self

class RerankingSettings(Strict):
    # Preserve existing configuration/job snapshots until explicitly upgraded.
    provider: Literal['lexical','local'] = 'lexical'
    model: Literal['BAAI/bge-reranker-v2-m3'] = 'BAAI/bge-reranker-v2-m3'
    device: Literal['auto','cpu','cuda'] = 'auto'
    candidate_k: int = Field(default=40,ge=20,le=100)
    batch_size: int = Field(default=4,ge=1,le=16)
    max_length: int = Field(default=1024,ge=128,le=8192)
    timeout_seconds: int = Field(default=120,ge=10,le=600)

class ChunkSettings(Strict):
    size: int = Field(default=800,ge=100,le=8000)
    overlap: int = Field(default=150,ge=0,le=2000)

    @model_validator(mode='after')
    def overlap_check(self):
        if self.overlap>=self.size:
            raise ValueError('重叠长度必须小于片段长度')
        return self

class RetrievalSettings(Strict):
    mode: Literal['vector','keyword','hybrid'] = 'hybrid'
    top_k: int = Field(default=6,ge=1,le=20)
    min_similarity: float = Field(default=.30,ge=0,le=1)
    min_coverage: float = Field(default=.12,ge=0,le=1)
    rerank: bool = True
    cross_language: bool = False
    query_rewrite_timeout_seconds: int = Field(default=20,ge=2,le=60)

class QueueSettings(Strict):
    concurrency: int = Field(default=2,ge=1,le=4)
    max_attempts: int = Field(default=3,ge=1,le=5)

class AnswerQualitySettings(Strict):
    # Old saved requests/jobs retain their old policy until explicitly enabled.
    enabled: bool = False
    repair: bool = True
    max_supplements: int = Field(default=4,ge=1,le=6)
    timeout_seconds: int = Field(default=45,ge=10,le=120)
    context_neighbors: int = Field(default=0,ge=0,le=6)

class RuntimeSettings(Strict):
    chat: ChatSettings = Field(default_factory=ChatSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    reranking: RerankingSettings = Field(default_factory=RerankingSettings)
    chunk: ChunkSettings = Field(default_factory=ChunkSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    answer_quality: AnswerQualitySettings = Field(default_factory=AnswerQualitySettings)

active: ContextVar[RuntimeSettings|None]=ContextVar('rag_configuration',default=None)

def defaults():
    return RuntimeSettings(chat=ChatSettings(base_url=os.getenv('KIMI_BASE_URL','https://api.moonshot.cn/v1'),model=os.getenv('KIMI_MODEL','kimi-k2.6')),
        embedding=EmbeddingSettings(provider='api' if os.getenv('EMBEDDING_BASE_URL') and os.getenv('EMBEDDING_MODEL') else 'tfidf',
          base_url=os.getenv('EMBEDDING_BASE_URL',''),model=os.getenv('EMBEDDING_MODEL','')),
        retrieval=RetrievalSettings(min_similarity=float(os.getenv('RAG_MIN_SIMILARITY','.3'))))

def get():
    if active.get() is not None:
        return active.get()
    row=s.one('SELECT value FROM system_settings WHERE id=1')
    return RuntimeSettings.model_validate_json(row['value']) if row else defaults()

def read():
    row=s.one('SELECT * FROM system_settings WHERE id=1')
    value=RuntimeSettings.model_validate_json(row['value']) if row else defaults()
    return {'settings':value.model_dump(),'revision':row['revision'] if row else 0,
            'updated_at':row['updated_at'] if row else None,
            'credentials':{'chat':bool(os.getenv('KIMI_API_KEY','').strip() and not os.getenv('KIMI_API_KEY','').startswith('replace-')),'embedding':bool(os.getenv('EMBEDDING_API_KEY','').strip())}}

def save(value,expected_revision,user):
    with s.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT revision FROM system_settings WHERE id=1').fetchone()
        revision=row['revision'] if row else 0
        if revision!=expected_revision:
            from fastapi import HTTPException
            raise HTTPException(409,'配置已被其他管理员修改，请刷新后重试')
        c.execute('INSERT OR REPLACE INTO system_settings VALUES(1,?,?,?,?)',(value.model_dump_json(),revision+1,s.now(),user['id']))
    s.audit(user,'修改配置','系统配置',f'配置版本 {revision+1}；未记录密钥',object_id=revision+1)
    return read()

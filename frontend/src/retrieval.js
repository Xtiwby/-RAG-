export const retrievalModes = [
  { value: 'vector', label: '向量检索' },
  { value: 'keyword', label: '关键词检索（BM25）' },
  { value: 'hybrid', label: '混合检索（RRF）' },
]

export function embeddingLabel(value){return {'local-bge-m3':'BGE-M3 语义向量','semantic-api':'语义向量 API','local-tfidf':'TF-IDF 词项向量','not-used':'未调用（关键词模式）',none:'未执行'}[value]||value||'未配置'}
export function rerankLabel(value){return {'local-cross-encoder':'Cross-Encoder 模型重排','rank-aware-lexical':'轻量规则重排',none:'未启用'}[value]||value||'未配置'}

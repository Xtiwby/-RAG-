<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { labelOf } from '../ui'
import RetrievalDetails from '../components/RetrievalDetails.vue'
import SearchOriginalPreview from '../components/SearchOriginalPreview.vue'
import { embeddingLabel, rerankLabel } from '../retrieval'

const modes = [
  { value: 'vector', label: '向量检索' },
  { value: 'keyword', label: '关键字检索（BM25）' },
  { value: 'hybrid', label: '混合检索（RRF）' },
]
const query = ref('混合检索')
const mode = ref('hybrid')
const kbs = ref([])
const selected = ref([])
const rerank = ref(true)
const data = ref(null)
const activeChunk = ref(null)
const loading = ref(false)
const error = ref('')
const activeResult = computed(() => data.value?.results.find(item => item.chunk_id === activeChunk.value))

onMounted(async () => {
  try {
    const [bases, state] = await Promise.all([api('/api/knowledge-bases'), api('/api/model-status')])
    kbs.value = bases
    mode.value = state.retrieval.mode
    rerank.value = state.retrieval.rerank
  } catch (err) {
    error.value = err.message
  }
})

async function run() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api('/api/search', {
      method: 'POST',
      body: JSON.stringify({ query: query.value, mode: mode.value, knowledge_base_ids: selected.value, rerank: rerank.value }),
    })
    activeChunk.value = data.value.results[0]?.chunk_id ?? null
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="page-stack">
    <div class="page-intro">
      <div><h2>检索测试台</h2><p>定位命中的原文并高亮，权限过滤在服务端执行</p></div>
    </div>
    <form class="card search-console" @submit.prevent="run">
      <div class="search-row">
        <input v-model="query" aria-label="检索测试问题" class="input" placeholder="输入测试问题或关键字" required />
        <button class="primary-button" :disabled="loading">{{ loading ? '检索中…' : '开始检索' }}</button>
      </div>
      <div class="toolbar">
        <label v-for="item in modes" :key="item.value">
          <input v-model="mode" type="radio" :value="item.value" />{{ item.label }}
        </label>
      </div>
      <details class="search-options">
        <summary>知识范围与重排设置 · {{ selected.length ? '已选 ' + selected.length + ' 个知识库' : '全部授权知识库' }}</summary>
        <div class="toolbar">
          <label v-for="kb in kbs" :key="kb.id"><input v-model="selected" type="checkbox" :value="kb.id" />{{ kb.name }}</label>
          <small>未选时搜索全部授权知识库</small>
          <label><input v-model="rerank" type="checkbox" />启用重排（按系统配置）</label>
        </div>
      </details>
    </form>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="data" class="card">
      <div class="card-header">
        <div>
          <h3>检索结果</h3>
          <p>{{ data.total }} 条 · {{ data.elapsed_ms }} ms · {{ labelOf(data.mode) }} · 向量：{{ embeddingLabel(data.pipeline.embedding) }} · 重排：{{ rerankLabel(data.pipeline.rerank) }}</p>
        </div>
      </div>
      <div class="card-body">
        <RetrievalDetails :pipeline="data.pipeline" />
        <div v-if="activeResult" class="result-layout">
          <div class="result-list" role="group" aria-label="选择检索结果">
            <button v-for="(item, index) in data.results" :key="item.chunk_id" type="button"
              class="search-result result-option" :aria-pressed="item.chunk_id === activeChunk"
              @click="activeChunk = item.chunk_id">
              <strong>{{ index + 1 }}. {{ item.name }}</strong>
              <span class="result-score">{{ item.version }} · {{ item.kind === 'adjacent-context' ? '相邻原文' : `相关度 ${item.relevance}` }}</span>
              <small>{{ item.section }} · 片段 #{{ item.chunk_id }}</small>
            </button>
          </div>
          <SearchOriginalPreview :key="activeResult.chunk_id" :result="activeResult" />
        </div>
        <div v-else class="empty">没有找到足够相关的可用片段</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-console { padding: 22px; display: grid; gap: 18px; }
.search-row { display: flex; gap: 12px; }
.search-row input { flex: 1; min-width: 0; }
.search-row button { flex-shrink: 0; }
.search-console label { font-size: 13px; }
.search-console small { color: #657a88; }
.search-options { font-size: 12px; color: var(--muted); }
.search-options summary { cursor: pointer; }
.search-options .toolbar { margin-top: 14px; }
.result-layout { display: grid; grid-template-columns: minmax(0, 240px) minmax(0, 1fr); align-items: start; gap: 14px; }
.result-list { max-height: 70vh; overflow: auto; }
.result-option { width: 100%; padding: 12px; gap: 4px; background: white; color: inherit; cursor: pointer; text-align: left; overflow-wrap: anywhere; }
.result-option:hover, .result-option[aria-pressed='true'] { border-color: var(--primary); background: var(--primary-soft); }
.result-option small { color: var(--muted); }
@media (max-width: 900px) {
  .result-layout { grid-template-columns: minmax(0, 1fr); }
  .result-list { max-height: 220px; }
}
@media (max-width: 450px) {
  .search-console { padding: 16px; }
  .search-row { flex-direction: column; }
  .search-console > .toolbar { align-items: flex-start; flex-direction: column; gap: 12px; }
}
</style>

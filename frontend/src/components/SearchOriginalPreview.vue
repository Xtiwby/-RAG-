<script setup>
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import DocumentOriginal from './DocumentOriginal.vue'

const PdfCitationPreview = defineAsyncComponent(() => import('./PdfCitationPreview.vue'))
const props = defineProps({ result: { type: Object, required: true } })
const doc = ref(null)
const match = ref(null)
const error = ref('')
const context = ref(null)
const controller = new AbortController()
const presentation = computed(() => ['ppt', 'pptx'].includes(doc.value?.file_type))
const hasPage = computed(() => doc.value?.has_original &&
  (doc.value.file_type === 'pdf' || presentation.value) &&
  Number.isInteger(match.value?.page) && match.value.page > 0)
const hasImage = computed(() => doc.value?.has_original &&
  ['png', 'jpg', 'jpeg', 'webp'].includes(doc.value.file_type))

onMounted(async () => {
  const { document_id: documentId, chunk_id: chunkId } = props.result
  const { signal } = controller
  try {
    const result = await api(`/api/documents/${documentId}?compact=true&limit=3&chunk_id=${chunkId}`, { signal })
    if (signal.aborted) return
    const chunk = result.chunks.find(item => item.id === chunkId)
    if (!chunk) throw new Error('找不到对应的原文片段。')
    match.value = chunk
    doc.value = result
    await nextTick()
    if (context.value) {
      const selected = context.value.querySelector('mark')
      context.value.scrollTop = Math.max(0, selected.offsetTop - context.value.clientHeight / 3)
    }
  } catch (err) {
    if (!signal.aborted) error.value = err.message
  }
})

onUnmounted(() => controller.abort())
</script>

<template>
  <section class="search-preview" aria-label="检索原文预览">
    <header class="preview-heading">
      <h4>{{ result.name }} · {{ result.version }}</h4>
      <router-link class="text-button" :to="{ path: `/documents/${result.document_id}`, query: { chunk: result.chunk_id } }">
        查看文档详情
      </router-link>
    </header>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-else-if="!doc" class="muted" role="status">正在定位原文…</p>
    <PdfCitationPreview v-else-if="hasPage" :document-id="doc.id" :citation="match" :presentation="presentation" />
    <template v-else>
      <DocumentOriginal v-if="hasImage" :document-id="doc.id" :file-type="doc.file_type" />
      <p class="muted">{{ hasImage ? '图中缺少文字坐标，以下高亮解析出的命中文本。' : '此文档没有可定位的原文页，以下按片段显示原文上下文。' }}</p>
      <div ref="context" class="preview-context" tabindex="0" aria-label="原文上下文">
        <article v-for="chunk in doc.chunks" :key="chunk.id">
          <small>{{ chunk.section }} · 片段 #{{ chunk.id }}</small>
          <mark v-if="chunk.id === match.id">{{ chunk.text }}</mark>
          <p v-else>{{ chunk.text }}</p>
        </article>
      </div>
    </template>
  </section>
</template>

<style scoped>
.search-preview { min-width: 0; padding: 14px; border: 1px solid var(--line); border-radius: 6px; }
.preview-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.preview-heading h4 { margin: 0; overflow-wrap: anywhere; }
.preview-context { position: relative; max-height: 62vh; overflow: auto; padding: 14px; background: #f6f8fa; }
.preview-context article + article { margin-top: 16px; }
.preview-context small { color: var(--muted); }
.preview-context p, .preview-context mark { display: block; margin: 6px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.75; }
.preview-context mark { padding: 8px; background: #fff0a5; color: inherit; }
</style>

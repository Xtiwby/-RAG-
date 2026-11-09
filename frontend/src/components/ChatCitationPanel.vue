<script setup>
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { api } from '../api'

const PdfCitationPreview = defineAsyncComponent(() => import('./PdfCitationPreview.vue'))
const props = defineProps({
  sources: { type: Array, required: true },
  number: Number,
})
const emit = defineEmits(['select', 'close'])
const source = computed(() => props.sources.find(item => item.number === props.number))
const doc = ref(null)
const fragment = ref(null)
const loading = ref(false)
const error = ref('')
const closeButton = ref(null)
const presentation = computed(() => ['ppt', 'pptx'].includes(doc.value?.file_type))
const pdfPage = computed(() => (doc.value?.file_type === 'pdf' || presentation.value) && doc.value.has_original &&
  Number.isInteger(fragment.value?.page) && fragment.value.page > 0)

onMounted(() => closeButton.value?.focus({ preventScroll: true }))

watch([() => source.value?.document_id, () => source.value?.chunk_id], async ([docId, chunkId], _, onCleanup) => {
  doc.value = null
  fragment.value = null
  error.value = ''
  loading.value = false
  if (!source.value) return
  if (!Number.isSafeInteger(Number(docId)) || Number(docId) < 1 ||
      !Number.isSafeInteger(Number(chunkId)) || Number(chunkId) < 1) {
    error.value = '这条引用缺少原文定位信息。'
    return
  }

  const controller = new AbortController()
  onCleanup(() => controller.abort())
  loading.value = true
  try {
    const result = await api(`/api/documents/${Number(docId)}?compact=true&limit=1&include_context=true&chunk_id=${Number(chunkId)}`,
      { signal: controller.signal })
    if (controller.signal.aborted) return
    const chunk = result.chunks.find(item => Number(item.id) === Number(chunkId))
    if (!chunk) throw new Error('找不到对应的引用片段。')
    doc.value = result
    fragment.value = chunk
  } catch (err) {
    if (!controller.signal.aborted) error.value = err.message
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}, { immediate: true })
</script>

<template>
  <aside id="chat-citation-panel" class="card citation-panel" aria-labelledby="chat-citation-heading" @keydown.esc.stop="emit('close')">
    <header class="citation-header">
      <h3 id="chat-citation-heading">引用原文</h3>
      <button ref="closeButton" type="button" class="secondary-button" @click="emit('close')">关闭</button>
    </header>
    <div v-if="sources.length > 1" class="citation-options" role="group" aria-label="切换引用">
      <button v-for="item in sources" :key="item.number" type="button"
        class="secondary-button" :aria-pressed="item.number === number"
        @click="emit('select', item.number)">[{{ item.number }}]</button>
    </div>
    <div class="citation-body">
      <template v-if="source">
        <p class="citation-name">[{{ source.number }}] {{ doc?.name || source.name }}</p>
        <p v-if="loading" class="muted" role="status">正在查找引用页…</p>
        <p v-else-if="error" class="error-banner" role="alert">{{ error }}</p>
        <template v-else-if="fragment">
          <router-link class="text-button" :to="{path:`/documents/${doc.id}`,query:{chunk:fragment.id,view:'original'}}">打开原文页面</router-link>
          <PdfCitationPreview v-if="pdfPage" :key="`${doc.id}:${fragment.id}`" :document-id="doc.id" :citation="fragment" :presentation="presentation"/>
          <p v-else class="citation-quote"><span>{{doc.citation_context?.before}}</span><mark>{{fragment.text}}</mark><span>{{doc.citation_context?.after}}</span></p>
        </template>
      </template>
      <p v-else class="muted">当前回答没有引用编号。</p>
    </div>
  </aside>
</template>

<style scoped>
.citation-panel { display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
.citation-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--border); }
.citation-header h3 { margin: 0; }
.citation-options { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 16px 0; }
.citation-options [aria-pressed="true"] { border-color: var(--primary); background: var(--primary-soft); color: var(--primary); }
.citation-body { min-height: 0; overflow: auto; padding: 12px 16px 16px; }
.citation-name { font-weight: 600; margin: 0 0 6px; overflow-wrap: anywhere; }
.citation-quote { margin: 12px 0 0; padding: 12px; background: #f6f8fa; white-space: pre-wrap; overflow-wrap: anywhere; }
.citation-quote mark { background: #fff0a5; color: inherit; }
</style>

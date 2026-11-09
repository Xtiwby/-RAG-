<script setup>
import { onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import { getDocument, GlobalWorkerOptions, TextLayer } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { originalBlob, presentationPreviewFile } from '../api'
import { citationTextRanges } from '../pdf-citation'

const props = defineProps({
  documentId: { type: Number, required: true },
  citation: { type: Object, required: true },
  presentation: Boolean,
})

GlobalWorkerOptions.workerSrc = workerUrl
const pdf = shallowRef(null)
const viewer = ref(null)
const sheet = ref(null)
const width = ref(0)
const loading = ref(true)
const error = ref('')
const message = ref('')
const fallback = ref(false)
const controller = new AbortController()
let task, fileUrl, observer

onMounted(async () => {
  observer = new ResizeObserver(() => {
    if (viewer.value) width.value = Math.max(0, viewer.value.clientWidth - 24)
  })
  observer.observe(viewer.value)
  try {
    const url = props.presentation
      ? URL.createObjectURL(await presentationPreviewFile(props.documentId, controller.signal))
      : await originalBlob(props.documentId, controller.signal)
    if (controller.signal.aborted) {
      URL.revokeObjectURL(url)
      return
    }
    fileUrl = url
    const assets = new URL(`${import.meta.env.BASE_URL}pdfjs/`, document.baseURI)
    task = getDocument({
      url, isEvalSupported: false,
      cMapUrl: new URL('cmaps/', assets).href,
      standardFontDataUrl: new URL('standard_fonts/', assets).href,
      wasmUrl: new URL('wasm/', assets).href,
    })
    const pdfDocument = await task.promise
    if (!controller.signal.aborted) {
      if (props.citation.page > pdfDocument.numPages) {
        loading.value = false
        error.value = props.presentation ? '原文件中没有对应的幻灯片。' : '原 PDF 中没有该引用页。'
        return
      }
      pdf.value = pdfDocument
    }
  } catch (err) {
    if (!controller.signal.aborted) {
      loading.value = false
      error.value = props.presentation ? err.message || '无法打开幻灯片预览。' : '无法打开 PDF 原文件。'
    }
  }
})

onUnmounted(() => {
  controller.abort()
  observer?.disconnect()
  if (task) task.destroy().catch(() => {})
  if (fileUrl) URL.revokeObjectURL(fileUrl)
})

watch([pdf, width], async ([pdfDocument, available], _, onCleanup) => {
  if (!pdfDocument || !available) return
  let active = true
  let render, textLayer
  onCleanup(() => {
    active = false
    render?.cancel()
    textLayer?.cancel()
  })

  loading.value = true
  error.value = ''
  message.value = ''
  fallback.value = false
  sheet.value.replaceChildren()
  try {
    const page = await pdfDocument.getPage(props.citation.page)
    if (!active) return

    const natural = page.getViewport({ scale: 1 })
    const viewport = page.getViewport({ scale: Math.min(available, 900) / natural.width })
    const density = Math.min(window.devicePixelRatio || 1, 2, 4096 / Math.max(viewport.width, viewport.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.ceil(viewport.width * density))
    canvas.height = Math.max(1, Math.ceil(viewport.height * density))
    canvas.style.width = `${viewport.width}px`
    canvas.style.height = `${viewport.height}px`
    render = page.render({ canvas, viewport, transform: [density, 0, 0, density, 0, 0] })
    await render.promise
    if (!active) return

    sheet.value.style.setProperty('--total-scale-factor', viewport.scale * viewport.userUnit)
    sheet.value.replaceChildren(canvas)
    viewer.value.scrollTop = 0
    const layer = document.createElement('div')
    layer.className = 'pdf-text-layer'
    sheet.value.append(layer)
    try {
      textLayer = new TextLayer({ textContentSource: page.streamTextContent(), container: layer, viewport })
      await textLayer.render()
      if (!active) return
      const { ranges, partial } = citationTextRanges(textLayer.textContentItemsStr, props.citation.text)
      const first = highlightRanges(sheet.value, textLayer.textDivs, ranges)
      if (first === null) {
        fallback.value = true
        message.value = '已打开引用页，未能可靠匹配高亮位置。'
      } else {
        const format = props.presentation ? '幻灯片' : 'PDF'
        message.value = partial ? `已标出能与 ${format} 文字匹配的部分。` : `已在原 ${format} 中标出引用文字。`
        viewer.value.scrollTop = Math.max(0, first - viewer.value.clientHeight / 3)
      }
    } catch {
      if (active) {
        fallback.value = true
        message.value = '已打开引用页，无法读取文字位置。'
      }
    }
  } catch {
    if (active) error.value = props.presentation ? '无法渲染幻灯片。' : '无法渲染 PDF 引用页。'
  } finally {
    if (active) loading.value = false
  }
})

function highlightRanges(container, textDivs, ranges) {
  const origin = container.getBoundingClientRect()
  const marks = document.createDocumentFragment()
  let first = null
  for (const { item, start, end } of ranges) {
    const node = textDivs[item]?.firstChild
    if (node?.nodeType !== Node.TEXT_NODE || end > node.length) continue
    const range = new Range()
    range.setStart(node, start)
    range.setEnd(node, end)
    for (const rect of range.getClientRects()) {
      if (!rect.width || !rect.height) continue
      const top = rect.top - origin.top
      first = first === null ? top : Math.min(first, top)
      const mark = document.createElement('span')
      mark.className = 'pdf-mark'
      mark.setAttribute('aria-hidden', 'true')
      Object.assign(mark.style, {
        top: `${top}px`, left: `${rect.left - origin.left}px`,
        width: `${rect.width}px`, height: `${rect.height}px`,
      })
      marks.append(mark)
    }
  }
  container.append(marks)
  return first
}
</script>

<template>
  <section class="pdf-preview" :aria-label="presentation ? '引用幻灯片' : 'PDF 引用页'">
    <p class="pdf-page-number">{{ presentation ? `第 ${citation.page} 张幻灯片` : `PDF 第 ${citation.page} 页` }}</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-else class="muted" role="status">{{ loading ? (presentation ? '正在加载幻灯片…' : '正在加载 PDF 页面…') : message }}</p>
    <div ref="viewer" class="pdf-scroll" tabindex="0" :aria-label="presentation ? '幻灯片' : 'PDF 页面'">
      <div ref="sheet" class="pdf-sheet"></div>
    </div>
    <blockquote v-if="error || fallback" class="pdf-quote">{{ citation.text }}</blockquote>
  </section>
</template>

<style scoped>
.pdf-page-number { font-size: 13px; margin: 0 0 6px; }
.pdf-scroll { display: grid; justify-items: center; max-height: max(180px, calc(100dvh - 260px)); overflow: auto; scrollbar-gutter: stable; padding: 12px; background: #edf1f5; }
.pdf-scroll:has(.pdf-sheet:empty) { visibility: hidden; height: 0; padding-block: 0; }
.pdf-quote { margin: 12px 0 0; padding: 12px; border-left: 3px solid #d6a82c; background: #fff8da; white-space: pre-wrap; overflow-wrap: anywhere; }
.pdf-sheet {
  position: relative; isolation: isolate; background: white;
  --scale-round-x: 1px;
  --scale-round-y: 1px;
}
.pdf-sheet :deep(canvas) { display: block; }
.pdf-sheet :deep(.pdf-text-layer) {
  position: absolute; inset: 0; overflow: clip; text-align: initial; line-height: 1;
  text-size-adjust: none; z-index: 1; transform-origin: 0 0;
  --min-font-size: 1;
  --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
  --min-font-size-inv: calc(1 / var(--min-font-size));
}
.pdf-sheet :deep(.pdf-text-layer[data-main-rotation="90"]) { transform: rotate(90deg) translateY(-100%); }
.pdf-sheet :deep(.pdf-text-layer[data-main-rotation="180"]) { transform: rotate(180deg) translate(-100%, -100%); }
.pdf-sheet :deep(.pdf-text-layer[data-main-rotation="270"]) { transform: rotate(270deg) translateX(-100%); }
.pdf-sheet :deep(.pdf-text-layer :is(span, br)) {
  position: absolute; color: transparent; white-space: pre; transform-origin: 0 0;
}
.pdf-sheet :deep(.pdf-text-layer > span) {
  font-size: calc(var(--text-scale-factor) * var(--font-height, 0));
  transform: rotate(var(--rotate, 0deg)) scaleX(var(--scale-x, 1)) scale(var(--min-font-size-inv));
}
.pdf-sheet :deep(.pdf-mark) {
  position: absolute; z-index: 2; pointer-events: none;
  background: #ffdb4d99; mix-blend-mode: multiply; border-radius: 2px;
}
</style>

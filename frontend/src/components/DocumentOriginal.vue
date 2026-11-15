<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { originalFile, presentationPreviewFile } from '../api'

const props = defineProps({
  documentId: { type: Number, required: true },
  fileType: { type: String, required: true },
  page: { type: Number, default: 1 },
  startSeconds: { type: Number, default: 0 },
})
const mediaTypes = {
  pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp',
  bmp: 'image/bmp', tif: 'image/tiff', tiff: 'image/tiff', gif: 'image/gif',
  mp3: 'audio/mpeg', m4a: 'audio/mp4', wav: 'audio/wav', ogg: 'audio/ogg', flac: 'audio/flac', aac: 'audio/aac',
  mp4: 'video/mp4', mov: 'video/quicktime', mkv: 'video/x-matroska', webm: 'video/webm', avi: 'video/x-msvideo',
}
const isImage = mediaTypes[props.fileType]?.startsWith('image/')
const isPresentation = ['ppt', 'pptx'].includes(props.fileType)
const mediaType = isPresentation ? 'application/pdf' : mediaTypes[props.fileType]
const isAudio = Boolean(mediaType?.startsWith('audio/'))
const isVideo = Boolean(mediaType?.startsWith('video/'))
const isMedia = isAudio || isVideo
const isWeb = ['md', 'html', 'htm'].includes(props.fileType)
const supported = Boolean(mediaType || isWeb || ['txt', 'csv', 'json'].includes(props.fileType))
const loading = ref(false)
const error = ref('')
const url = ref('')
const text = ref('')
const web = ref('')
let controller

onMounted(() => { if (supported) load() })
onUnmounted(() => {
  controller?.abort()
  if (url.value) URL.revokeObjectURL(url.value)
})

async function load() {
  if (loading.value) return
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''
  loading.value = true
  error.value = ''
  controller = new AbortController()
  const { signal } = controller
  try {
    const fetchFile = isPresentation ? presentationPreviewFile : originalFile
    const file = await fetchFile(props.documentId, signal)
    if (signal.aborted) return
    if (mediaType) {
      url.value = URL.createObjectURL(file.slice(0, file.size, mediaType))
      return
    }
    if (file.size > 8 * 1024 * 1024) throw new Error('文件较大，请下载原文件查看。')
    const bytes = await file.arrayBuffer()
    if (signal.aborted) return
    const source = decodeText(bytes)
    if (!isWeb) {
      text.value = source
      return
    }
    const markdown = props.fileType === 'md'
    const html = markdown ? (await import('marked')).marked.parse(source) : source
    if (!signal.aborted) web.value = isolatedPage(html, markdown)
  } catch (err) {
    if (!signal.aborted) error.value = err.message || '无法预览原文件。'
  } finally {
    if (!signal.aborted) loading.value = false
  }
}

function seek(event) {
  // Citations carry a timestamp section such as 00:01:23 – 00:02:10.
  if (props.startSeconds > 0 && event.target.duration) {
    event.target.currentTime = Math.min(props.startSeconds, Math.max(0, event.target.duration - 1))
  }
}

function decodeText(bytes) {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    return new TextDecoder('gb18030').decode(bytes)
  }
}

function isolatedPage(html, markdown) {
  // Uploaded markup stays in an opaque iframe. Remove navigation directives
  // before composing the frame, and apply CSP before any uploaded content.
  const page = document.implementation.createHTMLDocument()
  page.documentElement.innerHTML = html
  page.querySelectorAll('base, meta[http-equiv], script, iframe, object, embed')
    .forEach(element => element.remove())
  const markdownStyle = markdown ? `<style>
    body{max-width:880px;margin:32px auto;padding:0 24px;font:16px/1.75 system-ui,sans-serif;color:#1e293b;overflow-wrap:anywhere}
    h1,h2,h3,h4{line-height:1.3;margin-top:1.5em} h1,h2{padding-bottom:.3em;border-bottom:1px solid #dfe5ec}
    pre{overflow:auto;padding:14px;background:#f4f6f9} blockquote{margin-inline:0;padding-inline-start:1em;border-inline-start:3px solid #ccd6e3}
    table{border-collapse:collapse}th,td{padding:6px 12px;border:1px solid #dfe5ec}img{max-width:100%}
  </style>` : ''
  page.head.insertAdjacentHTML('afterbegin', `<meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="referrer" content="no-referrer">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'">
    ${markdownStyle}`)
  return '<!doctype html>' + page.documentElement.outerHTML
}
</script>

<template>
  <section class="card original-preview" aria-label="原文件预览">
    <p v-if="!supported" class="preview-status">此格式请下载原文件，用对应的应用打开查看原始版式。</p>
    <p v-else-if="loading" class="preview-status" role="status">{{ isPresentation ? '正在生成幻灯片预览…' : '正在加载原文件…' }}</p>
    <div v-else-if="error" class="preview-status">
      <p role="alert">{{ error }}</p>
      <button type="button" class="secondary-button" @click="load">重试预览</button>
    </div>
    <template v-else-if="url">
      <div class="preview-actions">
        <a :href="isImage||isMedia ? url : url+'#page='+page" target="_blank" rel="noopener noreferrer" class="secondary-button">在新标签页打开</a>
      </div>
      <video v-if="isVideo" :src="url" controls preload="metadata" @loadedmetadata="seek"/>
      <audio v-else-if="isAudio" :src="url" controls preload="metadata" @loadedmetadata="seek"/>
      <img v-else-if="isImage" :src="url" alt="文档原图" @error="error='无法显示原图，请下载原文件查看。'"/>
      <iframe v-else :src="url+'#page='+page" :title="isPresentation ? '幻灯片预览' : 'PDF 原文件'"/>
    </template>
    <iframe v-else-if="isWeb" :srcdoc="web" sandbox="" referrerpolicy="no-referrer" title="原文排版预览"/>
    <pre v-else class="original-text">{{ text }}</pre>
  </section>
</template>

<style scoped>
.original-preview { overflow: hidden; }
.preview-status { padding: 20px; margin: 0; }
.preview-actions { display: flex; justify-content: flex-end; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.original-preview iframe { display: block; width: 100%; height: max(400px, 72vh); border: 0; }
.original-preview img { display: block; max-width: 100%; max-height: 80vh; height: auto; margin: 0 auto; }
.original-preview video { display: block; width: 100%; max-height: 76vh; background: #0b1220; }
.original-preview audio { display: block; width: 100%; padding: 18px; }
.original-text { margin: 0; padding: 22px; max-height: 72vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.75; }
</style>

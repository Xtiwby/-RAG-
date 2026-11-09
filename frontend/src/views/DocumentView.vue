<script setup>
import {computed,defineAsyncComponent,nextTick,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {api,download,can} from '../api'
import {labelOf} from '../ui'
import DocumentOriginal from '../components/DocumentOriginal.vue'
import ProcessingPanel from '../components/ProcessingPanel.vue'
const PdfCitationPreview=defineAsyncComponent(()=>import('../components/PdfCitationPreview.vue'))
const route=useRoute(),router=useRouter(),doc=ref(null),error=ref(''),busy=ref(false),pageOffset=ref(0)
const activePane=ref('original')
const citationMark=ref(null)
const pageSize=40
let loadSequence=0
const selected=(c)=>String(c.id)===String(route.query.chunk)
const citation=computed(()=>doc.value?.chunks.find(selected))
const originalPage=computed(()=>citation.value?.page||1)
const presentation=computed(()=>['ppt','pptx'].includes(doc.value?.file_type))
const pageCitation=computed(()=>doc.value?.has_original&&citation.value?.page>0&&(doc.value.file_type==='pdf'||presentation.value))
async function load(centerCitation=true){
  const sequence=++loadSequence,id=Number(route.params.id),chunkId=Number(route.query.chunk)
  error.value=''
  const params=new URLSearchParams({compact:'true',limit:String(pageSize),offset:String(pageOffset.value)})
  if(centerCitation&&Number.isInteger(chunkId)&&chunkId>0)params.set('chunk_id',String(chunkId))
  if(centerCitation&&route.query.view==='original')params.set('include_context','true')
  try{
    const result=await api(`/api/documents/${id}?${params}`)
    if(sequence!==loadSequence)return
    doc.value=result;pageOffset.value=result.chunk_offset
    if(!result.has_original&&activePane.value!=='citation')activePane.value='chunks'
    if(centerCitation&&chunkId>0&&activePane.value==='chunks')await scrollToCitation()
    if(centerCitation&&activePane.value==='citation')await scrollToOriginalMark()
  }catch(e){if(sequence!==loadSequence)return;error.value=e.message;doc.value=null}
}
watch([()=>route.params.id,()=>route.query.chunk,()=>route.query.view],([,chunk,view])=>{
  doc.value=null
  pageOffset.value=0
  activePane.value=Number(chunk)>0?(view==='original'?'citation':'chunks'):'original'
  load()
},{immediate:true})
async function scrollToCitation(){
  const chunkId=Number(route.query.chunk)
  if(!Number.isInteger(chunkId)||chunkId<=0)return
  await nextTick()
  if(activePane.value==='chunks'&&Number(route.query.chunk)===chunkId)
    document.getElementById(`chunk-${chunkId}`)?.scrollIntoView({block:'center',behavior:'smooth'})
}
function showChunks(){
  activePane.value='chunks'
  scrollToCitation()
}
async function scrollToOriginalMark(){
  await nextTick()
  if(activePane.value==='citation')citationMark.value?.scrollIntoView({block:'center'})
}
function showCitation(){
  if(activePane.value==='citation')return
  activePane.value='citation'
  if(!citation.value||!doc.value.citation_context)load()
  else scrollToOriginalMark()
}
async function page(direction){
  if(!doc.value)return
  pageOffset.value=Math.max(0,Math.min(pageOffset.value+direction*pageSize,Math.max(0,doc.value.chunk_count-pageSize)))
  await load(false)
}
async function save(){try{await download(`/api/documents/${doc.value.id}/original`,doc.value.name)}catch(e){error.value=e.message}}
async function vision(){busy.value=true;error.value='';try{const result=await api(`/api/documents/${doc.value.id}/extract-vision`,{method:'POST'});router.push(`/documents/${result.id}`)}catch(e){error.value=e.message}finally{busy.value=false}}
</script>
<template><div class="page-stack">
 <div class="page-intro"><div><h2>{{doc?.name||'文档原文'}}</h2><p>查看原文件、历史版本和检索片段</p></div><router-link to="/documents" class="secondary-button">返回文档列表</router-link></div>
 <div v-if="error" class="error-banner">{{error}}</div>
 <template v-if="doc">
  <div class="card metadata"><div class="toolbar"><span class="badge">{{doc.version}}</span><span>{{labelOf(doc.status)}}</span><select class="select versions" :value="doc.id" @change="router.push(`/documents/${$event.target.value}`)"><option v-for="v in doc.versions" :value="v.id" :key="v.id">{{v.version}} · {{v.created_at}}</option></select></div><details class="technical"><summary>文件校验信息</summary><p>原文件 SHA-256：<code>{{doc.sha256}}</code></p></details><p>{{doc.page_count?`共 ${doc.page_count} 个物理页 / 幻灯片`:'此格式不提供可靠物理页码，按章节和片段定位'}} · {{doc.chunk_count}} 个片段</p><p v-if="!doc.has_original" class="error-banner">原文件不可用；历史演示只保留当时的文本，上传未完成的记录可重新上传。</p><p v-if="doc.parse_error" class="error-banner">{{doc.parse_error}}</p><div class="toolbar"><button v-if="doc.has_original" class="secondary-button" @click="save">下载原文件</button><button v-if="can('kb.write')&&['png','jpg','jpeg','webp'].includes(doc.file_type)" class="primary-button" :disabled="busy" @click="vision">{{busy?'视觉解析中…':'用 Kimi 解析图片（需已配置密钥）'}}</button></div></div>
  <div v-if="doc.has_original||route.query.view==='original'" class="toolbar document-panes" role="group" aria-label="文档查看方式">
    <button v-if="route.query.view==='original'&&route.query.chunk" type="button" class="secondary-button" :aria-pressed="activePane==='citation'" @click="showCitation">引用原文</button>
    <button v-if="doc.has_original" type="button" class="secondary-button" :aria-pressed="activePane==='original'" @click="activePane='original'">{{route.query.view==='original'?'完整原文件':'原文'}}</button>
    <button type="button" class="secondary-button" :aria-pressed="activePane==='chunks'" @click="showChunks">检索片段</button>
  </div>
  <section v-if="activePane==='citation'&&citation" class="card document-context" aria-label="引用原文">
    <PdfCitationPreview v-if="pageCitation" :key="`${doc.id}:${citation.id}`" :document-id="doc.id" :citation="citation" :presentation="presentation"/>
    <template v-else>
      <h3>{{citation.section||'原文上下文'}}</h3>
      <p class="muted">以下显示提取的原文，黄色标出引用文字。</p>
      <p class="citation-context"><span>{{doc.citation_context?.before}}</span><mark ref="citationMark">{{citation.text}}</mark><span>{{doc.citation_context?.after}}</span></p>
    </template>
  </section>
  <DocumentOriginal v-else-if="doc.has_original&&activePane==='original'" :key="doc.id" :document-id="doc.id"
    :file-type="doc.file_type" :page="originalPage"/>
  <div v-else class="card document-context"><h3>原文上下文</h3><p>高亮块为引用命中的片段；前后片段保留上下文。片段重叠用于保留语义边界。</p><div v-if="doc.chunk_count" class="context-pager"><span>显示 {{doc.chunk_offset+1}}–{{doc.chunk_offset+doc.chunks.length}} / {{doc.chunk_count}} 个片段</span><div class="toolbar"><button class="secondary-button" :disabled="doc.chunk_offset===0" @click="page(-1)">上一组</button><button class="secondary-button" :disabled="doc.chunk_offset+doc.chunks.length>=doc.chunk_count" @click="page(1)">下一组</button></div></div><article v-for="c in doc.chunks" :id="`chunk-${c.id}`" :key="c.id" :class="{selected:selected(c)}"><small>{{c.section}} · {{c.page?`第 ${c.page} 页 / 幻灯片`:'无固定页码'}} · 片段 #{{c.id}} · 字符 {{c.char_start}}–{{c.char_end}}</small><mark v-if="selected(c)">{{c.text}}</mark><p v-else>{{c.text}}</p><details class="technical"><summary>片段校验信息</summary><small class="hash">片段 SHA-256：{{c.sha256}}</small></details></article><div v-if="!doc.chunks.length" class="empty">没有可检索文本，请等待后台处理或完成视觉解析。</div></div>
  <details class="card processing-detail" :open="['processing','processing_failed','uploading'].includes(doc.status)"><summary>处理进度与事件记录</summary><ProcessingPanel :key="doc.id" :document-id="doc.id" @changed="load()"/></details>
 </template>
 <p v-else-if="!error" class="muted" role="status">正在加载文档…</p>
</div></template>

<style scoped>
.document-context .citation-context{overflow-wrap:anywhere}
.document-context .citation-context mark{display:inline;margin:0;color:inherit}
</style>
<style scoped>.processing-detail{padding:16px}.processing-detail>summary,.technical>summary{cursor:pointer;font-size:12px;color:var(--muted)}.technical{margin:12px 0}.metadata,.document-context{padding:22px}.metadata p{font-size:13px;color:#566f80;overflow-wrap:anywhere}.versions{width:auto;max-width:100%}.document-context article{padding:20px;margin:12px 0;border:1px solid #e2e8ed;border-radius:6px;scroll-margin-top:100px}.document-context article.selected{border:2px solid #b58614;background:#fffaf0}.document-context p,.document-context mark{white-space:pre-wrap;line-height:1.9;font-size:14px}.document-context mark{display:block;background:#fff0a5;margin:12px 0}.document-context small{color:#617789}.hash{overflow-wrap:anywhere;display:block}.document-panes [aria-pressed="true"]{border-color:var(--primary);background:var(--primary-soft);color:var(--primary)}.context-pager{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:16px 0;color:var(--muted);font-size:12px}.context-pager .toolbar{gap:8px}</style>

<script setup>
import {nextTick,onUnmounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {api,download,originalBlob,can} from '../api'
import {labelOf} from '../ui'
import ProcessingPanel from '../components/ProcessingPanel.vue'
const route=useRoute(),router=useRouter(),doc=ref(null),error=ref(''),original=ref(''),busy=ref(false),pageOffset=ref(0)
const pageSize=40
let loadSequence=0
async function load(){
  const sequence=++loadSequence,id=Number(route.params.id),chunkId=Number(route.query.chunk)
  error.value=''
  if(doc.value&&doc.value.id!==id){if(original.value)URL.revokeObjectURL(original.value);original.value='';doc.value=null}
  const params=new URLSearchParams({compact:'true',limit:String(pageSize),offset:String(pageOffset.value)})
  if(Number.isInteger(chunkId)&&chunkId>0)params.set('chunk_id',String(chunkId))
  try{
    const result=await api(`/api/documents/${id}?${params}`)
    if(sequence!==loadSequence)return
    doc.value=result;pageOffset.value=result.chunk_offset
    await nextTick()
    if(chunkId>0)document.getElementById(`chunk-${chunkId}`)?.scrollIntoView({block:'center',behavior:'smooth'})
  }catch(e){if(sequence!==loadSequence)return;error.value=e.message;doc.value=null}
}
watch(()=>route.params.id,()=>{pageOffset.value=0;load()},{immediate:true})
watch(()=>route.query.chunk,()=>{pageOffset.value=0;load()})
async function page(direction){
  if(!doc.value)return
  pageOffset.value=Math.max(0,Math.min(pageOffset.value+direction*pageSize,Math.max(0,doc.value.chunk_count-pageSize)))
  if(route.query.chunk)await router.replace({path:`/documents/${route.params.id}`})
  else await load()
}
onUnmounted(()=>{if(original.value)URL.revokeObjectURL(original.value)})
async function preview(){try{original.value=await originalBlob(doc.value.id)}catch(e){error.value=e.message}}
async function save(){try{await download(`/api/documents/${doc.value.id}/original`,doc.value.name)}catch(e){error.value=e.message}}
async function vision(){busy.value=true;error.value='';try{const result=await api(`/api/documents/${doc.value.id}/extract-vision`,{method:'POST'});router.push(`/documents/${result.id}`)}catch(e){error.value=e.message}finally{busy.value=false}}
const selected=(c)=>String(c.id)===String(route.query.chunk)
</script>
<template><div class="page-stack">
 <div class="page-intro"><div><h2>{{doc?.name||'文档原文'}}</h2><p>版本、处理流水线、结构化文本与引用片段定位</p></div><router-link to="/documents" class="secondary-button">返回文档列表</router-link></div>
 <div v-if="error" class="error-banner">{{error}}</div>
 <template v-if="doc">
  <details class="card processing-detail" :open="['processing','processing_failed','uploading'].includes(doc.status)"><summary>处理进度与事件记录</summary><ProcessingPanel :key="doc.id" :document-id="doc.id" @changed="load"/></details>
  <div class="card metadata"><div class="toolbar"><span class="badge">{{doc.version}}</span><span>{{labelOf(doc.status)}}</span><select class="select versions" :value="doc.id" @change="router.push(`/documents/${$event.target.value}`)"><option v-for="v in doc.versions" :value="v.id" :key="v.id">{{v.version}} · {{v.created_at}}</option></select></div><details class="technical"><summary>文件校验信息</summary><p>原文件 SHA-256：<code>{{doc.sha256}}</code></p></details><p>{{doc.page_count?`共 ${doc.page_count} 个物理页 / 幻灯片`:'此格式不提供可靠物理页码，按章节和片段定位'}} · {{doc.chunk_count}} 个片段</p><p v-if="!doc.has_original" class="error-banner">原文件不可用；历史演示只保留当时的文本，上传未完成的记录可重新上传。</p><p v-if="doc.parse_error" class="error-banner">{{doc.parse_error}}</p><div class="toolbar"><button v-if="doc.has_original" class="secondary-button" @click="preview">查看原文件</button><button v-if="doc.has_original" class="secondary-button" @click="save">下载原文件</button><button v-if="can('kb.write')&&['png','jpg','jpeg','webp'].includes(doc.file_type)" class="primary-button" :disabled="busy" @click="vision">{{busy?'视觉解析中…':'用 Kimi 解析图片（需已配置密钥）'}}</button></div></div>
  <div v-if="original" class="card original-preview"><img v-if="['png','jpg','jpeg','webp'].includes(doc.file_type)" :src="original" alt="文档原图"/><iframe v-else-if="['pdf','txt','md','csv'].includes(doc.file_type)" :src="original+(doc.file_type==='pdf'?`#page=${doc.chunks.find(selected)?.page||1}`:'')" title="原文件预览"/><p v-else>浏览器不支持此格式的原生预览，请下载后打开；下方可核验结构化文本。</p></div>
  <div class="card document-context"><h3>原文上下文</h3><p>高亮块为引用命中的片段；前后片段保留上下文。片段重叠用于保留语义边界。</p><div v-if="doc.chunk_count" class="context-pager"><span>显示 {{doc.chunk_offset+1}}–{{doc.chunk_offset+doc.chunks.length}} / {{doc.chunk_count}} 个片段</span><div class="toolbar"><button class="secondary-button" :disabled="doc.chunk_offset===0" @click="page(-1)">上一组</button><button class="secondary-button" :disabled="doc.chunk_offset+doc.chunks.length>=doc.chunk_count" @click="page(1)">下一组</button></div></div><article v-for="c in doc.chunks" :id="`chunk-${c.id}`" :key="c.id" :class="{selected:selected(c)}"><small>{{c.section}} · {{c.page?`第 ${c.page} 页 / 幻灯片`:'无固定页码'}} · 片段 #{{c.id}} · 字符 {{c.char_start}}–{{c.char_end}}</small><mark v-if="selected(c)">{{c.text}}</mark><p v-else>{{c.text}}</p><details class="technical"><summary>片段校验信息</summary><small class="hash">片段 SHA-256：{{c.sha256}}</small></details></article><div v-if="!doc.chunks.length" class="empty">没有可检索文本，请等待后台处理或完成视觉解析。</div></div>
 </template>
</div></template>
<style scoped>.processing-detail{padding:16px}.processing-detail>summary,.technical>summary{cursor:pointer;font-size:12px;color:var(--muted)}.technical{margin:12px 0}.metadata,.document-context,.original-preview{padding:22px}.metadata p{font-size:13px;color:#566f80;overflow-wrap:anywhere}.versions{width:auto;max-width:100%}.document-context article{padding:20px;margin:12px 0;border:1px solid #e2e8ed;border-radius:6px;scroll-margin-top:100px}.document-context article.selected{border:2px solid #b58614;background:#fffaf0}.document-context p,.document-context mark{white-space:pre-wrap;line-height:1.9;font-size:14px}.document-context mark{display:block;background:#fff0a5;margin:12px 0}.document-context small{color:#617789}.hash{overflow-wrap:anywhere;display:block}.original-preview img{max-width:100%;max-height:700px}.original-preview iframe{border:0;width:100%;height:650px}.context-pager{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:16px 0;color:var(--muted);font-size:12px}.context-pager .toolbar{gap:8px}</style>

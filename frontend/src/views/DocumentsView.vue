<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {Modal,Table,message} from 'ant-design-vue'
import {api,can,uploadDocument} from '../api'
import {confirmAction,labelOf,fileSize} from '../ui'
import {FileUp,FileText,Search} from '../icons'
import ProcessingPanel from '../components/ProcessingPanel.vue'
const route=useRoute(),router=useRouter(),docs=ref([]),kbs=ref([]),error=ref(''),loading=ref(true)
const filter=ref(String(route.query.kb||'')),query=ref(String(route.query.q||'')),status=ref(String(route.query.status||''))
const uploadKb=ref(''),file=ref(null),uploading=ref(false),uploadError=ref(''),progress=ref(null)
const generateVectors=ref(null)
const tracking=ref(null),showUpload=ref(route.query.upload==='1')
const writable=computed(()=>kbs.value.filter(k=>k.access==='write'&&can('kb.write')))
const filtered=computed(()=>docs.value.filter(d=>(!filter.value||String(d.knowledge_base_id)===String(filter.value))&&(!query.value||(d.name+' '+d.knowledge_base_name).toLowerCase().includes(query.value.toLowerCase()))&&(!status.value||d.status===status.value)))
const columns=[{title:'文档名称',key:'name',width:300},{title:'知识库',dataIndex:'knowledge_base_name',width:160},{title:'类型',key:'type',width:85},{title:'版本',dataIndex:'version',width:85},{title:'大小',key:'size',width:90},{title:'处理状态',key:'status',width:150},{title:'操作',key:'actions',width:135}]
const stageNames={upload:'上传',parse:'解析',chunk:'切分',index:'索引'}
let timer=null,loadingList=false,closing=false
async function load(quiet=false){if(loadingList)return;loadingList=true;if(!quiet)loading.value=true;try{[docs.value,kbs.value]=await Promise.all([api('/api/documents'),api('/api/knowledge-bases')]);if(!uploading.value&&!writable.value.some(k=>k.id===uploadKb.value))uploadKb.value=writable.value.find(k=>String(k.id)===filter.value)?.id||writable.value[0]?.id||'';error.value=''}catch(e){error.value=e.message}finally{loading.value=false;loadingList=false}}
function resetTransfer(){
  uploadError.value=''
  progress.value=null
}
function selectFile(event){
  file.value=event.target.files[0]||null
  resetTransfer()
}
async function closeDialog(){
  if(uploading.value||closing)return
  closing=true
  try{
    if(!tracking.value&&file.value&&!await confirmAction('关闭后需要重新选择文件。',{title:'关闭上传？',okText:'关闭上传'}))return
    showUpload.value=false
    tracking.value=null
    file.value=null
    resetTransfer()
  }finally{closing=false}
}
async function upload(){
  if(uploading.value||tracking.value)return
  resetTransfer()
  const library=writable.value.find(k=>k.id===uploadKb.value)
  if(!file.value||!library){uploadError.value='请选择知识库和文件';return}
  if(file.value.size>100*1024*1024){uploadError.value='文件不能超过100MB';return}
  const form=new FormData()
  form.append('knowledge_base_id',library.id)
  form.append('file',file.value)
  if(generateVectors.value!==null)form.append('generate_vectors',String(generateVectors.value))
  uploading.value=true
  progress.value=0
  try{
    const result=await uploadDocument(form,value=>progress.value=value)
    tracking.value={id:result.id,name:result.name,knowledge_base_name:library.name}
    file.value=null
    load(true)
  }catch(e){uploadError.value=e.message}
  finally{uploading.value=false}
}
async function remove(doc){if(!await confirmAction('将“'+doc.name+' '+doc.version+'”移出检索？原文件和历史引用仍保留。',{title:'移出检索',danger:true,okText:'确认移出'}))return;try{await api('/api/documents/'+doc.id,{method:'DELETE'});message.success('文档已移出检索');await load()}catch(e){error.value=e.message}}
watch(showUpload,visible=>{if(!visible&&route.query.upload){const {upload,...rest}=route.query;router.replace({path:'/documents',query:rest})}})
watch([filter,query,status],()=>{router.replace({path:'/documents',query:{...(query.value?{q:query.value}:{}),...(filter.value?{kb:filter.value}:{}),...(status.value?{status:status.value}:{})}})})
onMounted(()=>{load();timer=setInterval(()=>{if(docs.value.some(d=>['uploading','processing'].includes(d.status)))load(true)},1500)})
onUnmounted(()=>clearInterval(timer))
</script>
<template><div class="page-stack">
 <div class="page-intro"><div><h2>文档管理</h2><p>上传资料、跟踪处理进度，按版本查看原文与引用。</p></div><button v-if="writable.length" class="primary-button" @click="showUpload=true"><FileUp :size="16"/>上传文档</button></div>
 <div v-if="error" class="error-banner" role="alert">{{error}} <button class="text-button" @click="load()">重试加载</button></div>
 <section class="card"><div class="filter-bar"><div class="document-search"><Search :size="16"/><input v-model="query" class="input" placeholder="搜索文档名称或知识库" aria-label="搜索文档"/></div><select v-model="filter" class="select" aria-label="文档所属知识库"><option value="">全部授权知识库</option><option v-for="kb in kbs" :key="kb.id" :value="String(kb.id)">{{kb.name}}</option></select><select v-model="status" class="select" aria-label="文档处理状态"><option value="">全部处理状态</option><option value="ready">已就绪</option><option value="processing">处理中</option><option value="processing_failed">处理失败</option><option value="needs_extraction">待视觉解析</option></select><span class="muted">{{filtered.length}} 个文档版本</span><button class="text-button" @click="load()">刷新</button></div>
 <Table :columns="columns" :data-source="filtered" row-key="id" :loading="loading" :scroll="{x:1000}" :pagination="{defaultPageSize:10,showSizeChanger:true,pageSizeOptions:['10','20','50'],showTotal:total=>'共 '+total+' 个文档版本',hideOnSinglePage:true}" size="middle"><template #bodyCell="{column,record}"><template v-if="column.key==='name'"><div class="document-name"><FileText :size="20"/><div><router-link :to="'/documents/'+record.id" class="table-title">{{record.name}}</router-link><small class="table-description">{{record.has_original?'原文件已保留':'历史资料 · 原文件不可用'}}</small></div></div></template><template v-else-if="column.key==='type'">{{labelOf(record.modality)}}</template><template v-else-if="column.key==='size'">{{fileSize(record.size)}}</template><template v-else-if="column.key==='status'"><span class="badge" :class="record.status==='ready'?'green':record.status.includes('failed')?'red':'amber'">{{labelOf(record.status)}}</span><button class="progress-link" @click="tracking=record">{{record.processing_stage?(stageNames[record.processing_stage]||record.processing_stage)+' · '+record.processing_progress+'%':'查看处理记录'}}</button></template><template v-else-if="column.key==='actions'"><div class="table-actions"><router-link :to="'/documents/'+record.id" class="text-button">{{record.has_original?'查看原文':'查看详情'}}</router-link><button v-if="writable.some(k=>k.id===record.knowledge_base_id)" class="text-button danger" @click="remove(record)">移出检索</button></div></template></template><template #emptyText><div class="empty">{{query||filter||status?'没有匹配的文档，试试调整筛选条件。':'暂无可见文档，上传资料或联系管理员授权。'}}</div></template></Table></section>
 <Modal :open="showUpload||!!tracking" :title="showUpload?'上传文档':'处理详情'"
  centered :width="820" :footer="null" :mask-closable="false" :closable="!uploading" :keyboard="!uploading" @cancel="closeDialog">
  <div class="document-dialog">
   <form v-if="showUpload&&!tracking" id="document-upload-form" class="drawer-form" @submit.prevent="upload">
    <p class="section-description">选择知识库和文件，上传后依次解析、切分并建立检索索引。</p>
    <label class="field">目标知识库
     <select v-model="uploadKb" class="select" required :disabled="uploading" @change="resetTransfer">
      <option value="" disabled>选择可维护的知识库</option>
      <option v-for="kb in writable" :key="kb.id" :value="kb.id">{{kb.name}}</option>
     </select>
    </label>
    <label class="field">上传后生成语义向量
     <select v-model="generateVectors" class="select" :disabled="uploading" @change="resetTransfer">
      <option :value="null">按系统配置</option>
      <option :value="true">生成 BGE-M3 语义向量</option>
      <option :value="false">仅切分并建立文本索引</option>
     </select>
     <span class="help">上传时生成语义向量需要先下载 BGE-M3，并在系统配置中启用本地向量后端。</span>
    </label>
    <label class="upload-zone">
     <FileUp :size="24"/>
     <strong>{{file?file.name:'选择要上传的文件'}}</strong>
     <span>{{file?fileSize(file.size):'单个文件不超过100MB'}}</span>
     <input type="file" aria-label="知识文件" accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.html,.ppt,.pptx,.png,.jpg,.jpeg,.webp" :disabled="uploading" @change="selectFile"/>
    </label>
    <details v-if="progress===null" class="muted"><summary>支持哪些格式？</summary><p>PDF、Word DOCX、TXT、Markdown、CSV、Excel、HTML、PPT/PPTX及图片。幻灯片中的图片需另行视觉解析，不执行宏。</p></details>
   </form>
   <dl v-if="tracking" class="document-summary">
    <div><dt>目标知识库</dt><dd>{{tracking.knowledge_base_name}}</dd></div>
    <div><dt>文件名</dt><dd>{{tracking.name}}</dd></div>
   </dl>
   <div v-if="uploadError&&progress===null" class="error-banner" role="alert">{{uploadError}}</div>
   <ProcessingPanel v-if="tracking||progress!==null" :document-id="tracking?.id"
    :upload-progress="progress" :upload-error="uploadError" @changed="load(true)"/>
   <p v-if="tracking" class="section-description">关闭弹窗后，后台处理会继续。可从文档列表重新查看进度。</p>
   <div class="drawer-actions">
    <button v-if="!uploading" type="button" class="secondary-button" @click="closeDialog">{{tracking?'关闭':'取消'}}</button>
    <button v-if="showUpload&&!tracking" type="submit" form="document-upload-form" class="primary-button" :disabled="uploading||!file||!uploadKb">
     {{uploading?'上传中':uploadError&&progress!==null?'重新上传':'上传并处理'}}
    </button>
   </div>
  </div>
 </Modal>
</div></template>
<style scoped>.document-search{display:flex;align-items:center;position:relative;width:260px}.document-search>.ui-icon{position:absolute;left:10px;color:#99a1ae}.document-search .input{width:100%;padding-left:34px}.document-name{display:flex;align-items:center;gap:12px}.document-name>.ui-icon{color:#79a0d9;flex-shrink:0}.document-name>div{min-width:0}.progress-link{display:block;border:0;background:none;color:#1677ff;padding:4px 0 0;font-size:11px}.upload-zone{border:1px dashed #b9d3f7;border-radius:8px;background:#fafcff;min-height:132px;padding:20px;display:grid;justify-items:center;gap:10px;cursor:pointer;text-align:center;color:#626b79}.upload-zone .ui-icon{color:#6a9ee8}.upload-zone strong{font-size:14px;font-weight:500;overflow-wrap:anywhere;max-width:100%}.upload-zone span{font-size:12px}.upload-zone input{max-width:100%;font-size:12px}.document-dialog{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;max-height:calc(100dvh - 140px);overflow:auto}.document-summary{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:12px;margin:0}.document-summary div{padding:10px 12px;background:#f5f8fb;border-radius:6px}.document-summary dt{color:#657a88;font-size:12px}.document-summary dd{margin:4px 0 0;overflow-wrap:anywhere}@media(max-width:767px){.document-search{width:100%}}</style>

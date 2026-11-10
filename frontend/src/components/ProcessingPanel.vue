<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {api,can} from '../api'
const props=defineProps({
  documentId:{type:Number,default:null},
  uploadProgress:{type:Number,default:null},
  uploadError:{type:String,default:''}
})
const emit=defineEmits(['changed'])
const data=ref(null),error=ref(''),busy=ref(false),loading=ref(false)
const stages={upload:'上传',parse:'解析',chunk:'切分',index:'索引'}
const states={pending:'等待上一步',queued:'排队中',running:'执行中',completed:'完成',failed:'失败',interrupted:'中断',cancelled:'已取消',needs_extraction:'待视觉解析'}
const failedStates=['failed','interrupted','cancelled']
const visibleStages=computed(()=>{
  if(data.value?.events.length)return data.value.stages
  if(props.uploadProgress===null)return []
  const saved=props.documentId!==null
  return [
    {stage:'upload',status:saved?'completed':props.uploadError?'failed':'running',
      progress:saved?100:props.uploadProgress,
      message:saved?'原文件已保存':props.uploadError?'文件上传失败':props.uploadProgress===100?'等待服务器确认':'正在传输文件'},
    {stage:'parse',status:'pending',progress:0},
    {stage:'chunk',status:'pending',progress:0},
    {stage:'index',status:'pending',progress:0}
  ]
})
let timer=null,request=null
async function load(clearError=true){
  if(!props.documentId||loading.value)return
  const controller=new AbortController()
  request=controller
  loading.value=true
  try{
    const next=await api(`/api/documents/${props.documentId}/processing`,{signal:controller.signal})
    if(controller.signal.aborted)return
    const statusChanged=data.value?.status!==next.status
    data.value=next
    if(clearError)error.value=''
    if(statusChanged)emit('changed')
  }catch(e){
    if(!controller.signal.aborted)error.value=e.message
  }finally{
    if(request===controller){request=null;loading.value=false}
  }
}
async function retry(){
  busy.value=true
  try{
    await api(`/api/tasks/${data.value.job.id}/retry`,{method:'POST'})
    await load()
  }catch(e){error.value=e.message}
  finally{busy.value=false}
}
async function continueVision(){
  busy.value=true
  error.value=''
  try{
    await api(`/api/documents/${props.documentId}/vision-processing`,{method:'POST'})
    await load()
  }catch(e){error.value=e.message}
  finally{busy.value=false}
}
watch(()=>props.documentId,()=>{
  request?.abort()
  request=null
  loading.value=false
  data.value=null
  error.value=''
  load()
},{immediate:true})
onMounted(()=>{
  timer=setInterval(()=>{
    if(!busy.value&&(data.value?.status==='uploading'||['queued','running'].includes(data.value?.job?.status)))load(false)
  },1200)
})
onUnmounted(()=>{
  clearInterval(timer)
  request?.abort()
})
</script>
<template>
 <section class="processing-panel">
  <div class="toolbar">
   <h3>文档处理进度</h3>
   <button v-if="documentId" type="button" class="secondary-button" :disabled="loading||busy" @click="load">刷新进度</button>
   <button v-if="data?.can_continue_vision" type="button" class="primary-button"
    :disabled="loading||busy" @click="continueVision">开始视觉解析</button>
   <button v-else-if="can('kb.write')&&data?.job&&failedStates.includes(data.job.status)" type="button" class="primary-button"
    :disabled="loading||busy||data.job.attempts>=data.job.max_attempts" @click="retry">重试处理任务</button>
  </div>
  <p v-if="data?.can_continue_vision" class="help">点击后会使用已保存的原图，发送到系统配置的模型服务。解析成功后会自动切分并建立索引。</p>
  <div v-if="error||uploadError" class="error-banner" role="alert">{{error||uploadError}}</div>
  <p v-if="data">总体 {{data.progress}}% · 后台任务 #{{data.job?.id||'—'}}<span v-if="data.job"> · 尝试 {{data.job.attempts}} / {{data.job.max_attempts}}</span></p>
  <p v-else-if="documentId&&loading" role="status">正在读取处理进度…</p>
  <p v-if="data&&!data.events.length&&!visibleStages.length" class="help">此文档没有阶段事件。</p>
  <div v-if="visibleStages.length" class="stage-grid">
   <article v-for="stage in visibleStages" :key="stage.stage">
    <strong>{{stages[stage.stage]}}</strong>
    <span class="badge" :class="stage.status==='completed'?'green':failedStates.includes(stage.status)?'red':stage.status==='pending'?'':'amber'">{{states[stage.status]||stage.status}}</span>
    <progress :aria-label="stages[stage.stage]+'进度'" :value="stage.progress" max="100"/>
    <small>{{stage.progress}}%<template v-if="stage.message"> · {{stage.message}}</template></small>
   </article>
  </div>
  <details v-if="data?.events.length">
   <summary>完整事件记录 · {{data.events.length}} 条</summary>
   <div class="table-wrap">
    <table>
     <thead><tr><th>时间</th><th>尝试</th><th>阶段</th><th>状态 / 进度</th><th>说明</th></tr></thead>
     <tbody><tr v-for="event in data.events" :key="event.id"><td>{{new Date(event.created_at).toLocaleString()}}</td><td>{{event.attempt}}</td><td>{{stages[event.stage]}}</td><td>{{states[event.status]||event.status}} · {{event.progress}}%</td><td>{{event.message}}</td></tr></tbody>
    </table>
   </div>
  </details>
  <div v-if="data?.error&&data.status!=='needs_extraction'" class="error-banner" role="alert">{{data.error}}</div>
 </section>
</template>
<style scoped>.processing-panel{padding:20px;background:#f5f8fb;border:1px solid #dae4ec;border-radius:6px}.processing-panel h3{margin:0}.processing-panel p,.help{font-size:12px;color:#657a88}.stage-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:15px 0}.stage-grid article{padding:15px;border:1px solid #dce6ed;border-radius:6px;background:white}.stage-grid strong{display:block;margin-bottom:9px}.stage-grid small{display:block;line-height:1.7;overflow-wrap:anywhere;color:#657a88}progress{display:block;width:100%;margin:12px 0;accent-color:#1677ff}.processing-panel summary{cursor:pointer;font-size:13px}td{font-size:12px}.table-wrap table{min-width:650px}@media(max-width:800px){.stage-grid{grid-template-columns:1fr 1fr}}</style>

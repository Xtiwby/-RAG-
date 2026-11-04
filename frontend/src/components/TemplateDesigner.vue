<script setup>
import {onMounted,onUnmounted,ref} from 'vue'
import {api,sessionUser} from '../api'
const props=defineProps({initialJobId:{type:Number,default:null}})
const emit=defineEmits(['close','saved'])
const prompt=ref('为第一周项目汇报设计一份技术方案报告模板，包括项目背景、需求分析、技术路线、敏捷迭代计划和风险。用 A4 竖版、宋体，包含封面。')
const constraints=ref({paper:'',orientation:'',font:'',font_size_pt:'',line_spacing:'',cover:''})
const plan=ref(null),job=ref(null),loading=ref(false),saving=ref(false),error=ref('')
let timer=null
const key=()=>`rag_template_design_${sessionUser.value?.id}`
async function poll(){
  try{
    job.value=await api(`/api/tasks/${job.value.id}`)
    if(job.value.kind!=='template_design')throw new Error('该任务不是模板设计任务')
    if(job.value.status==='completed'){
      plan.value=job.value.result.blueprint;loading.value=false
    }else if(['failed','cancelled','interrupted'].includes(job.value.status)){
      error.value=job.value.error||'设计未完成';loading.value=false
    }else{timer=setTimeout(poll,1200)}
  }catch(e){error.value=e.message;loading.value=false}
}
async function design(){
  loading.value=true;error.value='';plan.value=null;clearTimeout(timer)
  const locked={}
  for(const[k,v]of Object.entries(constraints.value)){
    if(v!=='')locked[k]=['font_size_pt','line_spacing'].includes(k)?Number(v):k==='cover'?v==='true':v
  }
  try{
    const result=await api('/api/templates/design',{method:'POST',body:JSON.stringify({prompt:prompt.value,constraints:locked})})
    job.value={id:result.job_id,status:'queued'};localStorage.setItem(key(),String(result.job_id));await poll()
  }catch(e){error.value=e.message;loading.value=false}
}
async function retry(){
  error.value='';loading.value=true
  try{await api(`/api/tasks/${job.value.id}/retry`,{method:'POST'});await poll()}catch(e){error.value=e.message;loading.value=false}
}
async function save(){
  error.value='';saving.value=true
  try{
    const result=await api('/api/templates',{method:'POST',body:JSON.stringify({name:plan.value.name,description:plan.value.description,sections:plan.value.sections.map(s=>s.title),blueprint:plan.value})})
    localStorage.removeItem(key());emit('saved',result.id)
  }catch(e){error.value=e.message}finally{saving.value=false}
}
onMounted(async()=>{
  const stored=props.initialJobId||Number(localStorage.getItem(key()))
  if(stored){job.value={id:stored};loading.value=true;await poll()}
})
onUnmounted(()=>clearTimeout(timer))
</script>
<template>
  <div class="modal-backdrop"><section class="modal designer">
    <header class="modal-header"><h3>自然语言设计报告模板</h3><button class="icon-button" @click="emit('close')">×</button></header>
    <div class="modal-body">
      <div v-if="error" class="error-banner">{{error}}</div>
      <form class="design-input" @submit.prevent="design">
        <label class="field">描述报告用途、章节和格式要求<textarea v-model="prompt" class="textarea" minlength="5" maxlength="5000" required :disabled="loading"/></label>
        <div class="format-grid">
          <label class="field">纸张约束<select v-model="constraints.paper" class="select" :disabled="loading"><option value="">由模型建议</option><option>A4</option><option>Letter</option></select></label>
          <label class="field">方向约束<select v-model="constraints.orientation" class="select" :disabled="loading"><option value="">由模型建议</option><option value="portrait">竖版</option><option value="landscape">横版</option></select></label>
          <label class="field">字体约束<select v-model="constraints.font" class="select" :disabled="loading"><option value="">由模型建议</option><option>宋体</option><option>仿宋</option><option>等线</option><option>Arial</option></select></label>
          <label class="field">字号约束<input v-model="constraints.font_size_pt" class="input" type="number" min="10" max="18" placeholder="10–18 pt，可留空" :disabled="loading"/></label>
          <label class="field">行距约束<input v-model="constraints.line_spacing" class="input" type="number" min="1" max="2.5" step="0.1" placeholder="可留空" :disabled="loading"/></label>
          <label class="field">封面约束<select v-model="constraints.cover" class="select" :disabled="loading"><option value="">由模型建议</option><option value="true">需要封面</option><option value="false">不需要封面</option></select></label>
        </div>
        <p class="help">用途、格式约束及允许的参数 Schema 会发送给 Kimi。返回值经过类型和范围校验，显式约束优先。生成的是可编辑模板，不包含虚构项目进展。</p>
        <button class="primary-button" :disabled="loading">{{loading?'任务执行中…':'调用 Kimi 设计模板'}}</button>
      </form>
      <div v-if="job" class="job-status">任务 #{{job.id}} · {{job.status}} · {{job.progress||0}}%<span v-if="loading">（关闭页面后任务仍继续）</span><button v-if="!loading&&['failed','interrupted','cancelled'].includes(job.status)" class="secondary-button" :disabled="job.attempts>=job.max_attempts" @click="retry">重试设计</button></div>
      <form v-if="plan" class="blueprint-form" @submit.prevent="save">
        <h3>校验后的模板参数（可修改）</h3>
        <label class="field">模板名称<input v-model="plan.name" class="input" required maxlength="100"/></label>
        <label class="field">描述<input v-model="plan.description" class="input" maxlength="1000"/></label>
        <label class="field">写作目的<textarea v-model="plan.purpose" class="textarea" maxlength="2000"/></label>
        <div class="format-grid">
          <label class="field">纸张<select v-model="plan.format.paper" class="select"><option>A4</option><option>Letter</option></select></label>
          <label class="field">方向<select v-model="plan.format.orientation" class="select"><option value="portrait">竖版</option><option value="landscape">横版</option></select></label>
          <label class="field">正文字体<select v-model="plan.format.font" class="select"><option>宋体</option><option>仿宋</option><option>等线</option><option>Arial</option></select></label>
          <label class="field">正文字号（pt）<input v-model.number="plan.format.font_size_pt" class="input" type="number" min="10" max="18" required/></label>
          <label class="field">标题字号（pt）<input v-model.number="plan.format.heading_size_pt" class="input" type="number" min="12" max="28" required/></label>
          <label class="field">行距倍数<input v-model.number="plan.format.line_spacing" class="input" type="number" min="1" max="2.5" step="0.1" required/></label>
          <label v-for="side in [{k:'top',n:'上'},{k:'bottom',n:'下'},{k:'left',n:'左'},{k:'right',n:'右'}]" :key="side.k" class="field">{{side.n}}页边距（cm）<input v-model.number="plan.format[`margin_${side.k}_cm`]" class="input" type="number" min="1" max="4" step="0.1" required/></label>
        </div>
        <div class="toolbar"><label><input v-model="plan.format.cover" type="checkbox"/>独立封面</label><label><input v-model="plan.format.page_numbers" type="checkbox"/>页码</label><label><input v-model="plan.format.include_sources" type="checkbox"/>每章来源列表</label></div>
        <article v-for="(section,i) in plan.sections" :key="i" class="section-editor">
          <label class="field">章节 {{i+1}}<input v-model="section.title" class="input" required maxlength="100"/></label>
          <label class="field">本章写作要求<textarea v-model="section.instructions" class="textarea" maxlength="2000"/></label>
          <label class="field">目标字数<input v-model.number="section.target_words" class="input" type="number" min="50" max="3000" required/></label>
        </article>
        <section class="paper-preview" :style="{fontFamily:plan.format.font,lineHeight:plan.format.line_spacing}"><h3>{{plan.name}}</h3><p>{{plan.format.paper}} · {{plan.format.orientation==='portrait'?'竖版':'横版'}} · {{plan.format.font}} {{plan.format.font_size_pt}} pt</p><h4 v-for="(section,i) in plan.sections" :key="i">{{i+1}}. {{section.title}}</h4></section>
        <p class="help">Word 导出实际应用以上纸张、页边距、字体、行距、封面与页码；Markdown 保留章节与内容结构。</p>
        <button class="primary-button" :disabled="saving">{{saving?'保存中…':'保存为报告模板'}}</button>
      </form>
    </div>
  </section></div>
</template>
<style scoped>
.designer{width:min(980px,96vw);max-height:93vh;overflow:auto}.designer .modal-header{position:sticky;top:0;background:white;z-index:1}.design-input,.blueprint-form{display:grid;gap:16px}.format-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.help{font-size:12px;color:#617789;line-height:1.8}.job-status{padding:14px;background:#e9f2f8;font-size:13px;border-radius:9px}.section-editor{padding:17px;background:#f4f7f9;border-radius:10px;display:grid;gap:12px}.paper-preview{padding:30px;border:1px solid #d2dce4;box-shadow:0 6px 20px #173f5f12}.paper-preview h3{text-align:center}.paper-preview p{text-align:center;font-size:12px;color:#617789}@media(max-width:700px){.format-grid{grid-template-columns:1fr 1fr}}
</style>

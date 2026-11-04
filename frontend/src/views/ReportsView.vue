<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {useRoute} from 'vue-router'
import {api,download,can,sessionUser} from '../api'
import EvidenceText from '../components/EvidenceText.vue'
import TemplateDesigner from '../components/TemplateDesigner.vue'
import ReportSectionEditor from '../components/ReportSectionEditor.vue'
import DocxTemplateImporter from '../components/DocxTemplateImporter.vue'
const showImporter=ref(false),importSource=ref(null)
const route=useRoute()
const templates=ref([]),tasks=ref([]),kbs=ref([]),showTemplate=ref(false),showDesigner=ref(false),showTask=ref(false),error=ref(''),success=ref(''),submitting=ref(false),preview=ref(null)
const template=ref({name:'',description:'',sections:'背景与目标\n总体架构\n核心流程\n实施计划'})
const task=ref({title:'',template_id:'',knowledge_base_id:'',requirements:''})
const wholeActive=computed(()=>preview.value&&['queued','generating'].includes(preview.value.status))
const active=computed(()=>wholeActive.value||preview.value?.sections?.some(s=>['queued','running'].includes(s.generation_status)))
const stateNames={queued:'排队中',generating:'生成中',completed:'已完成',draft:'人工草稿',needs_review:'待评审',failed:'失败',interrupted:'中断',cancelled:'已取消',legacy:'旧版记录'}
let timer=null,loading=false
async function load(){
 if(loading)return;loading=true
 try{
  [templates.value,tasks.value,kbs.value]=await Promise.all([api('/api/templates'),api('/api/report-tasks'),api('/api/knowledge-bases')])
  if(!task.value.template_id&&templates.value[0])task.value.template_id=templates.value[0].id
  if(!task.value.knowledge_base_id&&kbs.value[0])task.value.knowledge_base_id=kbs.value[0].id
  if(preview.value?.id&&active.value)preview.value=await api(`/api/report-tasks/${preview.value.id}`)
 }catch(e){error.value=e.message}finally{loading=false}
}
onMounted(async()=>{
 await load()
 if(route.query.task)await inspect({id:Number(route.query.task)})
 if(route.query.designJob)showDesigner.value=true
 timer=setInterval(()=>{if(tasks.value.some(t=>['queued','generating'].includes(t.status))||active.value)load()},1500)
})
onUnmounted(()=>clearInterval(timer))
watch(()=>route.query.task,value=>{if(value)inspect({id:Number(value)})})
async function createTemplate(){
 error.value=''
 try{await api('/api/templates',{method:'POST',body:JSON.stringify({...template.value,sections:template.value.sections.split('\n').map(s=>s.trim()).filter(Boolean)})});showTemplate.value=false;success.value='模板创建成功';await load()}catch(e){error.value=e.message}
}
async function generatedTemplate(id){showDesigner.value=false;task.value.template_id=id;success.value='模型设计的模板及格式参数已保存，可以选择它生成报告';await load()}
async function importedTemplate(id){showImporter.value=false;task.value.template_id=id;success.value='DOCX结构与字段校正已保存为新模板版本，旧版本保持不变';await load()}
async function generate(){
 error.value='';success.value='';submitting.value=true
 try{
   const data=await api('/api/report-tasks',{method:'POST',body:JSON.stringify(task.value)})
   showTask.value=false;success.value=`报告任务 #${data.job_id} 已进入后台队列，离开页面后继续执行`
   preview.value=await api(`/api/report-tasks/${data.id}`);await load()
 }catch(e){error.value=e.message}finally{submitting.value=false}
}
async function inspect(item){error.value='';try{preview.value=await api(`/api/report-tasks/${item.id}`)}catch(e){error.value=e.message}}
async function refreshPreview(){if(preview.value)await inspect({id:preview.value.id});await load()}
async function exportReport(format){try{await download(`/api/report-tasks/${preview.value.id}/export?format=${format}`,`${preview.value.title}.${format}`)}catch(e){error.value=e.message}}
async function cancel(){try{await api(`/api/tasks/${preview.value.job_id}/cancel`,{method:'POST'});success.value='取消请求已提交';await load()}catch(e){error.value=e.message}}
async function retryJob(item){try{await api(`/api/tasks/${item.job_id}/retry`,{method:'POST'});success.value='任务已重新排队，执行时会建立新的报告记录';await load()}catch(e){error.value=e.message}}
function regenerate(item){task.value={title:item.title,template_id:item.template_id,knowledge_base_id:item.knowledge_base_id,requirements:item.requirements||''};showTask.value=true}
</script>
<template>
 <div class="page-stack">
  <div class="page-intro"><div><h2>报告中心</h2><p>DOCX结构解析 / 自然语言模板 → 按章节检索 → 独立逐句证据核验后发布正文</p></div><div class="toolbar"><button v-if="sessionUser?.system_admin" class="secondary-button" @click="importSource=null;showImporter=true">导入DOCX模板</button><button v-if="can('templates.manage')" class="secondary-button" @click="showDesigner=true">自然语言设计模板</button><button v-if="can('templates.manage')" class="secondary-button" @click="showTemplate=true">手动新建模板</button><button class="primary-button" :disabled="!kbs.length" @click="showTask=true">生成报告</button></div></div>
  <div v-if="error" class="error-banner">{{error}}</div><div v-if="success" class="success-banner">{{success}}</div>
  <div class="template-grid"><article v-for="item in templates" :key="item.id" class="card template-card"><span class="badge">{{item.version}}</span><span v-if="item.import_id" class="badge">DOCX已解析</span><h3>{{item.name||'历史未命名模板'}}</h3><p>{{item.description}}</p><p v-if="item.blueprint?.format" class="format-summary">{{item.blueprint.format.paper}} · {{item.blueprint.format.orientation==='portrait'?'竖版':'横版'}} · {{item.blueprint.format.font}} {{item.blueprint.format.font_size_pt}} pt · {{item.blueprint.format.line_spacing}} 倍行距</p><ol><li v-for="(section,i) in item.sections" :key="i">{{section}} <small>{{item.blueprint?.sections[i]?.level||1}}级</small></li></ol><div class="toolbar"><button class="secondary-button" @click="task.template_id=item.id;showTask=true">使用此模板</button><button v-if="item.import_id&&sessionUser?.system_admin" class="secondary-button" @click="importSource=item;showImporter=true">结构与字段校正</button></div></article></div>
  <section v-if="preview" class="card report-preview">
   <div class="page-intro"><div><h3>{{preview.title}}</h3><p>{{stateNames[preview.status]||preview.status}} · {{preview.progress}}% · 后台任务 #{{preview.job_id||'—'}}</p><p v-if="preview.blueprint?.format">{{preview.blueprint.format.paper}} · {{preview.blueprint.format.font}} {{preview.blueprint.format.font_size_pt}} pt · 引用编号按每章分别编号</p></div><div class="toolbar"><button v-if="wholeActive&&preview.job_id" class="danger-button" @click="cancel">取消后台任务</button><template v-if="['completed','needs_review','draft'].includes(preview.status)"><button class="secondary-button" @click="exportReport('docx')">下载 Word</button><button class="secondary-button" @click="exportReport('md')">下载 Markdown</button></template></div></div>
   <div v-if="preview.error" class="error-banner">{{preview.error}}</div>
   <div v-if="preview.status==='queued'" class="empty">等待队列中的空闲执行槽位，任务已持久保存。</div>
   <article v-for="(section,i) in preview.sections" :key="section.id"><h3>{{i+1}}. {{section.title}}</h3><ReportSectionEditor :report-id="preview.id" :section="section" :whole-busy="!!wholeActive" @changed="refreshPreview"/></article>
  </section>
  <section class="card"><div class="card-header"><div><h3>本人生成任务</h3><p>排队、执行、待评审、失败和取消状态均可查看</p></div><button class="secondary-button" @click="load">刷新</button></div><div class="card-body table-wrap"><table><thead><tr><th>报告名称</th><th>模板 / 知识库</th><th>进度</th><th>状态 / 错误</th><th>操作</th></tr></thead><tbody><tr v-for="item in tasks" :key="item.id"><td>{{item.title}}</td><td>{{item.template_name}}<br/><small>{{item.knowledge_base_name}}</small></td><td>{{item.progress}}%</td><td><span class="badge">{{stateNames[item.status]||item.status}}</span><p v-if="item.error" class="task-error">{{item.error}}</p></td><td><div class="toolbar"><button class="secondary-button" @click="inspect(item)">查看正文</button><button v-if="item.job_id&&['failed','interrupted','cancelled'].includes(item.status)" class="secondary-button" @click="retryJob(item)">重试任务</button><button class="secondary-button" @click="regenerate(item)">整份重新生成（新任务）</button></div></td></tr></tbody></table><div v-if="!tasks.length" class="empty">暂无报告任务</div></div></section>
  <TemplateDesigner v-if="showDesigner" :initial-job-id="Number(route.query.designJob)||null" @close="showDesigner=false" @saved="generatedTemplate"/>
  <DocxTemplateImporter v-if="showImporter" :source-template="importSource" :kbs="kbs" @close="showImporter=false" @saved="importedTemplate"/>
  <div v-if="showTemplate" class="modal-backdrop"><form class="modal" @submit.prevent="createTemplate"><div class="modal-header"><h3>手动新建模板</h3><button type="button" class="icon-button" @click="showTemplate=false">×</button></div><div class="modal-body"><label class="field">模板名称<input v-model="template.name" class="input" required/></label><label class="field">描述<input v-model="template.description" class="input"/></label><label class="field">章节（每行一个）<textarea v-model="template.sections" class="textarea" required/></label><p class="help">手动模板采用 A4、宋体 12pt、1.5 倍行距。需要自定义格式时使用自然语言设计入口。</p></div><div class="modal-footer"><button class="primary-button">创建模板</button></div></form></div>
  <div v-if="showTask" class="modal-backdrop"><form class="modal" @submit.prevent="generate"><div class="modal-header"><h3>生成报告</h3><button type="button" class="icon-button" @click="showTask=false">×</button></div><div class="modal-body"><label class="field">报告名称<input v-model="task.title" class="input" required maxlength="100"/></label><label class="field">模板<select v-model="task.template_id" class="select" required><option v-for="item in templates" :key="item.id" :value="item.id">{{item.name}}</option></select></label><label class="field">知识库<select v-model="task.knowledge_base_id" class="select" required><option v-for="item in kbs" :key="item.id" :value="item.id">{{item.name}}</option></select></label><label class="field">本次报告的自然语言要求<textarea v-model="task.requirements" class="textarea" maxlength="5000" placeholder="例如：重点说明技术路线，使用面向指导老师的表达。没有依据的进展须明确待补充。"/></label><p class="help">任务保存提交时的模板和配置快照。会向配置的模型服务发送召回证据；离开页面后继续执行。</p></div><div class="modal-footer"><button class="primary-button" :disabled="submitting">{{submitting?'提交中…':'提交后台生成任务'}}</button></div></form></div>
 </div>
</template>
<style scoped>
.template-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.template-card{padding:20px}.template-card p,.template-card ol{color:#657a88;font-size:13px;line-height:1.8}.format-summary{background:#eef4f8;padding:8px;border-radius:7px}.report-preview{padding:24px}.report-preview>article{margin-top:24px;padding-top:16px;border-top:1px solid #dfe7ed}.report-preview h3{margin-top:0}.task-error{font-size:11px;max-width:220px;color:#b62831}.help{font-size:12px;line-height:1.7;color:#657a88}@media(max-width:1000px){.template-grid{grid-template-columns:1fr 1fr}}@media(max-width:650px){.template-grid{grid-template-columns:1fr}}
</style>

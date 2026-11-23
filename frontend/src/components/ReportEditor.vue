<script setup>
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {Drawer,Dropdown,Menu,MenuItem} from 'ant-design-vue'
import {api,download} from '../api'
import {confirmAction,requestText} from '../ui'
import {MoreHorizontal,ArrowRight,Info} from '../icons'
import ReportSectionAuthor from './ReportSectionAuthor.vue'
import ReportEvidenceReview from './ReportEvidenceReview.vue'
import DocumentNodes from './DocumentNodes.vue'
const props=defineProps({reportId:Number}),emit=defineEmits(['dirty']),route=useRoute(),router=useRouter()
const report=ref(null),selected=ref(Number(route.query.section)||null),error=ref(''),notice=ref(''),busy=ref(false),dirty=ref(false),tab=ref('edit'),preview=ref(null),fixedReviewed=ref(false),exportFormat=ref('docx'),release=ref(''),diagnostics=ref(false),showQuality=ref(false),retrying=ref(false)
let timer=null,loading=false,retryTimer=null,retryPolls=0,retryLoading=false,alive=true
const current=computed(()=>report.value?.sections.find(s=>s.id===selected.value)),disabled=computed(()=>['finalized','archived'].includes(report.value?.document_state)),wholeBusy=computed(()=>['queued','generating'].includes(report.value?.status)),active=computed(()=>wholeBusy.value||report.value?.sections.some(s=>['queued','running'].includes(s.generation_status)))
const wholeDocument=computed(()=>report.value?.generation_policy==='whole-document-v1')
const synthesis=computed(()=>report.value?.generation_mode==='synthesis')
const modeLabel=computed(()=>synthesis.value?'基于证据综合生成':'严格事实证据')
const blockingIssues=computed(()=>report.value?.quality.issues.filter(issue=>issue.kind!=='review')||[])
const reviewEntries=computed(()=>{
  if(!report.value)return []
  const {quality,sections}=report.value
  const entries=new Map((quality.warnings||[]).map(warning=>[warning.section_id,warning]))
  for(const issue of quality.issues){
    if(issue.kind!=='review'||entries.has(issue.section_id))continue
    const section=sections.find(item=>item.id===issue.section_id)
    if(section)entries.set(section.id,{section_id:section.id,title:section.title,human_accepted:false})
  }
  return [...entries.values()]
})
const docNames={draft:'草稿',in_review:'待审阅',finalized:'已定稿',archived:'已归档'},qualityNames={verified:'依据通过',partial:'部分候选被过滤',insufficient:'证据不足',unverified_manual:'待审阅',legacy:'历史未核验',synthesized:'综合生成，待人工审阅',structure:'固定结构'}
function chapterLabel(section,index){const title=(section.title||'').trim();return /^\d+(?:[.．、]|\s)/.test(title)?title:`${index+1}. ${title}`}
function sectionQuality(section){return ['fixed','container'].includes(report.value?.blueprint.sections[section.ordinal]?.kind)?'固定结构':qualityNames[section.verification.state]||'待核验'}
function changedDirty(value){dirty.value=value;emit('dirty',value)}
async function load(){if(loading)return;loading=true;try{report.value=await api('/api/reports/'+props.reportId);if(!selected.value||!report.value.sections.some(s=>s.id===selected.value))selected.value=report.value.sections[0]?.id;if(!release.value&&report.value.releases[0])release.value=report.value.releases[0].id}catch(e){error.value=e.message}finally{loading=false}}
onMounted(async()=>{await load();timer=setInterval(()=>{if(active.value)load()},1800)})
onUnmounted(()=>{alive=false;clearInterval(timer);clearInterval(retryTimer);emit('dirty',false)})
async function select(id){if(dirty.value&&!await confirmAction('本节尚有未保存内容，切换后这些修改会丢失。',{title:'切换章节？',okText:'切换章节'}))return false;changedDirty(false);selected.value=Number(id);router.replace({path:route.path,query:{section:id}});return true}
async function chooseIssue(id){if(id&&await select(id)){showQuality.value=false;tab.value='edit'}}
async function action(path,body){busy.value=true;error.value='';try{await api(path,{method:'POST',body:JSON.stringify(body||{})});await load()}catch(e){error.value=e.message}finally{busy.value=false}}
async function finalize(){await action('/api/reports/'+props.reportId+'/finalize',{expected_revision:report.value.revision,fixed_text_reviewed:fixedReviewed.value});if(!error.value){notice.value='';tab.value='export'}}
async function showPreview(){if(dirty.value){error.value='请等待自动保存完成，再查看预览。';return}try{preview.value=await api('/api/reports/'+props.reportId+'/preview');tab.value='preview'}catch(e){error.value=e.message}}
async function exportFile(mode){error.value='';try{const suffix=mode==='final'?'-定稿':'-草稿';await download('/api/reports/'+props.reportId+'/export?format='+exportFormat.value+'&mode='+mode+(mode==='final'&&release.value?'&release_id='+release.value:'')+(diagnostics.value?'&diagnostics=true':''),report.value.title+suffix+'.'+exportFormat.value)}catch(e){error.value=e.message}}
async function rename(){const title=await requestText('报告名称',report.value.title);if(!title||title===report.value.title)return;try{await api('/api/reports/'+props.reportId,{method:'PATCH',body:JSON.stringify({expected_revision:report.value.revision,title})});await load()}catch(e){error.value=e.message}}
async function submitReview(){try{await api('/api/reports/'+props.reportId,{method:'PATCH',body:JSON.stringify({expected_revision:report.value.revision,document_state:'in_review'})});await load();showQuality.value=false}catch(e){error.value=e.message}}
async function retryReport(){if(retrying.value)return;retrying.value=true;error.value='';try{await api('/api/tasks/'+report.value.job_id+'/retry',{method:'POST'});notice.value='已提交重试，正在打开新报告；原失败记录会保留。';retryPolls=0;retryTimer=setInterval(async()=>{if(retryLoading)return;retryLoading=true;try{const job=await api('/api/tasks/'+report.value.job_id);if(!alive)return;if(job.result?.report_id&&Number(job.result.report_id)!==props.reportId){clearInterval(retryTimer);router.push('/reports/'+job.result.report_id);return}if(['failed','cancelled','interrupted'].includes(job.status))throw new Error(job.error||'重试未完成');if(++retryPolls>=40){clearInterval(retryTimer);retrying.value=false;notice.value='重试已排队，可在报告中心或任务与运行中查看，不必重复提交。'}}catch(e){clearInterval(retryTimer);retrying.value=false;error.value=e.message}finally{retryLoading=false}},1500)}catch(e){retrying.value=false;error.value=e.message}}
</script>
<template><section class="author-page report-editor-page">
 <div v-if="error" class="error-banner" role="alert">{{error}}</div>
 <p v-if="!report" class="empty">正在读取报告…</p>
 <template v-else>
  <div class="author-header"><div><div class="toolbar"><h2>{{report.title}}</h2><span class="badge" :class="report.document_state==='finalized'?'green':'blue'">{{docNames[report.document_state]}}</span></div><p>{{report.template_snapshot.name||'历史模板'}} {{report.template_snapshot.version||''}} · 修订{{report.revision}}<template v-if="report.period_start||report.period_end"> · {{report.period_start||'不限'}} 至 {{report.period_end||'不限'}}</template></p></div><div class="sticky-top-actions"><router-link to="/reports" class="text-button">返回列表</router-link><Dropdown :trigger="['click']"><button class="icon-button" aria-label="更多报告操作"><MoreHorizontal :size="20"/></button><template #overlay><Menu><MenuItem key="refresh" :disabled="dirty" @click="load">刷新报告</MenuItem><MenuItem v-if="!disabled" key="rename" :disabled="dirty||active" @click="rename">重命名报告</MenuItem></Menu></template></Dropdown></div></div>
  <div v-if="notice" class="success-banner" role="status">{{notice}}</div>
  <div v-if="wholeBusy||(active&&tab!=='edit')" class="report-info">
   <Info :size="17"/>
   <div role="status"><strong>{{wholeBusy?(wholeDocument?`正在生成并核验整篇正文 · ${report.progress}%`:`正在生成报告 · ${report.progress}%`):'正文任务正在运行'}}</strong><p>{{wholeDocument?'系统按模板主题检索证据，只调用一次 Kimi，并将返回结果直接保存为整篇正文，不再自动拆分章节。':'离开页面后，任务会继续执行。'}}</p></div>
   <button v-if="wholeBusy" class="text-button danger" @click="action('/api/tasks/'+report.job_id+'/cancel')">取消生成</button>
  </div>
  <div v-if="report.error" class="error-banner">{{report.error}}<button v-if="['failed','interrupted','cancelled'].includes(report.status)&&report.job_id" class="text-button" :disabled="retrying||dirty" @click="retryReport">{{retrying?'正在重试…':'重试并打开新报告'}}</button></div>
   <div class="editor-commandbar">
    <nav class="author-tabs"><button :class="{active:tab==='edit'}" @click="tab='edit'">编写与证据</button><button :class="{active:tab==='preview'}" @click="showPreview">结构预览</button><button :class="{active:tab==='export'}" @click="tab='export'">定稿与导出</button></nav>
    <div class="report-command-actions">
     <button class="mode-chip" aria-haspopup="dialog" :title="'查看生成模式：'+modeLabel" @click="showQuality=true">{{synthesis?'综合生成':'严格核验'}}</button>
     <button class="secondary-button" aria-haspopup="dialog" @click="showQuality=true">审阅进度 {{report.quality.reviewed_sections}}/{{report.quality.total_sections}}<span v-if="report.quality.warnings?.length" class="evidence-count">证据提示 {{report.quality.warnings.length}}</span></button>
    </div>
   </div>
  <div v-if="tab==='edit'" class="report-workbench" :class="{'whole-document-workbench':wholeDocument}">
   <template v-if="!wholeDocument"><aside class="card chapter-nav"><strong>章节目录</strong><button v-for="(s,i) in report.sections" :key="s.id" :class="{active:s.id===selected}" @click="select(s.id)"><span>{{chapterLabel(s,i)}}</span><small>{{sectionQuality(s)}} · {{s.reviewed_revision===s.revision?'已审阅':'未审阅'}}</small></button><p v-if="!report.sections.length" class="muted">报告生成后将在这里显示章节。</p></aside>
   <div class="chapter-mobile"><label class="field">当前章节<select class="select" :value="selected" aria-label="切换报告章节" @change="select($event.target.value)"><option v-for="(s,i) in report.sections" :key="s.id" :value="s.id">{{chapterLabel(s,i)}} · {{s.reviewed_revision===s.revision?'已审阅':'未审阅'}}</option></select></label></div></template>
   <ReportSectionAuthor v-if="current" :key="current.id" :report-id="reportId" :section="current" :whole-document="wholeDocument" :kind="wholeDocument?'fact':report.blueprint.sections[current.ordinal]?.kind||'fact'" :disabled="disabled||wholeBusy" :candidates="report.candidates.filter(c=>c.section_id===current.id)" @changed="load" @dirty="changedDirty"/>
   <div v-else class="card empty">等待整篇正文生成。后台任务会继续执行。</div>
  </div>
   <section v-if="tab==='preview'" class="card author-form"><div v-if="preview" class="paper-nodes" :style="{fontFamily:preview.format.font,fontSize:preview.format.font_size_pt+'pt',lineHeight:preview.format.line_spacing}"><DocumentNodes :nodes="preview.nodes"/></div></section>
  <section v-if="tab==='export'" class="card author-form">
   <div class="export-heading"><span class="step-number">1</span><div><h3>确认并定稿</h3><p v-if="!disabled" class="muted">完成全文审阅后，核对报告信息并确认定稿。</p></div></div>
   <template v-if="!disabled">
    <ul v-if="blockingIssues.length" class="blocking-issues"><li v-for="(issue,i) in blockingIssues" :key="i">{{issue.message}}</li></ul>
    <ReportEvidenceReview v-if="reviewEntries.length" :entries="reviewEntries" @select-section="chooseIssue"/>
    <label class="check-line final-ack"><input v-model="fixedReviewed" type="checkbox"/>我已核对固定文字、业务字段和日期，确认定稿当前已审阅的版本。</label>
    <div class="author-actions"><button class="primary-button" :disabled="busy||dirty||!fixedReviewed||!report.quality.ready" @click="finalize">冻结为正式版本</button></div>
   </template>
    <div v-else class="revision-card">
     <div><strong>{{docNames[report.document_state]}} · 只读</strong><p class="muted">创建修订后可继续编辑，历史定稿仍可下载。</p></div>
     <button class="secondary-button" :disabled="busy" @click="action('/api/reports/'+reportId+'/reopen',{expected_revision:report.revision})">创建可编辑修订</button>
    </div>
   <hr/><div class="export-heading"><span class="step-number">2</span><div><h3>下载文件</h3><p class="muted">草稿带标识；正式版本使用冻结快照，后续编辑不改变历史定稿。</p></div></div>
   <div class="form-grid"><label class="field">文件格式<select v-model="exportFormat" class="select"><option value="docx">Word DOCX</option><option value="pdf">PDF</option><option value="md">Markdown</option></select></label><label v-if="report.releases.length" class="field">定稿版本<select v-model="release" class="select"><option v-for="r in report.releases" :key="r.id" :value="r.id">v{{r.version}} · {{new Date(r.created_at).toLocaleString()}}</option></select></label></div><details class="export-options"><summary>导出选项与说明</summary><label class="check-line"><input v-model="diagnostics" type="checkbox"/>草稿附加质量核验说明</label><p class="muted">基础引用列表始终保留。定稿不代表所有内容均通过模型核验。PDF中文字体和分页可能与Word不同。</p></details><div class="author-actions"><button class="secondary-button" :disabled="dirty||active" @click="exportFile('draft')">下载草稿</button><button class="primary-button" :disabled="!report.releases.length||dirty" @click="exportFile('final')">下载正式版本</button></div>
  </section>
   <Drawer :open="showQuality" title="审阅与证据状态" width="min(420px,100vw)" @close="showQuality=false">
    <div class="quality-panel"><div class="review-count"><strong>{{report.quality.reviewed_sections}}</strong><span>/ {{report.quality.total_sections}} {{wholeDocument?'份正文':'节'}}已审阅</span></div>
     <div class="generation-info"><strong>{{modeLabel}}</strong><p class="muted">{{synthesis?'Kimi根据检索资料一次撰写整篇正文。请结合引用审阅关键事实。':'系统对整篇生成正文逐句核验证据，请审阅核验结果。'}}全文改写沿用当前模式。</p></div>
     <h4 v-if="blockingIssues.length">定稿前需处理</h4><button v-for="(issue,i) in blockingIssues" :key="i" class="issue blocking" @click="chooseIssue(issue.section_id)">{{issue.message}}</button>
     <ReportEvidenceReview v-if="reviewEntries.length" :entries="reviewEntries" @select-section="chooseIssue"/>
     <p v-else-if="!report.quality.issues.length" class="muted">{{disabled?'可下载历史定稿。':wholeDocument?'全文已审阅，可以定稿。':'章节已审阅，可以定稿。'}}</p>
     <button v-if="!disabled" class="secondary-button" :disabled="dirty||active" @click="submitReview">标记报告为待审阅</button><button class="primary-button" :disabled="dirty" @click="tab='export';showQuality=false">前往定稿与导出<ArrowRight :size="15"/></button>
    </div>
   </Drawer>
 </template>
</section></template>
<style scoped>
.report-command-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px}
.mode-chip{border:1px solid #d9e3ed;border-radius:999px;background:#f4f7fb;color:#344d64;padding:4px 10px;font-size:12px;cursor:pointer}
.mode-chip:hover{background:#e8eff7}
.evidence-count{padding:2px 6px;border-radius:5px;background:#f0f3f8;color:#475b72;font-size:11px;white-space:nowrap}
.report-info{display:flex;align-items:center;flex-wrap:wrap;gap:10px;padding:10px 13px;background:#f8faff;border:1px solid var(--line);border-radius:8px}
.report-info .ui-icon{color:#205cb0}
.report-info strong{font-size:13px;font-weight:600}
.report-info p{margin:2px 0 0;color:var(--muted);font-size:12px}
.report-info button{margin-inline-start:auto}
.generation-info,.revision-card{background:#f6f8fb;border:1px solid #e0e7ef;border-radius:10px;padding:12px 14px}
.generation-info p,.revision-card p{margin:3px 0 0}
.revision-card{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.whole-document-workbench{display:block;max-width:1100px;margin:0 auto}
</style>

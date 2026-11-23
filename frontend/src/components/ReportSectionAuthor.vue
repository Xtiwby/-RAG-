<script setup>
import {computed,onUnmounted,ref,watch,nextTick} from 'vue'
import {api} from '../api'
import {Drawer,Dropdown,Menu,MenuItem} from 'ant-design-vue'
import {ChevronDown,MoreHorizontal} from '../icons'
import {confirmAction} from '../ui'
import EvidenceText from './EvidenceText.vue'
import CoveragePanel from './CoveragePanel.vue'
import ChatCitationPanel from './ChatCitationPanel.vue'
const props=defineProps({reportId:Number,section:Object,kind:{type:String,default:'fact'},disabled:Boolean,wholeDocument:Boolean,candidates:{type:Array,default:()=>[]}}),emit=defineEmits(['changed','dirty'])
const content=ref(props.section.content),revision=ref(props.section.revision),editing=ref(false),dirty=ref(false),saving=ref(false),busy=ref(false),error=ref(''),status=ref('已保存'),tool=ref(''),query=ref(''),evidence=ref([]),instructions=ref(''),history=ref([]),compare=ref(null)
let timer=null,savePromise=null
const running=computed(()=>['queued','running'].includes(props.section.generation_status)),locked=computed(()=>props.disabled||running.value)
const stale=computed(()=>props.section.revision>revision.value&&dirty.value)
const v=computed(()=>props.section.verification||{}),qualityLabel=computed(()=>['fixed','container'].includes(props.kind)?'固定结构':({verified:'证据已核验',partial:'部分证据通过',insufficient:'证据不足',unverified_manual:'待核验',synthesized:'待核验',legacy:'待核验'}[v.value.state]||'待核验'))
const reviewed=computed(()=>props.section.reviewed_revision===props.section.revision)
const evidenceRisk=computed(()=>!['fixed','container'].includes(props.kind)&&v.value.state!=='verified'&&!(v.value.state==='partial'&&v.value.publishable))
async function review(){if(evidenceRisk.value&&!await confirmAction('本节缺少证据或尚未通过核验。确认后可按人工审阅放行，保留现有内容或待补充标记并用于正式版本；模型核验结果不变。',{title:'审阅并接受证据风险？',okText:'确认审阅放行'}))return;await action(`/api/reports/${props.reportId}/sections/${props.section.id}/review`)}
const authorRoot=ref(null),highlight=ref('')
async function locate(text){editing.value=false;highlight.value=text;await nextTick();authorRoot.value?.querySelector('.highlight-line')?.scrollIntoView({block:'center',behavior:'smooth'})}
watch(()=>props.section.revision,value=>{if(!dirty.value&&!saving.value){content.value=props.section.content;revision.value=value}})
watch(dirty,value=>emit('dirty',value))
function changed(){dirty.value=content.value!==props.section.content;status.value=dirty.value?'等待自动保存…':'已保存';clearTimeout(timer);if(dirty.value)timer=setTimeout(()=>save(),900)}
async function save(){clearTimeout(timer);if(savePromise){await savePromise;if(dirty.value)return save();return true}if(!dirty.value)return true;if(locked.value||stale.value){error.value=stale.value?'服务器已有新修订，本地文字仍保留。请复制本地内容后刷新，再合并修改。':'本节已锁定或有任务执行，尚未保存。';return false}
 const sent=content.value;saving.value=true;error.value='';status.value='保存中…'
 savePromise=(async()=>{try{const result=await api(`/api/report-tasks/${props.reportId}/sections/${props.section.id}`,{method:'PATCH',body:JSON.stringify({expected_revision:revision.value,content:sent})});revision.value=result.revision;dirty.value=content.value!==sent;status.value=dirty.value?'有新修改待保存':'已自动保存';emit('changed');return true}catch(e){error.value=e.message;status.value='保存失败，本地内容已保留';return false}finally{saving.value=false;savePromise=null}})();const ok=await savePromise;if(ok&&dirty.value)timer=setTimeout(()=>save(),500);return ok
}
async function action(path,body={}){if(!await save())return;busy.value=true;error.value='';try{await api(path,{method:'POST',body:JSON.stringify({expected_revision:revision.value,...body})});tool.value='';emit('changed')}catch(e){error.value=e.message}finally{busy.value=false}}
const citationNumber=ref(null)
const citationSources=computed(()=>{const used=new Set([...content.value.matchAll(/\[(\d+)\]/g)].map(match=>Number(match[1])));return (props.section.citations||[]).map((item,index)=>({...item,number:index+1})).filter(item=>used.has(item.number))})
function showCitation(number){citationNumber.value=number??citationSources.value[0]?.number}
async function search(){busy.value=true;error.value='';try{evidence.value=(await api(`/api/reports/${props.reportId}/evidence-search`,{method:'POST',body:JSON.stringify({query:query.value})})).items}catch(e){error.value=e.message}finally{busy.value=false}}
async function insert(e){if(!await save())return;busy.value=true;try{const added=await api(`/api/reports/${props.reportId}/sections/${props.section.id}/citations`,{method:'POST',body:JSON.stringify({expected_revision:revision.value,chunk_id:e.chunk_id})});revision.value=added.revision;content.value+=` [${added.reference}]`;editing.value=true;dirty.value=true;emit('changed');timer=setTimeout(()=>save(),500)}catch(e){error.value=e.message}finally{busy.value=false}}
async function versions(){tool.value=tool.value==='history'?'':'history';if(tool.value)try{history.value=await api(`/api/report-tasks/${props.reportId}/sections/${props.section.id}/history`)}catch(e){error.value=e.message}}
function lines(value){return value.split('\n')}
const ours=computed(()=>new Set(lines(content.value))),theirs=computed(()=>new Set(lines(compare.value?.content||'')))
async function accept(candidate,actionName){await action(`/api/reports/${props.reportId}/candidates/${candidate.id}`,{action:actionName});compare.value=null}
onUnmounted(()=>{clearTimeout(timer);emit('dirty',false)})
</script>
<template><div class="report-section-pane"><CoveragePanel :assessment="v.coverage||{}"/><article ref="authorRoot" class="card section-author">
 <header class="section-heading">
  <div class="toolbar">
   <h3>{{wholeDocument?'全文正文':section.title}}</h3>
   <span v-if="!evidenceRisk" class="badge" :class="v.state==='verified'?'green':''">{{qualityLabel}}</span>
   <span v-if="reviewed" class="report-review-status">已人工审阅</span>
  </div>
  <details v-if="evidenceRisk" class="section-risk">
   <summary>{{qualityLabel}}<ChevronDown :size="12"/></summary>
   <p>{{reviewed?'当前修订已审阅，可保留正文并定稿。核验状态会随版本保存。':'可补充证据，或审阅现有正文后决定是否保留。'}}</p>
  </details>
 </header>
 <div v-if="error" class="error-banner" role="alert">{{error}}</div>
 <div v-if="section.generation_error" class="error-banner">本次任务未完成：{{section.generation_error}}。<button v-if="section.generation_status==='failed'" class="text-button" @click="action(`/api/tasks/${section.job_id}/retry`)">重试任务</button></div>
 <div v-if="running" class="section-notice"><p role="status">正在后台生成或核验。</p><button class="text-button" @click="action(`/api/tasks/${section.job_id}/cancel`)">取消任务</button></div>
 <div class="section-toolbar"><div class="toolbar"><button class="secondary-button" :disabled="locked||kind==='container'" @click="editing=!editing">{{editing?'切换阅读':'编辑正文'}}</button><button v-if="!['fixed','container'].includes(kind)" class="text-button" :disabled="locked||busy" @click="tool=tool==='sources'?'':'sources'">引用与证据</button><Dropdown :trigger="['click']"><button class="icon-button" :aria-label="wholeDocument?'更多正文操作':'更多章节操作'"><MoreHorizontal :size="20"/></button><template #overlay><Menu><MenuItem v-if="!['fixed','container'].includes(kind)" key="regenerate" :disabled="locked||busy" @click="tool=tool==='regenerate'?'':'regenerate'">{{wholeDocument?'生成全文改写候选':'生成改写候选'}}</MenuItem><MenuItem key="history" @click="versions">修订历史</MenuItem></Menu></template></Dropdown></div><span class="save-status" role="status">{{disabled?'只读版本':status}} · 修订{{revision}}</span></div>
 <label v-if="editing" class="field">正文（停止输入后自动保存）<textarea v-model="content" class="textarea whole-document-textarea" maxlength="100000" :disabled="locked" @input="changed"/></label><EvidenceText v-else preview-citations :content="content||(wholeDocument?'报告尚无正文':'本节尚无正文')" :citations="section.citations" :highlight="highlight" @select-citation="showCitation"/>
 <div v-if="editing" class="toolbar"><span class="save-status" role="status">{{status}} · 修订{{revision}}</span><button class="secondary-button" :disabled="!dirty||saving||locked||stale" @click="save">立即保存</button></div><p v-if="stale" class="error-banner">服务器已有新修订，自动保存已停止，未覆盖较新正文。当前文本仍保留。</p>
 <section v-if="tool==='sources'" class="outline-row"><h4>从本报告授权知识库中查找证据</h4><form class="toolbar" @submit.prevent="search"><input v-model="query" class="input" required maxlength="2000" placeholder="输入要补充依据的事实或关键词" aria-label="证据检索内容"/><button class="primary-button" :disabled="busy">查找证据</button></form><div class="source-results"><article v-for="e in evidence" :key="e.chunk_id"><router-link :to="{path:`/documents/${e.document_id}`,query:{chunk:e.chunk_id,view:'original'}}" target="_blank">{{e.source}}</router-link><p>{{e.text}}</p><button class="secondary-button" :disabled="busy||locked" @click="insert(e)">添加引用编号到正文末尾</button></article><p v-if="query&&!busy&&!evidence.length" class="muted">暂无匹配证据。可调整关键词，或先到知识库补充资料。</p></div></section>
 <form v-if="tool==='regenerate'" class="outline-row" @submit.prevent="action(`/api/report-tasks/${reportId}/sections/${section.id}/regenerate`,{instructions,preview:true})"><h4>{{wholeDocument?'重新生成整篇候选，不直接替换正文':'重新生成候选，不直接替换正文'}}</h4><label class="field">希望如何修改<textarea v-model="instructions" class="textarea" maxlength="5000" placeholder="例如：聚焦技术路线、缩短说明，保留可追溯引用。"/></label><button class="primary-button" :disabled="busy||locked||dirty">生成候选供比较</button></form>
 <section v-for="candidate in candidates" :key="candidate.id" class="section-notice"><p>改写候选已生成</p><button class="text-button" @click="compare=candidate">比较新旧内容</button><button class="text-button" :disabled="busy||disabled" @click="accept(candidate,'discard')">放弃候选</button></section>
 <section v-if="compare" class="outline-row"><h4>{{compare.expected_revision!==undefined?'候选版本比较':'历史修订比较'}}</h4><div class="compare-grid"><div><strong>当前正文</strong><pre v-for="(line,i) in lines(content)" :key="i" :class="{'diff-removed':!theirs.has(line)}">{{line}}</pre></div><div><strong>{{compare.expected_revision!==undefined?'候选正文':'历史正文'}}</strong><pre v-for="(line,i) in lines(compare.content)" :key="i" :class="{'diff-added':!ours.has(line)}">{{line}}</pre></div></div><div class="toolbar"><button v-if="compare.expected_revision!==undefined" class="primary-button" :disabled="busy||locked||dirty||compare.expected_revision!==revision" @click="accept(compare,'accept')">采用此候选</button><button v-else class="primary-button" :disabled="busy||locked||dirty" @click="action(`/api/reports/${reportId}/sections/${section.id}/restore/${compare.id}`);compare=null">恢复为新修订</button><button class="secondary-button" @click="compare=null">关闭比较</button></div><p v-if="compare.expected_revision!==undefined&&compare.expected_revision!==revision" class="muted">当前修订已变化，请基于最新正文重新生成候选。</p></section>
 <section v-if="tool==='history'" class="outline-row"><h4>保留的历史修订</h4><button v-for="h in history" :key="h.id" class="secondary-button" @click="compare=h">修订{{h.revision}} · {{h.reason}} · {{new Date(h.created_at).toLocaleString()}}</button><p v-if="!history.length" class="muted">尚无旧修订。保存、核验、恢复和采用候选均会保留历史。</p></section>
 <details v-if="v.checks?.length" class="quality-details"><summary>查看本节核验依据 · 通过{{v.accepted_count}} / 拦截{{v.rejected_count}}</summary><article v-for="c in v.checks" :key="c.claim_id"><strong>句段{{c.claim_id}} · {{c.status==='supported'?'通过':'未通过'}}</strong><p v-if="c.text">核验句：{{c.text}} <button class="secondary-button" @click="locate(c.text)">在正文定位</button></p><p>{{c.reason}}</p><p v-if="c.status!=='supported'&&v.publishable">此候选已被拦截，未写入当前生成正文。</p><blockquote v-for="(q,i) in c.quotes||[]" :key="i">[{{q.reference}}] {{q.quote}}</blockquote></article><details><summary>技术追溯信息</summary><pre>{{v.checked_at}} · {{v.policy}}<br/>{{v.content_sha256}}</pre></details></details>
 <footer v-if="!disabled&&!reviewed" class="author-actions"><button class="primary-button" :disabled="running||dirty||busy" @click="review">确认审阅</button></footer>
 <Drawer :open="!!citationNumber" title="引用原文定位" width="min(760px,100vw)" @close="citationNumber=null"><ChatCitationPanel v-if="citationNumber" :sources="citationSources" :number="citationNumber" @select="citationNumber=$event" @close="citationNumber=null"/></Drawer>
</article></div></template>
<style scoped>
.section-heading{display:flex;align-items:flex-start;flex-wrap:wrap;gap:8px 12px}
.section-risk{max-width:100%;font-size:12px;color:#475b72}
.section-risk summary{display:inline-flex;align-items:center;gap:4px;list-style:none;border:1px solid var(--line);background:#f6f8fb;border-radius:999px;padding:3px 8px;cursor:pointer}
.section-risk summary::-webkit-details-marker{display:none}
.section-risk summary:hover{background:#edf3fb}
.section-risk[open] summary .ui-icon{transform:rotate(180deg)}
.section-risk p{margin:6px 0 0;padding:7px 10px;border-inline-start:2px solid #c6d7ec;background:#f8faff;line-height:1.5}
.section-notice{display:flex;align-items:center;gap:4px 12px;flex-wrap:wrap;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:#f8faff;color:#475b72;font-size:13px}
.section-notice p{margin:0 auto 0 0}
.whole-document-textarea{min-height:560px;line-height:1.8}
</style>

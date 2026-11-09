<script setup>
import { computed,nextTick,onMounted,onUnmounted,ref,watch } from 'vue'
import { api,streamApi,download } from '../api'
import { Dropdown as ADropdown, Menu as AMenu, MenuItem as AMenuItem, Popover as APopover, Select as ASelect } from 'ant-design-vue'
import { MoreHorizontal, Plus, Send, BookOpen, SlidersHorizontal, ChevronDown, Menu } from '../icons'
import { confirmAction,requestText,dateTime } from '../ui'
import EvidenceText from '../components/EvidenceText.vue'
import ChatCitationPanel from '../components/ChatCitationPanel.vue'
import {embeddingLabel,retrievalModes} from '../retrieval'
const question=ref(''),mode=ref('hybrid'),selected=ref([]),kbs=ref([]),sessions=ref([]),active=ref(null),messages=ref([])
const loading=ref(false),opening=ref(false),error=ref(''),model=ref(null),rerank=ref(true),scroll=ref(null),showSettings=ref(false)
const citationPreview=ref(null)
const exporting=ref(false)
const settingsDropdown=ref(null)
const historyOpen=ref(false)
let controller=null,citationTrigger=null
const busy=computed(()=>loading.value||opening.value)
const chatTitle=computed(()=>sessions.value.find(s=>s.id===active.value)?.title||messages.value[0]?.content.slice(0,40)||'新对话')
const modeLabel=computed(()=>retrievalModes.find(item=>item.value===mode.value)?.label)
const kbOptions=computed(()=>{
  const options=kbs.value.map(kb=>({value:kb.id,label:kb.name}))
  for(const id of selected.value){
    if(!options.some(option=>option.value===id))options.push({value:id,label:`知识库 #${id}（不可检索）`})
  }
  return options
})
const canAsk=computed(()=>selected.value.length>0&&selected.value.every(id=>kbs.value.some(kb=>kb.id===id)))
const sessionGroups=computed(()=>{
  const today=new Date()
  today.setHours(0,0,0,0)
  const week=new Date(today),month=new Date(today)
  week.setDate(today.getDate()-6)
  month.setDate(today.getDate()-29)
  const groups=['今天','7 天内','30 天内','更早'].map(label=>({label,items:[]}))
  for(const item of sessions.value){
    const updated=new Date(item.updated_at)
    groups[updated>=today?0:updated>=week?1:updated>=month?2:3].items.push(item)
  }
  return groups.filter(group=>group.items.length)
})
watch(showSettings,open=>{if(!open)settingsDropdown.value=null})
function toggleSettingsDropdown(field,open){
  if(open)settingsDropdown.value=field
  else if(settingsDropdown.value===field)settingsDropdown.value=null
}
function showSources(msg,number){
  const used=new Set([...msg.content.matchAll(/\[(\d+)\]/g)].map(match=>Number(match[1])))
  const sources=(msg.citations||[]).map((citation,index)=>({...citation,number:index+1})).filter(citation=>used.has(citation.number))
  citationTrigger=document.activeElement
  citationPreview.value={sources,number:number??sources[0]?.number}
}
function closeCitation(restoreFocus=true){
  citationPreview.value=null
  const trigger=citationTrigger
  citationTrigger=null
  if(restoreFocus)nextTick(()=>{if(trigger?.isConnected)trigger.focus({preventScroll:true})})
}
function resetRetrieval(){
  mode.value=model.value?.retrieval.mode??'hybrid'
  rerank.value=model.value?.retrieval.rerank??true
}
onMounted(async()=>{try{[kbs.value,sessions.value,model.value]=await Promise.all([api('/api/knowledge-bases'),api('/api/conversations'),api('/api/model-status')]);resetRetrieval()}catch(e){error.value=e.message}})
onUnmounted(()=>controller?.abort())
async function refreshSessions(){sessions.value=await api('/api/conversations')}
function scrollToBottom(){
  nextTick(()=>{if(scroll.value)scroll.value.scrollTop=scroll.value.scrollHeight})
}
function newChat(){
  if(busy.value)return
  closeCitation(false)
  active.value=null
  messages.value=[]
  selected.value=[]
  question.value=''
  error.value=''
  showSettings.value=false
  resetRetrieval()
}
async function open(item){
  if(busy.value||active.value===item.id)return
  opening.value=true
  try{
    const data=await api(`/api/conversations/${item.id}`)
    closeCitation(false)
    active.value=data.id
    selected.value=data.knowledge_base_ids
    messages.value=data.messages
    const lastRetrieval=data.messages.findLast(msg=>msg.role==='assistant')?.retrieval
    mode.value=data.retrieval_settings?.mode??lastRetrieval?.mode??model.value.retrieval.mode
    rerank.value=data.retrieval_settings?.rerank??lastRetrieval?.rerank_enabled??model.value.retrieval.rerank
    question.value=''
    error.value=''
    showSettings.value=false
    scrollToBottom()
  }catch(e){error.value=e.message}
  finally{opening.value=false}
}
async function renameSession(item){const title=await requestText('重命名会话',item.title);if(title===null)return;try{await api(`/api/conversations/${item.id}`,{method:'PATCH',body:JSON.stringify({title})});await refreshSessions()}catch(e){error.value=e.message}}
async function remove(item){if(busy.value||!await confirmAction(`删除会话“${item.title}”及其消息？此操作不可恢复。`,{title:'删除会话',danger:true,okText:'删除'})||busy.value)return;try{await api(`/api/conversations/${item.id}`,{method:'DELETE'});if(active.value===item.id)newChat();await refreshSessions()}catch(e){error.value=e.message}}
async function feedback(msg,value){try{await api(`/api/messages/${msg.id}/feedback`,{method:'POST',body:JSON.stringify({feedback:value})});msg.feedback=value}catch(e){error.value=e.message}}
async function exportChat(){
  if(exporting.value)return
  exporting.value=true
  try{await download(`/api/conversations/${active.value}/export`,`${chatTitle.value}.docx`)}
  catch(e){error.value=e.message}
  finally{exporting.value=false}
}
async function ask(){
  const text=question.value.trim();if(!text||busy.value)return
  if(!canAsk.value){error.value=active.value?'会话包含不可检索的知识库，请新建对话':'请选择至少一个已授权知识库';return}
  showSettings.value=false
  loading.value=true;error.value='';controller=new AbortController();let metaReceived=false,finished=false
  const userMessage={role:'user',content:text,status:'complete'},assistant={role:'assistant',content:'',citations:[],status:'streaming'}
  messages.value.push(userMessage,assistant);question.value='';scrollToBottom()
  try{
    await streamApi('/api/qa/stream',{query:text,mode:mode.value,knowledge_base_ids:selected.value,conversation_id:active.value,rerank:rerank.value},(event)=>{
      const msg=messages.value[messages.value.length-1]
      if(event.type==='meta'){
        metaReceived=true;active.value=event.conversation_id;msg.id=event.message_id;msg.citations=event.citations
        mode.value=event.pipeline?.mode??mode.value
        rerank.value=event.pipeline?.rerank_enabled??rerank.value
      }
      if(event.type==='token')msg.content+=event.content
      if(event.type==='stage')msg.stage=event.message
      if(event.type==='replace')msg.content=event.content
      if(event.type==='done'){msg.status='complete';finished=true}
      if(event.type==='error'){msg.status='error';msg.content=event.content||event.message}
      scrollToBottom()
    },controller.signal)
    if(!finished)throw new Error('连接在回答完成前中断，可继续提问')
  }catch(e){
    error.value=e.name==='AbortError'?'生成已停止，已收到内容保留为中断记录':e.message
    if(!metaReceived){messages.value.splice(-2);question.value=text}else if(messages.value.at(-1).status==='streaming')messages.value.at(-1).status='interrupted'
  }finally{controller=null;await refreshSessions().catch(()=>{});loading.value=false}
}
</script>
<template>
<div class="card chat-workspace" :class="{'with-history':historyOpen,'with-citation':citationPreview}">
<aside v-show="historyOpen" id="chat-history" class="chat-history" aria-label="历史会话">
  <button class="new-chat" :disabled="busy" @click="newChat"><Plus :size="18"/>开启新对话</button>
  <div class="session-list">
    <p v-if="!sessions.length" class="muted history-empty">新对话会自动保存在这里</p>
    <section v-for="group in sessionGroups" :key="group.label" class="session-group">
      <h3>{{group.label}}</h3>
      <article v-for="item in group.items" :key="item.id" :class="{active:active===item.id}">
        <button class="session-title" :disabled="busy" :aria-current="active===item.id?'true':undefined"
          :title="item.title" @click="open(item)">{{item.title}}</button>
        <ADropdown :trigger="['click']">
          <button class="icon-button" :disabled="busy" :aria-label="'操作会话 '+item.title"><MoreHorizontal :size="16"/></button>
          <template #overlay><AMenu><AMenuItem @click="renameSession(item)">重命名</AMenuItem><AMenuItem danger @click="remove(item)">删除</AMenuItem></AMenu></template>
        </ADropdown>
      </article>
    </section>
  </div>
</aside>
<section class="chat-main" :class="{'is-empty':!messages.length}" @keydown.esc="citationPreview&&closeCitation()">
<header class="chat-head">
  <div class="chat-heading">
    <button class="secondary-button history-toggle" aria-controls="chat-history" :aria-expanded="historyOpen"
      @click="historyOpen=!historyOpen"><Menu :size="16"/>{{historyOpen?'收起历史':'历史会话'}}</button>
    <h2 :title="chatTitle">{{chatTitle}}</h2>
  </div>
  <div class="toolbar">
    <button v-if="!historyOpen" class="secondary-button" :disabled="busy" @click="newChat"><Plus :size="16"/>开启新对话</button>
    <button v-if="active" class="secondary-button" :disabled="busy||exporting" @click="exportChat">{{exporting?'正在生成 Word…':'导出 Word'}}</button>
  </div>
</header>
<div v-if="model&&!model.configured" class="error-banner">尚未配置生成模型，请联系管理员在后端配置密钥。</div>
<div v-if="!messages.length" class="chat-empty"><div class="empty-symbol"><BookOpen :size="26"/></div><h3>从你的知识库开始提问</h3><p>选择知识库和检索方式后开始提问，回答会附上原文引用。</p></div>
<div v-else ref="scroll" class="messages" aria-live="polite" aria-relevant="additions text">
<article v-for="(msg,i) in messages" :key="msg.id||i" class="message" :class="msg.role"><header><strong>{{msg.role==='user'?'你':'知源助手'}}</strong><small>{{msg.status==='streaming'?'正在生成与核验…':msg.status==='interrupted'?'已中断':msg.status==='error'?'生成失败':dateTime(msg.created_at)}}</small></header>
<EvidenceText compact preview-citations :content="msg.content || '正在检索资料、生成候选并核验证据，请稍候…'" :citations="msg.citations||[]" @select-citation="showSources(msg,$event)"/>
<div v-if="msg.role==='assistant'" class="message-tools"><button v-if="msg.citations?.length" class="text-button" @click="showSources(msg)">查看引用来源</button><template v-if="msg.id&&msg.status==='complete'"><button :class="{chosen:msg.feedback==='helpful'}" @click="feedback(msg,msg.feedback==='helpful'?'none':'helpful')">有帮助</button><button :class="{chosen:msg.feedback==='unhelpful'}" @click="feedback(msg,msg.feedback==='unhelpful'?'none':'unhelpful')">需改进</button></template></div>
<p v-if="msg.status==='streaming'&&msg.stage" class="help" role="status">{{msg.stage}}</p>
</article></div><div v-if="error" class="error-banner" role="alert">{{error}}</div>
<footer class="composer">
  <div class="chat-settings">
    <APopover v-model:open="showSettings" trigger="click" :placement="messages.length?'top':'bottom'">
      <button class="scope-button" :aria-expanded="showSettings" :disabled="busy" @keydown.esc="showSettings=false">
        <SlidersHorizontal :size="16"/>
        <span>{{selected.length?`已选 ${selected.length} 个知识库 · ${modeLabel}`:'选择知识库与检索方式'}}</span>
        <ChevronDown :size="14"/>
      </button>
      <template #content>
        <div class="settings-panel" @keydown.esc="showSettings=false">
          <div class="field">
            <label id="chat-kb-label">知识库</label>
            <ASelect v-model:value="selected" mode="multiple" show-arrow placement="topLeft" :options="kbOptions" :max-tag-count="2"
              :disabled="busy||!!active" option-filter-prop="label" placeholder="请选择知识库"
              :open="showSettings&&settingsDropdown==='knowledge'" @dropdown-visible-change="toggleSettingsDropdown('knowledge',$event)"
              aria-labelledby="chat-kb-label" not-found-content="暂无可选知识库"/>
          </div>
          <div class="field">
            <label id="chat-mode-label">检索方式</label>
            <ASelect v-model:value="mode" :options="retrievalModes" :disabled="busy||!!active" aria-labelledby="chat-mode-label"
              :open="showSettings&&settingsDropdown==='mode'" @dropdown-visible-change="toggleSettingsDropdown('mode',$event)"/>
          </div>
          <details v-if="!active" class="advanced-settings">
            <summary>高级检索设置</summary>
            <label><input v-model="rerank" type="checkbox" :disabled="busy"/> 启用重排</label>
            <p class="muted">向量实现 · {{embeddingLabel(model?.embedding)}}</p>
          </details>
          <p v-else class="muted">追问沿用当前设置。更换设置请开启新对话。</p>
          <button class="secondary-button settings-done" @click="showSettings=false">完成</button>
        </div>
      </template>
    </APopover>
  </div>
  <div class="composer-input">
    <textarea v-model="question" aria-label="问题" rows="2" :placeholder="active?'输入追问，Ctrl + Enter 发送':'输入问题，Ctrl + Enter 发送'"
      :disabled="busy" @keydown.ctrl.enter.prevent="ask"/>
    <div class="composer-actions">
      <small>{{active&&!canAsk?'会话包含不可检索的知识库，请新建对话':'回答基于资料，重要结论请核对引用'}}</small>
      <button v-if="loading&&controller" class="danger-button" @click="controller?.abort()">停止生成</button>
      <button v-else class="primary-button" :disabled="busy||!question.trim()||!canAsk" @click="ask"><Send :size="16"/>发送</button>
    </div>
  </div>
</footer>
</section>
<ChatCitationPanel v-if="citationPreview" :sources="citationPreview.sources" :number="citationPreview.number"
  @select="citationPreview.number=$event" @close="closeCitation()"/>
</div></template>
<style scoped>
.chat-workspace {
  display:grid;
  grid-template-columns:minmax(0,1fr);
  min-height:580px;
  height:calc(100dvh - 104px);
}
.chat-workspace.with-history {grid-template-columns:clamp(190px,18vw,248px) minmax(0,1fr)}
.chat-history {
  min-height:0;
  display:flex;
  flex-direction:column;
  padding:16px 12px;
  border-right:1px solid var(--line);
  border-radius:8px 0 0 8px;
  background:#f7f9fc;
}
.new-chat {
  display:flex;
  justify-content:center;
  align-items:center;
  gap:8px;
  min-height:42px;
  flex-shrink:0;
  border:1px solid var(--line);
  border-radius:24px;
  background:white;
  color:var(--text);
  box-shadow:0 2px 4px #192c4610;
}
.new-chat:hover,.scope-button:hover {border-color:var(--primary);color:var(--primary)}
.session-list {overflow:auto;min-height:0;margin-top:18px;overscroll-behavior:contain}
.history-empty {padding:0 10px}
.session-group + .session-group {margin-top:16px}
.session-group h3 {font-weight:500;font-size:12px;color:var(--muted);margin:0;padding:6px 10px}
.session-group article {display:flex;align-items:center;border-radius:8px;padding:2px 4px 2px 0}
.session-group article:hover {background:#edf1f7}
.session-group article.active {background:var(--primary-soft)}
.session-title {
  border:0;
  background:none;
  text-align:left;
  flex:1;
  min-width:0;
  padding:9px 10px;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
  color:var(--text);
}
.session-group article.active .session-title {color:var(--primary);font-weight:600}
.chat-main {display:flex;flex-direction:column;min-width:0;min-height:0}
.chat-head {
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  padding:12px 22px;
  min-height:54px;
  border-bottom:1px solid var(--line);
}
.chat-head h2 {margin:0;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chat-heading {display:flex;align-items:center;gap:12px;min-width:0}
.history-toggle {flex-shrink:0}
.chat-head .toolbar {flex-shrink:0}
.chat-main > .error-banner {margin:8px 16px}
.chat-empty {
  margin:auto auto 0;
  padding:24px 16px 0;
  text-align:center;
  color:var(--muted);
  font-size:13px;
  line-height:1.8;
}
.chat-empty h3 {color:var(--text);font-size:clamp(19px,2vw,26px);margin:12px 0 2px}
.chat-empty p {margin:0}
.empty-symbol {
  display:grid;
  place-items:center;
  width:54px;
  height:54px;
  background:var(--primary-soft);
  color:var(--primary);
  border-radius:16px;
  margin:auto;
}
.messages {
  width:100%;
  max-width:900px;
  margin:0 auto;
  padding:24px 22px 0;
  min-height:0;
  overflow:auto;
  flex:1;
  overscroll-behavior:contain;
}
.message {padding:0 0 22px;margin-bottom:20px;border-bottom:1px solid var(--line);font-size:14px}
.message.user {background:#f5f7fa;border:0;padding:14px 18px;border-radius:12px;margin-left:12%}
.message header {display:flex;align-items:center;gap:10px;margin-bottom:10px}
.message header small {color:var(--muted);font-size:11px}
.message-tools {display:flex;gap:8px;margin-top:10px}
.message-tools button {border:0;background:none;color:var(--muted);font-size:12px;padding:4px}
.message-tools .chosen {color:var(--primary);background:var(--primary-soft);border-radius:4px}
.composer {width:100%;max-width:900px;margin:0 auto;padding:12px 22px 18px}
.is-empty .composer {max-width:720px;margin-bottom:auto;padding-top:16px}
.chat-settings {display:flex;justify-content:center;margin-bottom:10px}
.is-empty .chat-settings {margin-bottom:clamp(24px,4vh,44px)}
.scope-button {
  display:flex;
  align-items:center;
  gap:8px;
  max-width:100%;
  min-height:36px;
  border:1px solid var(--line);
  border-radius:20px;
  background:#f8faff;
  color:var(--text);
  padding:6px 12px;
  font-size:12px;
}
.scope-button svg {flex-shrink:0;color:var(--primary)}
.settings-panel {width:min(290px,calc(100vw - 60px));display:grid;gap:12px}
.settings-panel .field {position:relative}
.settings-panel p {margin:0}
.settings-done {justify-self:end}
.advanced-settings {font-size:12px;color:var(--muted)}
.advanced-settings summary {margin-bottom:6px;cursor:pointer}
.composer-input {border:1px solid #ccd4df;border-radius:16px;padding:12px;background:white}
.composer-input:focus-within {border-color:var(--primary);box-shadow:0 0 0 2px #1677ff14}
.composer textarea {width:100%;resize:vertical;border:0;outline:none;font:inherit;min-height:48px;max-height:160px;box-shadow:none}
.composer-actions {display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:6px}
.composer-actions small {font-size:11px;color:var(--muted)}
.composer-actions button {flex-shrink:0}
.with-citation > .citation-panel {
  position:fixed;
  inset:0 0 0 auto;
  width:min(640px,100vw);
  max-height:none;
  z-index:900;
  box-shadow:0 0 30px #172a4540;
}
@media(min-width:1350px) {
  .with-citation {grid-template-columns:minmax(0,1fr) minmax(340px,36%)}
  .with-history.with-citation {grid-template-columns:clamp(190px,18vw,248px) minmax(0,1fr) minmax(340px,36%)}
  .with-citation > .citation-panel {position:relative;inset:auto;width:auto;max-height:100%;box-shadow:none}
}
@media(max-width:767px) {
  .chat-workspace {height:auto;min-height:calc(100dvh - 100px)}
  .chat-workspace.with-history {grid-template-columns:minmax(0,1fr);grid-template-rows:auto 1fr}
  .chat-history {border-right:0;border-bottom:1px solid var(--line);border-radius:8px 8px 0 0;padding:10px}
  .session-list {max-height:155px;margin-top:6px}
  .chat-head {padding:10px 14px;flex-wrap:wrap}
  .chat-head .toolbar {flex-shrink:1}
  .messages {padding:16px 12px 0;max-height:55dvh}
  .composer {padding:8px 12px 12px}
  .message.user {margin-left:5%}
  .message header {flex-wrap:wrap}
}
</style>

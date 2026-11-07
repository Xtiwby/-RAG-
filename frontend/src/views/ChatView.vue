<script setup>
import { computed,nextTick,onMounted,onUnmounted,ref } from 'vue'
import { api,streamApi,download } from '../api'
import { Drawer as ADrawer, Dropdown as ADropdown, Menu as AMenu, MenuItem as AMenuItem } from 'ant-design-vue'
import { MoreHorizontal, Plus, Send, BookOpen } from '../icons'
import { confirmAction,requestText,dateTime,labelOf } from '../ui'
import EvidenceText from '../components/EvidenceText.vue'
import ChatCitationPanel from '../components/ChatCitationPanel.vue'
import GroundingPanel from '../components/GroundingPanel.vue'
import RetrievalDetails from '../components/RetrievalDetails.vue'
import {embeddingLabel,rerankLabel} from '../retrieval'
const question=ref(''),mode=ref('hybrid'),selected=ref([]),kbs=ref([]),sessions=ref([]),active=ref(null),messages=ref([]),loading=ref(false),error=ref(''),model=ref(null),rerank=ref(true),pipeline=ref(null),scroll=ref(null),showHistory=ref(false),showKnowledge=ref(false)
const citationPreview=ref(null)
let controller=null,citationTrigger=null
const chatTitle=computed(()=>sessions.value.find(s=>s.id===active.value)?.title||'新会话')
const knowledgeNames=computed(()=>kbs.value.filter(k=>selected.value.includes(k.id)).map(k=>k.name))
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
onMounted(async()=>{try{[kbs.value,sessions.value,model.value]=await Promise.all([api('/api/knowledge-bases'),api('/api/conversations'),api('/api/model-status')]);mode.value=model.value.retrieval.mode;rerank.value=model.value.retrieval.rerank}catch(e){error.value=e.message}})
onUnmounted(()=>controller?.abort())
async function refreshSessions(){sessions.value=await api('/api/conversations')}
function newChat(){if(loading.value)return;closeCitation(false);active.value=null;messages.value=[];selected.value=[];pipeline.value=null;error.value=''}
async function open(item){if(loading.value)return;try{const data=await api(`/api/conversations/${item.id}`);closeCitation(false);active.value=data.id;selected.value=data.knowledge_base_ids;messages.value=data.messages;showHistory.value=false;error.value='';await nextTick();if(scroll.value)scroll.value.scrollTop=scroll.value.scrollHeight}catch(e){error.value=e.message}}
async function renameSession(item){const title=await requestText('重命名会话',item.title);if(title===null)return;try{await api(`/api/conversations/${item.id}`,{method:'PATCH',body:JSON.stringify({title})});await refreshSessions()}catch(e){error.value=e.message}}
async function remove(item){if(loading.value||!await confirmAction(`删除会话“${item.title}”及其消息？此操作不可恢复。`,{title:'删除会话',danger:true,okText:'删除'}))return;try{await api(`/api/conversations/${item.id}`,{method:'DELETE'});if(active.value===item.id)newChat();await refreshSessions()}catch(e){error.value=e.message}}
async function feedback(msg,value){try{await api(`/api/messages/${msg.id}/feedback`,{method:'POST',body:JSON.stringify({feedback:value})});msg.feedback=value}catch(e){error.value=e.message}}
async function exportChat(format){try{await download(`/api/conversations/${active.value}/export?format=${format}`,`问答记录.${format}`)}catch(e){error.value=e.message}}
async function ask(){
  const text=question.value.trim();if(!text||loading.value)return
  if(!selected.value.length){error.value='请选择至少一个已授权知识库';return}
  loading.value=true;error.value='';controller=new AbortController();let metaReceived=false,finished=false
  const userMessage={role:'user',content:text,status:'complete'},assistant={role:'assistant',content:'',citations:[],verification:[],status:'streaming'}
  messages.value.push(userMessage,assistant);question.value=''
  try{
    await streamApi('/api/qa/stream',{query:text,mode:mode.value,knowledge_base_ids:selected.value,conversation_id:active.value,rerank:rerank.value},(event)=>{
      const msg=messages.value[messages.value.length-1]
      if(event.type==='meta'){metaReceived=true;active.value=event.conversation_id;msg.id=event.message_id;msg.citations=event.citations;msg.retrieval=event.pipeline;pipeline.value=event.pipeline}
      if(event.type==='token')msg.content+=event.content
      if(event.type==='verification')msg.verification=event.checks
      if(event.type==='coverage')msg.coverage=event.assessment
      if(event.type==='stage')msg.stage=event.message
      if(event.type==='replace')msg.content=event.content
      if(event.type==='done'){msg.status='complete';finished=true}
      if(event.type==='error'){msg.status='error';msg.content=event.content||event.message}
      nextTick(()=>{if(scroll.value)scroll.value.scrollTop=scroll.value.scrollHeight})
    },controller.signal)
    if(!finished)throw new Error('连接在回答完成前中断，可继续提问')
  }catch(e){
    error.value=e.name==='AbortError'?'生成已停止，已收到内容保留为中断记录':e.message
    if(!metaReceived){messages.value.splice(-2);question.value=text}else if(messages.value.at(-1).status==='streaming')messages.value.at(-1).status='interrupted'
  }finally{loading.value=false;controller=null;await refreshSessions().catch(()=>{})}
}
</script>
<template><div class="chat-workspace" :class="{'with-citation':citationPreview}"><section class="card chat-main" @keydown.esc="citationPreview&&closeCitation()">
<header class="chat-head"><div><h2>知识问答</h2><p>{{chatTitle}}</p></div><div class="toolbar"><button class="secondary-button" :disabled="loading" @click="showHistory=true">历史会话</button><button class="primary-button" :disabled="loading" @click="newChat"><Plus/>新会话</button><ADropdown :trigger="['click']" v-if="active"><button class="icon-button" aria-label="导出问答记录" :disabled="loading"><MoreHorizontal/></button><template #overlay><AMenu><AMenuItem @click="exportChat('md')">导出 Markdown</AMenuItem><AMenuItem @click="exportChat('docx')">导出 Word</AMenuItem></AMenu></template></ADropdown></div></header>
<div v-if="model&&!model.configured" class="error-banner">尚未配置生成模型，请联系管理员在后端配置密钥。</div>
<div class="chat-scope"><button class="scope-button" :disabled="loading" @click="showKnowledge=true"><BookOpen/><span>{{selected.length?'已选 '+selected.length+' 个知识库':'选择知识库'}}</span><span class="badge">{{labelOf(mode)}}</span></button><span class="scope-names" :title="knowledgeNames.join('、')">{{knowledgeNames.join('、')||'仅检索你有权限访问的资料'}}</span></div>
<div ref="scroll" class="messages" aria-live="polite" aria-relevant="additions text">
<div v-if="!messages.length" class="chat-empty"><div class="empty-symbol"><BookOpen/></div><h3>从你的知识库开始提问</h3><p>先选择知识库，再输入问题。回答附带原文引用，资料不足时会明确说明。</p><button class="secondary-button" @click="showKnowledge=true">选择知识范围</button></div>
<article v-for="(msg,i) in messages" :key="msg.id||i" class="message" :class="msg.role"><header><strong>{{msg.role==='user'?'你':'知源助手'}}</strong><small>{{msg.status==='streaming'?'正在生成与核验…':msg.status==='interrupted'?'已中断':msg.status==='error'?'生成失败':dateTime(msg.created_at)}}</small></header>
<EvidenceText compact preview-citations :content="msg.content || '正在检索资料、生成候选并核验证据，请稍候…'" :citations="msg.citations||[]" @select-citation="showSources(msg,$event)"/>
<div v-if="msg.role==='assistant'" class="message-tools"><button v-if="msg.citations?.length" class="text-button" @click="showSources(msg)">查看引用来源</button><template v-if="msg.id&&msg.status==='complete'"><button :class="{chosen:msg.feedback==='helpful'}" @click="feedback(msg,msg.feedback==='helpful'?'none':'helpful')">有帮助</button><button :class="{chosen:msg.feedback==='unhelpful'}" @click="feedback(msg,msg.feedback==='unhelpful'?'none':'unhelpful')">需改进</button></template></div>
<p v-if="msg.status==='streaming'&&msg.stage" class="help" role="status">{{msg.stage}}</p>
<GroundingPanel v-if="msg.role==='assistant'" :checks="msg.verification||[]" :coverage="msg.coverage||{}"/>
<RetrievalDetails v-if="msg.role==='assistant'" :pipeline="msg.retrieval"/>
</article></div><div v-if="error" class="error-banner" role="alert">{{error}}</div>
<footer class="composer"><div class="composer-input"><textarea v-model="question" aria-label="问题" rows="3" placeholder="输入问题，Ctrl + Enter 发送" :disabled="loading" @keydown.ctrl.enter.prevent="ask"/><div class="composer-actions"><small>{{loading?'只发布通过校验的内容':'回答基于资料，重要结论请核对引用'}}</small><button v-if="loading" class="danger-button" @click="controller?.abort()">停止生成</button><button v-else class="primary-button" :disabled="!question.trim()||!selected.length" @click="ask"><Send/>发送</button></div></div></footer></section>
<ADrawer v-model:open="showHistory" title="历史会话" :width="'min(400px, 100vw)'"><div class="session-list"><article v-for="item in sessions" :key="item.id" :class="{active:active===item.id}"><button class="session-title" :disabled="loading" @click="open(item)">{{item.title}}</button><ADropdown :trigger="['click']"><button class="icon-button" :disabled="loading" :aria-label="'操作会话 '+item.title"><MoreHorizontal/></button><template #overlay><AMenu><AMenuItem @click="renameSession(item)">重命名</AMenuItem><AMenuItem danger @click="remove(item)">删除</AMenuItem></AMenu></template></ADropdown></article><p v-if="!sessions.length" class="empty">你的会话将自动保存在这里</p></div></ADrawer>
<ADrawer v-model:open="showKnowledge" title="知识范围与检索设置" :width="'min(440px, 100vw)'"><p class="help">{{active?'当前会话沿用原知识范围；更换范围请新建会话。':'至少选择一个已授权知识库。'}}</p><div class="kb-options"><label v-for="kb in kbs" :key="kb.id"><input v-model="selected" type="checkbox" :value="kb.id" :disabled="!!active||loading"/><span><strong>{{kb.name}}</strong><small>{{kb.description||'暂无说明'}}</small></span></label><p v-if="!kbs.length" class="empty">暂无授权知识库，请联系管理员</p></div><details class="advanced-settings"><summary>高级检索设置</summary><label class="field">检索模式<select v-model="mode" class="select" :disabled="loading"><option value="vector">向量检索</option><option value="keyword">关键词检索（BM25）</option><option value="hybrid">混合检索（RRF）</option></select></label><label><input v-model="rerank" type="checkbox" :disabled="loading"/>启用重排（按系统配置）</label><p class="help">向量实现：{{embeddingLabel(model?.embedding)}}。{{pipeline?'本轮重排：'+rerankLabel(pipeline.rerank):''}}</p></details><template #footer><button class="primary-button" @click="showKnowledge=false">完成选择</button></template></ADrawer>
<ChatCitationPanel v-if="citationPreview" :sources="citationPreview.sources" :number="citationPreview.number"
  @select="citationPreview.number=$event" @close="closeCitation()"/>
</div></template>
<style scoped>
.chat-workspace{max-width:1100px;margin:auto}.chat-main{display:flex;flex-direction:column;min-height:calc(100vh - 108px)}.chat-head{padding:18px 22px;display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid var(--border);flex-wrap:wrap}.chat-head h2{margin:0;font-size:20px}.chat-head p{margin:5px 0 0;color:var(--muted);font-size:12px;max-width:500px;overflow-wrap:anywhere}.chat-scope{padding:10px 22px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--border);min-width:0}.scope-button{display:flex;align-items:center;gap:8px;border:0;background:transparent;color:var(--primary);padding:4px 0;white-space:nowrap}.scope-names{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--muted)}.messages{padding:24px;min-height:260px;max-height:calc(100vh - 350px);overflow:auto;flex:1;scroll-behavior:smooth}.chat-empty{max-width:430px;text-align:center;margin:45px auto;color:var(--muted);font-size:13px;line-height:1.8}.chat-empty h3{color:var(--text);font-size:18px}.empty-symbol{display:grid;place-items:center;width:48px;height:48px;background:var(--primary-soft);color:var(--primary);border-radius:12px;margin:auto}.message{padding:0 0 22px;margin-bottom:20px;border-bottom:1px solid var(--border);font-size:14px}.message.user{background:#f5f7fa;border:0;padding:14px 18px;border-radius:8px;margin-left:12%}.message header{display:flex;align-items:center;gap:10px;margin-bottom:10px}.message header small{color:var(--muted);font-size:11px}.message-tools{display:flex;gap:8px;margin-top:10px}.message-tools button{border:0;background:none;color:var(--muted);font-size:12px;padding:4px}.message-tools .chosen{color:var(--primary);background:var(--primary-soft);border-radius:4px}.composer{padding:0 22px 18px}.composer-input{border:1px solid #ccd4df;border-radius:8px;padding:12px;background:white}.composer-input:focus-within{border-color:var(--primary);box-shadow:0 0 0 2px #1677ff14}.composer textarea{width:100%;resize:vertical;border:0;outline:none;font:inherit;min-height:64px;max-height:200px;box-shadow:none}.composer-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px}.composer-actions small{font-size:11px;color:var(--muted)}.session-list article{display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--border);padding:12px 8px}.session-list article.active{background:var(--primary-soft)}.session-title{border:0;background:none;text-align:left;flex:1;overflow-wrap:anywhere;color:var(--text)}.kb-options label{display:flex;align-items:flex-start;gap:10px;padding:14px 0;border-bottom:1px solid var(--border)}.kb-options input{margin-top:5px}.kb-options small{display:block;color:var(--muted);font-size:12px;margin-top:5px}.advanced-settings{margin-top:24px}.advanced-settings summary{margin-bottom:16px;cursor:pointer}.advanced-settings .field{margin-bottom:16px}@media(max-width:767px){.chat-head,.chat-scope{padding:14px}.messages{padding:16px;max-height:55vh}.composer{padding:0 12px 12px}.chat-main{min-height:calc(100vh - 85px)}.chat-head .toolbar{gap:6px}.scope-names{display:none}.composer-actions small{max-width:65%;line-height:1.5}.message.user{margin-left:5%}.message header{flex-wrap:wrap}}
.chat-main{min-width:0}
.with-citation{display:grid;grid-template-columns:minmax(0,1fr) minmax(340px,45%);gap:16px;max-width:1600px}
.with-citation>.citation-panel{position:sticky;top:72px;align-self:start;max-height:calc(100dvh - 108px)}
@media(max-width:1149px){
  .with-citation{display:block}
  .with-citation>.citation-panel{position:fixed;inset:0 0 0 auto;width:min(640px,100vw);max-height:none;z-index:900;box-shadow:0 0 30px #172a4540}
}
</style>

<script setup>
import{computed,onMounted,onUnmounted,ref}from'vue'
import{onBeforeRouteLeave}from'vue-router'
import{confirmAction}from'../ui'
import{api,getToken}from'../api'
const config=ref(null),revision=ref(0),credentials=ref({}),error=ref(''),success=ref(''),busy=ref(false),probing=ref('')
const baseline=ref(''),dirty=computed(()=>!!config.value&&JSON.stringify(config.value)!==baseline.value)
function remember(){baseline.value=JSON.stringify(config.value)}
async function load(){if(dirty.value&&!await confirmAction('重新加载会丢弃未保存的配置。',{title:'放弃修改？'}))return;try{const data=await api('/api/settings');config.value=data.settings;revision.value=data.revision;credentials.value=data.credentials;remember()}catch(e){error.value=e.message}}
function preventClose(e){if(dirty.value){e.preventDefault();e.returnValue=''}}
function revealInvalid(e){e.target.closest('details')?.setAttribute('open','')}
function embeddingChanged(){if(config.value.embedding.provider==='local')config.value.embedding.model='BAAI/bge-m3'}
onMounted(()=>{load();window.addEventListener('beforeunload',preventClose)})
onUnmounted(()=>window.removeEventListener('beforeunload',preventClose))
onBeforeRouteLeave(async()=>!getToken()||!dirty.value||await confirmAction('配置尚未保存，离开后修改会丢失。',{title:'离开系统配置？',okText:'离开'}))
async function save(){busy.value=true;error.value='';success.value='';try{const data=await api('/api/settings',{method:'PUT',body:JSON.stringify({settings:config.value,expected_revision:revision.value})});revision.value=data.revision;config.value=data.settings;remember();success.value=`配置版本 ${data.revision} 已保存：后续请求、新任务和新上传会采用新参数。已有任务保留提交时的配置。`}catch(e){error.value=e.message}finally{busy.value=false}}
async function probe(target){probing.value=target;error.value='';success.value='';try{const data=await api('/api/operations/probe',{method:'POST',body:JSON.stringify({target})});success.value=data.message+'（使用当前已保存配置）'}catch(e){error.value=e.message}finally{probing.value=''}}
</script>
<template>
 <div class="page-stack">
  <div class="page-intro"><div><h2>系统配置</h2><p>配置版本 {{revision}} · 校验后持久保存，对后续请求生效</p></div><button class="secondary-button" :disabled="busy" @click="load">重新加载</button></div>
  <div v-if="error" class="error-banner">{{error}}</div><div v-if="success" class="success-banner">{{success}}</div>
  <form v-if="config" class="page-stack" @submit.prevent="save" @invalid.capture="revealInvalid">
   <details class="card settings-card" open>
    <summary>生成模型</summary><div class="settings-body">
     <p>密钥仅从后端 .env 加载；界面不读取或显示密钥。状态：{{credentials.chat?'已配置':'未配置'}}。</p>
     <div class="form-grid">
      <label class="field">API 服务地址<input v-model="config.chat.base_url" class="input" type="url" required/></label>
      <label class="field">模型名称<input v-model="config.chat.model" class="input" required/></label>
      <label class="field">超时秒数<input v-model.number="config.chat.timeout_seconds" class="input" type="number" min="10" max="600" required/></label>
      <label class="field">最大输出 Token<input v-model.number="config.chat.max_tokens" class="input" type="number" min="256" max="32768" required/></label>
     </div><button type="button" class="secondary-button" :disabled="!!probing" @click="probe('chat')">{{probing==='chat'?'检测中…':'检测已保存的模型服务'}}</button>
    </div>
   </details>
   <details class="card settings-card" open>
    <summary>语义 Embedding</summary><div class="settings-body"><div class="form-grid">
     <label class="field">向量后端<select v-model="config.embedding.provider" class="select" @change="embeddingChanged"><option value="local">本地预训练 BGE-M3（语义向量）</option><option value="api">语义向量 API</option><option value="tfidf">TF-IDF（仅词项基线）</option></select></label>
     <label class="field">模型名称<input v-model="config.embedding.model" class="input" :readonly="config.embedding.provider==='local'" :required="config.embedding.provider!=='tfidf'"/></label>
     <label v-if="config.embedding.provider==='api'" class="field">API 服务地址<input v-model="config.embedding.base_url" class="input" type="url" required placeholder="http://localhost:11434/v1"/></label>
     <template v-if="config.embedding.provider==='local'">
      <label class="field">推理设备<select v-model="config.embedding.device" class="select"><option value="auto">自动（优先 CUDA，否则 CPU）</option><option value="cuda">仅 CUDA</option><option value="cpu">仅 CPU</option></select></label>
      <label class="field">每批文本数<input v-model.number="config.embedding.batch_size" class="input" type="number" min="1" max="32" required/></label>
      <label class="field">最大输入 Token<input v-model.number="config.embedding.max_length" class="input" type="number" min="128" max="8192" required/></label>
     </template>
     <label class="field">推理 / API 超时秒数<input v-model.number="config.embedding.timeout_seconds" class="input" type="number" min="10" max="600" required/></label>
    </div>
    <p v-if="config.embedding.provider==='local'">本地 BGE-M3 使用预训练模型生成 1024 维语义向量，不调用 Kimi，也无需额外密钥。需先运行 backend/prepare_models.py 下载模型。新文档索引时生成向量；旧片段首次检索时补齐缓存，不改变片段或历史引用。模型版本、输入长度分别隔离缓存。模型缺失或失败会报错，不静默回退。</p>
    <p v-else>API 密钥状态：{{credentials.embedding?'已配置':'未配置（本地免密服务可留空）'}}。TF-IDF 不是预训练语义模型。切换后端不会删除旧向量。</p>
    <button type="button" class="secondary-button" :disabled="!!probing" @click="probe('embedding')">{{probing==='embedding'?'推理检测中…':'检测已保存的向量后端'}}</button></div>
   </details>
   <details class="card settings-card" open>
    <summary>候选片段重排</summary><div class="settings-body"><div class="form-grid">
     <label class="field">重排方式<select v-model="config.reranking.provider" class="select"><option value="local">本地 Cross-Encoder 模型</option><option value="lexical">轻量规则（非模型，仅基线）</option></select></label>
     <template v-if="config.reranking.provider==='local'">
      <label class="field">模型<input v-model="config.reranking.model" class="input" readonly/></label>
      <label class="field">候选池大小<input v-model.number="config.reranking.candidate_k" class="input" type="number" min="20" max="100" required/></label>
      <label class="field">每批问题—片段对数<input v-model.number="config.reranking.batch_size" class="input" type="number" min="1" max="16" required/></label>
      <label class="field">推理设备<select v-model="config.reranking.device" class="select"><option value="auto">自动（优先 CUDA，否则 CPU）</option><option value="cuda">仅 CUDA</option><option value="cpu">仅 CPU</option></select></label>
      <label class="field">最大输入 Token<input v-model.number="config.reranking.max_length" class="input" type="number" min="128" max="8192" required/></label>
      <label class="field">超时秒数<input v-model.number="config.reranking.timeout_seconds" class="input" type="number" min="10" max="600" required/></label>
     </template>
     <label><input v-model="config.retrieval.rerank" type="checkbox"/>默认启用重排</label>
    </div><p>检索先召回候选，再由 BGE-reranker-v2-m3 联合读取问题和片段并评分，最后选取 TopK。复合问题保留主题覆盖；重排得分表示相对相关性，不是事实正确率。模型故障会报错，不以词项重排冒充模型。</p>
    <button type="button" class="secondary-button" :disabled="!!probing" @click="probe('reranking')">{{probing==='reranking'?'推理检测中…':'检测已保存的重排后端'}}</button></div>
   </details>
   <details class="card settings-card"><summary>中英查询改写（可选辅助）</summary><div class="settings-body">
    <label class="check-line"><input v-model="config.retrieval.cross_language" type="checkbox"/>启用 Kimi 双语查询改写与子问题召回</label>
    <label class="field">查询改写超时（秒）<input v-model.number="config.retrieval.query_rewrite_timeout_seconds" class="input" type="number" min="2" max="60" required/></label>
    <p>BGE-M3 本身支持跨语言语义检索，改写是额外辅助手段。开启会增加 Kimi 调用、费用与延迟；失败时使用原问题并显示降级。改写不是证据。</p>
   </div></details>
   <details class="card settings-card" open><summary>回答关键点与有限补充</summary><div class="settings-body"><div class="form-grid">
    <label><input v-model="config.answer_quality.enabled" type="checkbox"/>启用回答结束后的关键点覆盖检查</label>
    <label><input v-model="config.answer_quality.repair" type="checkbox"/>允许一次证据内补充，仍须逐句核验</label>
    <label class="field">最多补充句数<input v-model.number="config.answer_quality.max_supplements" class="input" type="number" min="1" max="6" required/></label>
    <label class="field">相邻原文上限<input v-model.number="config.answer_quality.context_neighbors" class="input" type="number" min="0" max="6" required/></label>
    <label class="field">覆盖检查超时秒数<input v-model.number="config.answer_quality.timeout_seconds" class="input" type="number" min="10" max="120" required/></label>
   </div><p>主检索TopK之外可补相邻原文，总证据最多20条；相邻片段保留独立ID、版本和哈希，不伪装成主检索得分。增加一次模型覆盖评估；需要补充时再增加一次事实核验，因此有额外费用和等待。失败保留已核验答案并提示未能确认完整性。旧任务保持提交时配置。</p></div></details>
   <details class="card settings-card"><summary>文档切分</summary><div class="settings-body"><div class="form-grid">
    <label class="field">片段长度（字符）<input v-model.number="config.chunk.size" class="input" type="number" min="100" max="8000" required/></label>
    <label class="field">重叠长度（字符）<input v-model.number="config.chunk.overlap" class="input" type="number" min="0" :max="Math.min(2000,config.chunk.size-1)" required/></label>
   </div><p>对新上传文档生效。已有文档重新切分会创建新版本，旧引用保持不变。较长片段可能超出模型输入长度，请结合检索过程中的长度提示调整。</p></div></details>
   <details class="card settings-card"><summary>检索默认值</summary><div class="settings-body"><div class="form-grid">
    <label class="field">默认模式<select v-model="config.retrieval.mode" class="select"><option value="vector">向量</option><option value="keyword">BM25 关键词</option><option value="hybrid">RRF 混合</option></select></label>
    <label class="field">TopK<input v-model.number="config.retrieval.top_k" class="input" type="number" min="1" max="20" required/></label>
    <label class="field">最低语义余弦相似度<input v-model.number="config.retrieval.min_similarity" class="input" type="number" min="0" max="1" step="0.01" required/></label>
    <label class="field">词项最低覆盖率<input v-model.number="config.retrieval.min_coverage" class="input" type="number" min="0" max="1" step="0.01" required/></label>
   </div><p>问答和报告共用这些默认值。纯关键词模式不调用 Embedding；开启模型重排时仍会调用重排模型。阈值应结合独立标注集评估。</p></div></details>
   <details class="card settings-card"><summary>任务队列</summary><div class="settings-body"><div class="form-grid">
    <label class="field">并发任务数<input v-model.number="config.queue.concurrency" class="input" type="number" min="1" max="4" required/></label>
    <label class="field">单任务最大尝试次数<input v-model.number="config.queue.max_attempts" class="input" type="number" min="1" max="5" required/></label>
   </div><p>本地模型推理串行使用设备，避免多个任务同时占满显存。任务配置在提交时保存，降低并发不会强制终止执行中的任务。</p></div></details>
   <div class="toolbar settings-save"><span class="muted">{{dirty?'有未保存的修改':'所有配置已保存'}}</span><button class="primary-button" :disabled="busy||!dirty">{{busy?'保存中…':'保存系统配置'}}</button></div>
  </form>
 </div>
</template>
<style scoped>
.settings-card{padding:0;overflow:hidden}.settings-card summary{padding:20px 22px;cursor:pointer;font-size:14px}.settings-body{padding:0 22px 22px;border-top:1px solid var(--line)}.settings-card p{font-size:12px;color:var(--muted);line-height:1.8}.settings-card button{margin-top:15px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:17px;margin-top:18px}.settings-save{justify-content:flex-end;position:sticky;bottom:12px;background:white;border:1px solid var(--line);padding:12px 18px;border-radius:8px;z-index:2}.settings-save .muted{margin-right:auto}@media(max-width:700px){.form-grid{grid-template-columns:1fr}.settings-body{padding:0 16px 16px}.settings-card summary{padding:16px}}
</style>

<script setup>
import {computed,onMounted,ref,watch} from 'vue'
import {useRouter} from 'vue-router'
import {Table,message} from 'ant-design-vue'
import {api,can} from '../api'
const router=useRouter()
const kbs=ref([]),library=ref(''),sources=ref([]),loading=ref(true),busy=ref(''),error=ref('')
const kinds={web:'网页',api:'API 接口',object:'对象存储',drive:'企业网盘',database:'数据库'}
const writable=computed(()=>kbs.value.filter(k=>k.access==='write'&&can('kb.write')))
const columns=[
  {title:'数据源',key:'label',width:260},
  {title:'类型',key:'kind',width:140},
  {title:'标识',dataIndex:'id',width:200},
  {title:'操作',key:'actions',width:120},
]
async function loadLibraries(){
  loading.value=true;error.value=''
  try{
    kbs.value=await api('/api/knowledge-bases')
    if(!writable.value.some(k=>String(k.id)===String(library.value)))library.value=writable.value[0]?.id||''
    await loadSources()
  }catch(e){error.value=e.message}
  finally{loading.value=false}
}
async function loadSources(){
  sources.value=[]
  if(!library.value)return
  error.value=''
  try{sources.value=await api('/api/data-sources?knowledge_base_id='+library.value)}
  catch(e){error.value=e.message}
}
async function importSource(row){
  busy.value=row.id;error.value=''
  try{
    const doc=await api('/api/data-sources/import',{method:'POST',body:JSON.stringify({source_id:row.id,knowledge_base_id:Number(library.value)})})
    message.success(`已导入「${row.label}」，正在后台解析`)
    router.push('/documents/'+doc.id)
  }catch(e){error.value=e.message}
  finally{busy.value=''}
}
onMounted(loadLibraries)
watch(library,loadSources)
</script>
<template>
 <div class="page-stack">
  <div class="page-intro"><div><h2>数据源接入</h2><p>把部署方配置的网页、API、对象存储、企业网盘和数据库快照导入知识库</p></div><button class="secondary-button" :disabled="loading||!library" @click="loadSources">刷新</button></div>
  <div v-if="error" class="error-banner">{{error}}</div>
  <section class="card">
   <div class="filter-bar">
    <label class="field">目标知识库<select v-model="library" class="select" aria-label="目标知识库"><option v-for="k in writable" :key="k.id" :value="k.id">{{k.name}}</option></select></label>
   </div>
   <p class="hint">数据源由部署方在 <code>RAG_DATA_SOURCES_FILE</code> 指向的 JSON 文件中维护，界面不保存数据库口令或访问令牌。导入后与普通上传共用上传、解析、切分、索引四个阶段，同名文件会生成新版本。</p>
  </section>
  <section v-if="writable.length" class="card">
   <Table :columns="columns" :data-source="sources" row-key="id" :loading="loading" :scroll="{x:760}" :pagination="{pageSize:10,hideOnSinglePage:true}" size="middle">
    <template #bodyCell="{column,record}">
     <template v-if="column.key==='label'"><strong class="table-title">{{record.label}}</strong></template>
     <template v-else-if="column.key==='kind'"><span class="badge">{{kinds[record.kind]||record.kind}}</span></template>
     <template v-else-if="column.key==='actions'"><button class="text-button" :disabled="!!busy" @click="importSource(record)">{{busy===record.id?'导入中…':'导入'}}</button></template>
    </template>
    <template #emptyText><div class="empty"><h3>此知识库没有配置数据源</h3><p>请让管理员在数据源配置文件中添加 <code>knowledge_base_id</code> 等于 {{library}} 的条目。</p></div></template>
   </Table>
  </section>
  <section v-else-if="!loading" class="card empty"><h3>没有可写入的知识库</h3><p>数据源导入需要知识库维护权限，请先申请授权或创建知识库。</p></section>
 </div>
</template>
<style scoped>
.hint{margin:0;padding:0 20px 18px;font-size:12px;color:var(--muted);line-height:1.8}
.hint code,.empty code{padding:1px 5px;background:#f3f6f9;border-radius:4px;font-size:12px}
.filter-bar{padding:18px 20px}
</style>

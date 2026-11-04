<script setup>
import {computed,onMounted,ref} from 'vue'
import {api,download} from '../api'
const props=defineProps({sourceTemplate:Object,kbs:{type:Array,default:()=>[]}}),emit=defineEmits(['close','saved'])
const error=ref(''),busy=ref(false),parsed=ref(null),headings=ref([]),mappings=ref([])
const form=ref({name:'',description:'',purpose:'',version:'v1.0',knowledge_base_ids:[]})
const bodyBlocks=computed(()=>parsed.value?.structure.blocks.filter(b=>b.part==='body'&&!b.table)||[])
const headingOptions=computed(()=>headings.value.filter(h=>h.level>0).map(h=>({...h,text:bodyBlocks.value.find(b=>b.id===h.block_id)?.text})))
const blocks=computed(()=>Object.fromEntries((parsed.value?.structure.blocks||[]).map(b=>[b.id,b])))
function prepare(data,stored=null){
 parsed.value=data
 headings.value=data.structure.blocks.filter(b=>b.part==='body'&&!b.table).map(b=>({block_id:b.id,level:b.type==='heading'?b.level:0}))
 const first=headings.value.find(h=>h.level>0)?.block_id||''
 mappings.value=stored?.field_mappings||data.structure.fields.map(f=>({key:f.key,target:'section',section_block_id:first,value:''}))
}
onMounted(async()=>{if(!props.sourceTemplate)return;busy.value=true;try{
 const data=await api(`/api/templates/${props.sourceTemplate.id}/structure`)
 prepare({import_id:data.import_id,filename:'已保存模板原文件',structure:data.structure},data)
 form.value={name:data.name,description:data.description,purpose:data.blueprint.purpose,version:data.version+'-new',knowledge_base_ids:data.knowledge_base_ids}
}catch(e){error.value=e.message}finally{busy.value=false}})
async function upload(event){const file=event.target.files?.[0];if(!file)return;busy.value=true;error.value='';try{
 const data=new FormData();data.append('file',file)
 prepare(await api('/api/templates/import-docx',{method:'POST',body:data}));form.value.name=file.name.replace(/\.docx$/i,'')
}catch(e){error.value=e.message}finally{busy.value=false}}
async function save(){busy.value=true;error.value='';try{
 const data=await api(`/api/templates/imports/${parsed.value.import_id}/save`,{method:'POST',body:JSON.stringify({...form.value,headings:headings.value,mappings:mappings.value,parent_id:props.sourceTemplate?.id||null})});emit('saved',data.id)
}catch(e){error.value=e.message}finally{busy.value=false}}
async function original(){try{await download(`/api/templates/${props.sourceTemplate.id}/original`,'模板原文件.docx')}catch(e){error.value=e.message}}
function cellText(cell){return cell.block_ids.map(id=>blocks.value[id]?.text||`[嵌套表格 ${id}]`).join('\n')}
</script>
<template><div class="modal-backdrop"><section class="modal docx-import"><div class="modal-header"><h3>DOCX模板结构解析与字段校正</h3><button class="icon-button" @click="emit('close')">×</button></div><div class="modal-body">
 <p class="help">只解析本地OOXML，不执行宏或访问外部链接。每个有效标题对应一节；固定文本和表格仅作模板结构，不作为知识库事实证据。保留原DOCX，当前不承诺复杂版式的无损导出。</p>
 <div v-if="error" class="error-banner">{{error}}</div>
 <label v-if="!sourceTemplate" class="field">上传DOCX报告模板<input type="file" accept=".docx" :disabled="busy" @change="upload"/></label>
 <button v-else class="secondary-button" @click="original">下载模板原文件</button>
 <div v-if="parsed">
  <p>{{parsed.filename}} · 导入 #{{parsed.import_id}}</p><small v-if="parsed.sha256">SHA256 {{parsed.sha256}}</small>
  <div v-if="parsed.structure.warnings.length" class="warning"><h4>解析警告（需人工核对）</h4><ul><li v-for="warning in parsed.structure.warnings" :key="warning">{{warning}}</li></ul></div>
  <div class="grid"><label class="field">模板名称<input v-model="form.name" class="input" maxlength="100" required/></label><label class="field">版本号<input v-model="form.version" class="input" maxlength="40" required/></label></div>
  <label class="field">用途<textarea v-model="form.purpose" class="textarea" maxlength="2000"/></label><label class="field">说明<input v-model="form.description" class="input" maxlength="1000"/></label>
  <label class="field">适用知识库（可多选，未选表示通用模板）<select v-model="form.knowledge_base_ids" class="select" multiple><option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{kb.name}}</option></select></label>
  <h4>标题层级与固定文本</h4><p class="help">0表示固定正文，可校正被误识别或缺失的标题；1–9为大纲级别，最多20个有效标题。</p>
  <div class="structure-list"><div v-for="(b,i) in bodyBlocks" :key="b.id" class="block"><label>段落 {{b.id}} 的标题级别<select v-model.number="headings[i].level" class="select"><option :value="0">0 · 固定正文</option><option v-for="level in 9" :key="level" :value="level">{{level}}级标题</option></select></label><p>{{b.text||'（空段落）'}}</p></div></div>
  <h4>占位符字段映射 · {{mappings.length}}项</h4><p class="help">支持跨run占位符、MERGEFIELD和内容控件标签。每个字段需选择来源；章节映射影响该节生成要求，元数据或固定值保存供模板应用核对，非知识库证据。</p>
  <div v-for="mapping in mappings" :key="mapping.key" class="mapping"><strong>{{mapping.key}}</strong><label>字段来源<select v-model="mapping.target" class="select"><option value="section">知识库证据生成章节</option><option value="report_title">报告标题</option><option value="knowledge_base">知识库名称</option><option value="generated_at">生成时间</option><option value="literal">固定值</option><option value="ignore">忽略</option></select></label><label v-if="mapping.target==='section'">映射章节<select v-model="mapping.section_block_id" class="select"><option v-for="h in headingOptions" :key="h.block_id" :value="h.block_id">{{h.level}}级 · {{h.text}}</option></select></label><label v-if="mapping.target==='literal'">固定值<input v-model="mapping.value" class="input" maxlength="2000"/></label></div>
  <h4>表格结构 · {{parsed.structure.tables.length}}个</h4><div v-for="table in parsed.structure.tables" :key="table.id" class="table-wrap"><p>{{table.id}} · {{table.part}} · {{table.rows.length}}行 <span v-if="table.parent_table">· 嵌套于{{table.parent_table}}</span></p><table><tbody><tr v-for="row in table.rows" :key="row.index"><td v-for="cell in row.cells" :key="cell.column" :colspan="cell.column_span"><small>行{{cell.row+1}} 列{{cell.column+1}} · 跨{{cell.column_span}}列 <span v-if="cell.vertical_merge">· 纵向合并{{cell.vertical_merge}}</span></small><pre>{{cellText(cell)}}</pre></td></tr></tbody></table></div>
  <details><summary>全部解析数据（含页眉页脚、字段位置与警告）</summary><pre class="raw">{{JSON.stringify(parsed.structure,null,2)}}</pre></details>
 </div>
 </div><div class="modal-footer"><button class="primary-button" :disabled="!parsed||busy||!form.name.trim()" @click="save">{{busy?'处理中…':'保存为新模板版本'}}</button><button class="secondary-button" @click="emit('close')">关闭</button></div></section></div></template>
<style scoped>.docx-import{max-width:1050px;width:94vw;max-height:calc(100dvh - 40px);display:flex;flex-direction:column}.docx-import .modal-body{overflow:auto;min-height:0;display:block}.docx-import .modal-header,.docx-import .modal-footer{flex-shrink:0}.docx-import .modal-footer{padding-top:12px;border-top:1px solid #e4edf2}.help,.block label,small{font-size:12px;color:#617789;line-height:1.7;overflow-wrap:anywhere}.grid{display:grid;grid-template-columns:2fr 1fr;gap:12px}.warning{background:#fff7dc;padding:12px;margin:12px 0;font-size:13px}.structure-list{max-height:340px;overflow:auto;border:1px solid #d7e3ea}.block{display:grid;grid-template-columns:170px 1fr;gap:16px;padding:10px;border-bottom:1px solid #e5edf3}.block p{white-space:pre-wrap;margin:0;line-height:1.8}.mapping{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid #e4edf2}.mapping label{font-size:12px}.mapping strong{min-width:120px}.table-wrap{margin:12px 0}pre{white-space:pre-wrap;font-family:inherit}.raw{max-height:350px;overflow:auto;font-size:12px}</style>

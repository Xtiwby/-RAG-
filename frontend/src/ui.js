import { h } from 'vue'
import { Modal, message } from 'ant-design-vue'

export const uiTheme = { token: { colorPrimary:'#1677ff', colorText:'#1f2329', colorTextSecondary:'#626b79', colorBorder:'#e5e8ef', borderRadius:6, fontSize:14, controlHeight:36, fontFamily:'"Microsoft YaHei", "PingFang SC", system-ui, sans-serif' } }
export function confirmAction(description, {title='请确认操作',danger=false,okText='确认'}={}) {
  return new Promise(resolve=>Modal.confirm({title,content:description,okText,cancelText:'取消',centered:true,okButtonProps:{danger},autoFocusButton:'cancel',onOk:()=>{resolve(true)},onCancel:()=>{resolve(false)}}))
}
export function requestText(title, initial='', {maxLength=100}={}) {
  let value=initial
  return new Promise(resolve=>Modal.confirm({title,centered:true,okText:'保存',cancelText:'取消',autoFocusButton:null,
    content:h('input',{class:'input',value,maxlength:maxLength,'aria-label':title,onInput:e=>{value=e.target.value},onVnodeMounted:vnode=>vnode.el?.focus()}),
    onOk:()=>{if(!value.trim()){message.warning('内容不能为空');return Promise.reject(new Error('empty'))}resolve(value.trim())},onCancel:()=>{resolve(null)}}))
}
export const statusNames={needs_extraction:'待视觉解析',ready:'已就绪',active:'启用',disabled:'已停用',archived:'已归档',deleted:'已删除',queued:'等待处理',running:'处理中',generating:'生成中',uploading:'上传中',processing:'处理中',processing_failed:'处理失败',parse_failed:'解析失败',failed:'失败',interrupted:'已中断',cancelled:'已取消',draft:'草稿',in_review:'待审阅',finalized:'已定稿',completed:'已完成',needs_review:'待审阅',success:'成功',failure:'失败',denied:'拒绝访问',unknown:'结果未知',legacy:'历史记录',text:'文本',image:'图片',table:'表格',hybrid:'混合检索',vector:'向量检索',keyword:'关键词检索'}
export const labelOf=value=>statusNames[value]||value||'—'
export const dateTime=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—'
export const fileSize=value=>value>=1048576?`${(value/1048576).toFixed(1)} MB`:`${Math.max(1,Math.round(value/1024))} KB`

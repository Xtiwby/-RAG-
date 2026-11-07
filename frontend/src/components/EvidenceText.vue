<script setup>
import { computed } from 'vue'
const props=defineProps({content:{type:String,default:''},citations:{type:Array,default:()=>[]},highlight:{type:String,default:''},compact:{type:Boolean,default:false},previewCitations:{type:Boolean,default:false}})
const emit=defineEmits(['select-citation'])
const blocks=computed(()=>props.content.split('\n').map(line=>{
  const heading=/^(#{1,6})\s+(.+)$/.exec(line)
  const content=heading?heading[2]:line
  return {tag:heading?'h'+Math.min(6,heading[1].length+2):'div',highlight:!!props.highlight&&line.includes(props.highlight),parts:content.split(/(\*\*[^*]+\*\*)/g).flatMap(segment=>{
    const bold=segment.startsWith('**')&&segment.endsWith('**')
    const text=bold?segment.slice(2,-2):segment
    return text.split(/(\[\d+\])/g).map(part=>{
      const number=Number(/^\[(\d+)\]$/.exec(part)?.[1])
      return {text:part,bold,number,source:props.citations[number-1]}
    })
  })}
}))
const location=(c)=>({path:`/documents/${c.document_id}`,query:{chunk:c.chunk_id}})
const displayed=computed(()=>{
 const used=new Set([...props.content.matchAll(/\[(\d+)\]/g)].map(m=>Number(m[1])))
 return props.citations.map((c,i)=>({...c,number:i+1})).filter(c=>used.has(c.number))
})
</script>
<template><div class="evidence-content"><component :is="block.tag" v-for="(block,j) in blocks" :key="j" class="text-line" :class="{'highlight-line':block.highlight}"><template v-for="(part,i) in block.parts" :key="i"><button v-if="part.source&&previewCitations" type="button" class="citation-inline citation-preview-button" :aria-label="'预览引用 '+part.text" aria-controls="chat-citation-panel" @click="emit('select-citation',part.number)">{{part.text}}</button><router-link v-else-if="part.source" :to="location(part.source)" class="citation-inline" :title="part.source.source">{{part.text}}</router-link><strong v-else-if="part.bold">{{part.text}}</strong><span v-else>{{part.text}}</span></template></component></div><details v-if="displayed.length&&!compact" class="evidence-list"><summary>引用证据 · {{displayed.length}} 条（点击查看原文与上下文）</summary><article v-for="c in displayed" :key="c.chunk_id"><router-link :to="location(c)">[{{c.number}}] {{c.name}}</router-link><small>{{c.section}} · {{c.page?`第 ${c.page} 页`:'流式文档，无固定页码'}} · {{c.version}} · 片段 #{{c.chunk_id}} · 相关度 {{c.relevance}}</small><p>{{c.text || c.snippet}}</p></article></details></template>
<style scoped>.evidence-content{line-height:1.85;overflow-wrap:anywhere}.text-line{white-space:pre-wrap;min-height:10px;margin:0}.evidence-content h3,.evidence-content h4,.evidence-content h5,.evidence-content h6{font-size:15px;margin:10px 0 5px}.citation-inline{color:#1666a1;font-weight:800;padding:0 3px}.citation-preview-button{border:0;background:none;font-size:inherit;line-height:inherit;vertical-align:baseline}.citation-preview-button:hover{text-decoration:underline}.evidence-list{margin-top:12px;font-size:12px}.evidence-list summary{cursor:pointer;color:#24638f}.evidence-list article{background:white;border:1px solid #d9e2e8;border-radius:9px;padding:10px;margin-top:8px}.evidence-list small{display:block;margin:5px 0;color:#617789}.evidence-list p{white-space:pre-wrap;max-height:180px;overflow:auto;margin-bottom:0;line-height:1.7}.evidence-list a{color:#24638f;font-weight:700}</style>

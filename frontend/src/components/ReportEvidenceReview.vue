<script setup>
import {computed} from 'vue'
import {ArrowRight,ChevronDown,Info} from '../icons'

const props=defineProps({entries:{type:Array,required:true}})
const emit=defineEmits(['select-section'])
const hasEvidenceWarnings=computed(()=>props.entries.some(entry=>entry.verification_state))
const summary=computed(()=>{
  const reviewed=props.entries.filter(entry=>entry.human_accepted).length
  const pending=props.entries.length-reviewed
  if (!pending) return '当前正文已人工审阅，可纳入定稿。'
  if (!reviewed) return '当前正文待人工审阅。'
  return `${reviewed} 份正文已人工审阅，${pending} 份待审阅。`
})
const evidenceLabels={
  insufficient:'证据不足',
  partial:'部分内容未通过核验',
  synthesized:'待证据核验',
  unverified_manual:'当前修订待核验',
  legacy:'尚无核验记录'
}
</script>

<template>
 <details class="evidence-review">
  <summary class="evidence-overview">
   <span class="evidence-icon"><Info :size="17"/></span>
   <span class="evidence-copy">
    <strong>{{hasEvidenceWarnings?'证据核验提醒':'正文审阅提醒'}}</strong>
    <span>{{summary}}</span>
   </span>
   <span class="evidence-toggle"><span>查看详情</span><ChevronDown :size="15"/></span>
  </summary>
  <ul class="evidence-chapters">
   <li v-for="entry in entries" :key="entry.section_id">
    <button type="button" class="evidence-chapter" @click="emit('select-section',entry.section_id)">
     <span class="chapter-copy">
      <strong>{{entry.title}}</strong>
      <small v-if="entry.verification_state">{{evidenceLabels[entry.verification_state]||'尚未通过核验'}}</small>
     </span>
     <span class="report-review-status">{{entry.human_accepted?'已人工审阅':'待人工审阅'}}</span>
     <ArrowRight :size="14" class="chapter-arrow"/>
    </button>
   </li>
  </ul>
 </details>
</template>

<style scoped>
.evidence-review{min-width:0;border:1px solid var(--line);border-radius:10px;background:var(--surface);overflow:hidden}
.evidence-overview{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:12px;padding:13px 14px;list-style:none;cursor:pointer;background:#f8faff}
.evidence-overview::-webkit-details-marker{display:none}
.evidence-overview:hover{background:#f1f6ff}
.evidence-icon{width:32px;height:32px;display:grid;place-items:center;border-radius:8px;background:var(--blue-soft);color:#205cb0}
.evidence-copy,.chapter-copy{min-width:0;display:grid;gap:2px;overflow-wrap:anywhere}
.evidence-copy strong,.chapter-copy strong{font-weight:600;font-size:13px;color:var(--text);line-height:1.45}
.evidence-copy>span,.chapter-copy small{color:var(--muted);font-size:12px;line-height:1.5}
.evidence-toggle{display:inline-flex;align-items:center;gap:4px;color:#205cb0;font-size:12px;white-space:nowrap}
.evidence-toggle .ui-icon{transition:transform .16s ease}
.evidence-review[open] .evidence-toggle .ui-icon{transform:rotate(180deg)}
.evidence-chapters{margin:0;padding:0 12px;list-style:none}
.evidence-chapters li{border-top:1px solid var(--line)}
.evidence-chapter{display:grid;width:100%;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:10px;border:0;border-radius:6px;padding:9px 3px;text-align:left;background:transparent;cursor:pointer}
.evidence-chapter:hover{background:#f5f8fc}
.evidence-overview:focus-visible,.evidence-chapter:focus-visible{outline:2px solid #205cb0;outline-offset:-2px}
.chapter-arrow{color:#657991}
@media(max-width:420px){
 .evidence-overview{gap:9px;padding:11px}
 .evidence-toggle span{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
 .evidence-chapter{gap:6px}
}
@media(prefers-reduced-motion:reduce){.evidence-toggle .ui-icon{transition:none}}
</style>

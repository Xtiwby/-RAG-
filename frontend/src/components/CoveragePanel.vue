<script setup>
defineProps({assessment:{type:Object,default:()=>({})}})
const names={covered:'已覆盖（模型评估）',supplemented:'已补充并核验',missing:'仍有遗漏',insufficient:'证据不足'}
</script>
<template>
 <details v-if="assessment?.status" class="coverage-panel" :open="assessment.status==='unavailable'||!assessment.complete">
  <summary>问题关键点检查（模型评估） · {{assessment.status==='unavailable'?'暂不可用':assessment.complete?'模型认为已覆盖':'请复核缺失项'}}</summary>
  <p>覆盖判断由模型执行，不保证绝对完整；补充事实须另经逐句引用核验。不会将无证据内容自动发布。</p>
  <p v-if="assessment.reason" role="status">{{assessment.reason}}</p>
  <ol v-if="assessment.items?.length"><li v-for="(item,i) in assessment.items" :key="i"><strong>{{names[item.status]||item.status}}</strong>：{{item.requirement}}</li></ol>
  <small v-if="assessment.repair_attempted">本轮已尝试一次有限补充，不会反复自动重写。</small>
 </details>
</template>
<style scoped>
.coverage-panel{margin:10px 0;padding:12px;border:1px solid var(--line,#d7e3ec);border-radius:8px;background:#fafcff;font-size:12px;line-height:1.8}.coverage-panel summary{cursor:pointer;font-weight:600;color:#245e82}.coverage-panel p,.coverage-panel small{color:var(--muted,#65758b)}.coverage-panel ol{padding-left:20px}.coverage-panel li{margin:6px 0}
</style>

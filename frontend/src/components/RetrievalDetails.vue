<script setup>
import {computed} from 'vue'
import {embeddingLabel,rerankLabel} from '../retrieval'
const props=defineProps({pipeline:{type:Object,default:null}})
const labels={disabled:'未启用双语改写',not_needed:'本次无需跨语言改写',expanded:'已进行中英双语检索',degraded:'双语改写已降级'}
const expansion=computed(()=>props.pipeline?.query_expansion)
const warnings=computed(()=>[...new Set([...(expansion.value?.warnings||[]),...(props.pipeline?.attempts||[]).flatMap(a=>a.retrieval?.query_expansion?.warnings||[])])])
</script>
<template>
 <div v-if="pipeline&&Object.keys(pipeline).length" class="retrieval-details">
  <p v-for="warning in warnings" :key="warning" class="retrieval-warning" role="status">{{warning}}</p>
  <details>
   <summary>检索过程<span v-if="expansion"> · {{labels[expansion.status]||expansion.status}}</span><span v-if="pipeline.policy==='section-focus-v2'"> · 优先本节主题{{warnings.length?'（含降级查询）':''}}</span></summary>
   <template v-if="pipeline.attempts">
    <div v-for="(attempt,i) in pipeline.attempts" :key="i" class="retrieval-attempt">
     <p>{{i+1}}. {{attempt.reason==='empty-focus-fallback'?'主题检索无结果，补充报告背景':attempt.reason==='generic-section-context'?'通用标题补充报告主题':'本节主题'}}：{{attempt.query}}</p>
     <RetrievalDetails :pipeline="attempt.retrieval"/>
    </div>
   </template>
   <template v-else>
    <p>向量：{{embeddingLabel(pipeline.embedding)}} · 重排：{{rerankLabel(pipeline.rerank)}}<span v-if="pipeline.top_k"> · TopK {{pipeline.top_k}}</span></p>
    <p v-if="pipeline.embedding_model">语义模型：{{pipeline.embedding_model}}</p>
    <p v-if="pipeline.context_result_count">主检索 {{pipeline.main_result_count}} 条，另补相邻原文 {{pipeline.context_result_count}} 条（独立片段与引用，未单独相关性评分）。</p>
    <p v-if="pipeline.reranking">重排模型：{{pipeline.reranking.model}} · 候选 {{pipeline.reranking.candidate_count}} 条 · {{pipeline.reranking.device||'未执行'}}<span v-if="pipeline.reranking.at_length_limit"> · {{pipeline.reranking.at_length_limit}} 条达到输入长度上限（模型仅评分截取部分，原文引用不变）</span></p>
    <p v-if="expansion?.status==='expanded'">只改写信息需求，不生成答案。{{expansion.cache==='hit'?'本次复用短期改写缓存。':'本次调用生成模型改写，可能产生API费用。'}}所有回答仍须使用授权原文证据。</p>
    <ol v-if="expansion?.queries?.length"><li v-for="(q,i) in expansion.queries" :key="i"><span>{{q.language==='original'?'原问题':q.language==='en'?'英文':'中文'}}{{q.group?.startsWith('part-')?' · 子问题 '+q.group.slice(5):''}}</span>{{q.query}}</li></ol>
   </template>
  </details>
 </div>
</template>
<style scoped>
.retrieval-details{font-size:12px;line-height:1.7;color:var(--muted);margin:12px 0;overflow-wrap:anywhere}.retrieval-details summary{cursor:pointer;color:var(--primary);padding:4px 0}.retrieval-details p{margin:8px 0}.retrieval-details ol{padding-left:20px;margin:10px 0}.retrieval-details li{padding:5px 0}.retrieval-details li span{font-size:11px;color:var(--muted);display:block}.retrieval-warning{color:#9b6700;background:#fffbe6;border:1px solid #ffe58f;border-radius:6px;padding:10px}.retrieval-attempt{border-left:2px solid var(--border);padding-left:12px;margin:8px 0}
</style>

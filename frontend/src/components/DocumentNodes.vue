<script setup>
defineProps({nodes:{type:Array,default:()=>[]}})
function colspanStart(cells,index){return cells.slice(0,index).reduce((n,c)=>n+c.span,0)}
function rowspan(rows,ri,ci){const cell=rows[ri].cells[ci];if(cell.merge!=='restart')return 1;const col=colspanStart(rows[ri].cells,ci);let n=1;for(let i=ri+1;i<rows.length;i++){const next=rows[i].cells.find((c,j)=>colspanStart(rows[i].cells,j)===col);if(next?.merge!=='continue')break;n++}return n}
</script>
<template><div class="document-nodes"><template v-for="(node,i) in nodes" :key="i"><table v-if="node.type==='table'"><tbody><tr v-for="(row,ri) in node.rows" :key="ri" :class="{header:row.header}"><template v-for="(cell,ci) in row.cells" :key="ci"><td v-if="cell.merge!=='continue'" :colspan="cell.span" :rowspan="rowspan(node.rows,ri,ci)"><DocumentNodes :nodes="cell.nodes"/></td></template></tr></tbody></table><component :is="node.type==='heading'?'h'+Math.min(6,node.level):'p'" v-else>{{node.text}}</component></template></div></template>

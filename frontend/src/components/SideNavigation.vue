<script setup>
import {Bot,ChevronLeft,X} from '../icons'
defineProps({groups:Array,activePath:String,collapsed:Boolean,mobile:Boolean})
defineEmits(['navigate','collapse','close'])
const selected=(path,current)=>path==='/'?current==='/':path==='/reports'?current.startsWith('/reports'):current.startsWith(path)
</script>
<template><div class="nav-panel"><div class="brand"><div class="brand-mark"><Bot :size="22"/></div><div class="brand-copy"><strong>知源</strong><span>知识工作空间</span></div><button v-if="mobile" class="icon-button" aria-label="关闭导航" @click="$emit('close')"><X :size="18"/></button></div><nav class="nav-list" aria-label="主导航"><section v-for="group in groups" :key="group.label" class="nav-group"><p class="nav-group-label">{{group.label}}</p><button v-for="item in group.items" :key="item.path" class="nav-item" :class="{active:selected(item.path,activePath)}" :aria-current="selected(item.path,activePath)?'page':undefined" :title="item.label" @click="$emit('navigate',item.path)"><component :is="item.icon" :size="18"/><span>{{item.label}}</span></button></section></nav><div class="sidebar-bottom"><span class="workspace-note">知识有据 · 决策由你</span><button v-if="!mobile" class="icon-button" :aria-label="collapsed?'展开导航':'收起导航'" @click="$emit('collapse')"><ChevronLeft :size="18" :class="{rotated:collapsed}"/></button></div></div></template>

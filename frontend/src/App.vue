<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {ConfigProvider,Drawer,Dropdown,Menu as AMenu,MenuItem} from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import {Activity,BookOpen,Bot,Database,FileSearch,FileText,LayoutDashboard,LayoutTemplate,Menu,Settings,ShieldCheck,Users,ChevronDown,User,LogOut} from './icons'
import SideNavigation from './components/SideNavigation.vue'
import {api,clearSession,sessionUser,updateUser,can,getToken} from './api'
import {uiTheme,confirmAction} from './ui'
const route=useRoute(),router=useRouter(),collapsed=ref(localStorage.getItem('rag_nav_collapsed')==='true'),mobileOpen=ref(false)
const backendInfo=ref(null),health=ref('checking'),user=sessionUser
const oldBackend=computed(()=>backendInfo.value&&Number((backendInfo.value.version||'0.0').split('.')[1])<7)
const isLogin=computed(()=>route.path==='/login')
const groups=computed(()=>[
 {label:'工作空间',items:[{path:'/',label:'工作台',icon:LayoutDashboard},{path:'/knowledge',label:'知识库',icon:Database,point:'kb.read'},{path:'/documents',label:'文档管理',icon:FileText,point:'kb.read'}]},
 {label:'智能应用',items:[{path:'/chat',label:'知识问答',icon:Bot,point:'qa.use'},{path:'/reports',label:'报告中心',icon:BookOpen,point:'reports.generate'},{path:'/report-templates',label:'模板库',icon:LayoutTemplate,point:'reports.generate'}]},
 {label:'调试与运维',items:[{path:'/search',label:'检索测试',icon:FileSearch,point:'qa.use'},{path:'/operations',label:'任务与运行',icon:Activity,point:'ops.read'}]},
 {label:'系统管理',items:[{path:'/users',label:'用户与权限',icon:Users,visible:can('users.manage')||can('roles.manage')},{path:'/audit',label:'审计日志',icon:ShieldCheck,point:'audit.read'},{path:'/settings',label:'系统配置',icon:Settings,point:'settings.manage'}]},
].map(g=>({...g,items:g.items.filter(i=>i.visible!==false&&(!i.point||can(i.point)))})).filter(g=>g.items.length))
const currentGroup=computed(()=>groups.value.find(g=>g.items.some(i=>i.path==='/'?route.path==='/':route.path.startsWith(i.path)))?.label||'账户设置')
async function checkHealth(){if(!getToken())return;health.value='checking';try{backendInfo.value=await api('/api/health');health.value=backendInfo.value.status==='ok'?'online':'error'}catch{health.value='error'}}
let timer
onMounted(()=>{checkHealth();timer=setInterval(checkHealth,30000)})
onUnmounted(()=>clearInterval(timer))
watch(()=>route.path,async()=>{mobileOpen.value=false;if(getToken())try{updateUser(await api('/api/auth/me'));if(!backendInfo.value)checkHealth()}catch{}},{immediate:true})
watch(collapsed,value=>localStorage.setItem('rag_nav_collapsed',String(value)))
function navigate(path){mobileOpen.value=false;router.push(path)}
async function logout(){if(!await confirmAction('退出当前账户？请先保存正在编辑的内容，未保存的修改可能丢失。',{title:'退出登录',okText:'退出登录'}))return;try{await api('/api/auth/logout',{method:'POST'})}catch{}clearSession();router.push('/login')}
</script>
<template>
 <ConfigProvider :theme="uiTheme" :locale="zhCN">
  <router-view v-if="isLogin"/>
  <div v-else class="app-shell" :class="{'sidebar-collapsed':collapsed}">
   <a class="skip-link" href="#main-content">跳到主要内容</a>
   <aside class="sidebar"><SideNavigation :groups="groups" :active-path="route.path" :collapsed="collapsed" @navigate="navigate" @collapse="collapsed=!collapsed"/></aside>
   <Drawer :open="mobileOpen" placement="left" :width="256" :closable="false" :body-style="{padding:0}" @close="mobileOpen=false"><SideNavigation :groups="groups" :active-path="route.path" mobile @navigate="navigate" @close="mobileOpen=false"/></Drawer>
   <main class="main-area">
    <header class="topbar">
     <div class="breadcrumb"><button class="icon-button mobile-menu" aria-label="打开导航" @click="mobileOpen=true"><Menu :size="20"/></button><span class="breadcrumb-group">{{currentGroup}}</span><span class="breadcrumb-separator">/</span><span>{{route.meta.title}}</span></div>
     <div class="top-actions"><button class="top-health" :class="health" :title="health==='online'?'后端健康检查正常；模型连通性请到系统配置中检测':'点击重新检测后端连接'" @click="checkHealth"><span class="status-dot"/>{{health==='online'?'服务在线':health==='checking'?'检测中':'连接异常'}}</button><Dropdown :trigger="['click']"><button class="user-block" aria-label="账户菜单"><span class="avatar">{{user?.name?.slice(0,1)||'用'}}</span><span class="user-copy">{{user?.name}}</span><ChevronDown :size="14"/></button><template #overlay><AMenu><MenuItem key="profile" @click="navigate('/profile')"><User :size="16"/> 个人账户</MenuItem><MenuItem key="logout" @click="logout"><LogOut :size="16"/> 退出登录</MenuItem></AMenu></template></Dropdown></div>
    </header>
    <div id="main-content" class="content-area" tabindex="-1"><div v-if="oldBackend" class="error-banner" role="alert">当前后端版本较旧，请重启后端后使用新功能。</div><div v-if="backendInfo?.data_scope==='acceptance'" class="warning-banner">独立验收环境，不是正式资料库</div><router-view/></div>
   </main>
  </div>
 </ConfigProvider>
</template>


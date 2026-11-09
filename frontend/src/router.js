import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from './api'
const ProfileView=()=>import('./views/ProfileView.vue')
const DocumentView=()=>import('./views/DocumentView.vue')
const LoginView=()=>import('./views/LoginView.vue')
const DashboardView=()=>import('./views/DashboardView.vue')
const KnowledgeView=()=>import('./views/KnowledgeView.vue')
const DocumentsView=()=>import('./views/DocumentsView.vue')
const SearchView=()=>import('./views/SearchView.vue')
const ChatView=()=>import('./views/ChatView.vue')
const ReportsView=()=>import('./views/ReportsHome.vue')
const UsersView=()=>import('./views/UsersView.vue')
const AuditView=()=>import('./views/AuditView.vue')
const SettingsView=()=>import('./views/SettingsView.vue')
const OperationsView=()=>import('./views/OperationsView.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView, meta: { public: true, title: '登录' } },
    { path: '/', component: DashboardView, meta: { title: '工作台' } },
    { path: '/knowledge', component: KnowledgeView, meta: { title: '知识库管理' } },
    { path: '/documents', component: DocumentsView, meta: { title: '文档与多模态内容' } },
    { path: '/documents/:id', component: DocumentView, meta: { title: '文档与引用定位' } },
    { path: '/profile', component: ProfileView, meta: { title: '个人账户' } },
    { path: '/search', component: SearchView, meta: { title: '检索测试' } },
    { path: '/chat', component: ChatView, meta: { title: '知识问答' } },
    { path: '/reports', component: ReportsView, meta: { title: '报告中心' } },
    { path: '/reports/new', component: ReportsView, meta: { title: '新建报告' } },
    { path: '/reports/:id', component: ReportsView, meta: { title: '报告编辑器' } },
    { path: '/report-templates', component: ReportsView, meta: { title: '模板库' } },
    { path: '/report-templates/new', component: ReportsView, meta: { title: '创建模板' } },
    { path: '/report-templates/:id/edit', component: ReportsView, meta: { title: '模板编辑器' } },
    { path: '/users', component: UsersView, meta: { title: '用户与权限' } },
    { path: '/audit', component: AuditView, meta: { title: '审计日志' } },
    { path: '/settings', component: SettingsView, meta: { title: '系统配置' } },
    { path: '/operations', component: OperationsView, meta: { title: '运行状态' } },
  ],
})

router.beforeEach((to) => {
  document.title = `${to.meta.title || '系统'} - RAG知识库管理系统`
  if (!to.meta.public && !getToken()) return {path:'/login',query:{redirect:to.fullPath}}
  if (to.path === '/login' && getToken()) return '/'
})

export default router


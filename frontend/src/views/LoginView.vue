<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bot, Database, FileSearch, ShieldCheck } from '../icons'
import { api, saveSession } from '../api'

const router = useRouter()
const username = ref('admin')
const password = ref('admin123')
const error = ref('')
const loading = ref(false)

async function login() {
  loading.value = true
  error.value = ''
  try {
    const data = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) })
    saveSession(data)
    router.push('/')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="login-hero">
      <div class="login-brand"><div class="login-logo"><Bot :size="28" /></div><span>知源 RAG</span></div>
      <div class="hero-content">
        <span class="hero-kicker">知源 · 知识工作空间</span>
        <h1>让组织知识<br />可检索、可生成、可追溯</h1>
        <p>面向课程项目的知识库管理系统，支持文本、图片与表格内容，以及向量、关键字和混合检索。</p>
        <div class="hero-features">
          <div><Database :size="20" /><span>多模态知识入库</span></div>
          <div><FileSearch :size="20" /><span>三种检索模式</span></div>
          <div><ShieldCheck :size="20" /><span>权限隔离与审计</span></div>
        </div>
      </div>
      <div class="hero-foot">卓越工程综合训练 · RAG项目组</div>
    </section>
    <section class="login-panel">
      <form class="login-form" @submit.prevent="login">
        <div class="mobile-brand"><Bot :size="26"/>知源 RAG</div>
        <h2>登录管理系统</h2>
        <p>使用项目账户进入知识工作台</p>
        <div v-if="error" class="error-banner">{{ error }}</div>
        <div class="field"><label for="login-user">用户名</label><input id="login-user" required v-model="username" class="input" autocomplete="username" /></div>
        <div class="field"><label for="login-password">密码</label><input id="login-password" required v-model="password" class="input" type="password" autocomplete="current-password" /></div>
        <button class="primary-button login-button" :disabled="loading">{{ loading ? '正在登录…' : '登录系统' }}</button>
        <div class="demo-account">演示账号：admin / admin123</div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.login-page{min-height:100vh;display:grid;grid-template-columns:1fr 1fr;background:#fff}.login-hero{background:#f3f7ff;color:#1f2329;padding:40px 6vw;display:flex;flex-direction:column;border-right:1px solid #e6edf8}.login-brand{display:flex;align-items:center;gap:10px;font-weight:600;font-size:18px}.login-logo{width:38px;height:38px;border-radius:8px;background:#1677ff;color:white;display:grid;place-items:center}.hero-content{margin:auto 0;max-width:500px;padding:60px 0}.hero-kicker{font-size:13px;color:#1677ff}.hero-content h1{font-size:clamp(26px,3vw,40px);line-height:1.5;margin:16px 0 18px;font-weight:600;letter-spacing:-.03em}.hero-content p{color:#626b79;line-height:1.9;font-size:14px;max-width:430px}.hero-features{display:grid;gap:18px;margin-top:36px}.hero-features div{display:flex;align-items:center;gap:10px;font-size:13px;color:#50637d}.hero-features .ui-icon{color:#1677ff}.hero-foot{font-size:12px;color:#8a929f}.login-panel{display:grid;place-items:center;padding:36px}.login-form{width:min(360px,100%);display:grid;gap:20px}.login-form h2{font-size:24px;margin:0;font-weight:600}.login-form>p{color:#626b79;margin:-12px 0 8px;font-size:13px}.login-button{width:100%;height:40px;margin-top:4px}.demo-account{text-align:center;color:#8a929f;font-size:12px}.mobile-brand{display:none}.field .input{height:40px}@media(max-width:800px){.login-page{grid-template-columns:1fr;background:#f5f7fa}.login-hero{display:none}.login-panel{min-height:100vh;padding:24px}.login-form{background:white;padding:28px 24px;border:1px solid #e5e8ef;border-radius:10px;width:min(410px,100%)}.mobile-brand{display:flex;align-items:center;gap:10px;color:#1677ff;font-size:17px;font-weight:600;margin-bottom:18px}}
</style>


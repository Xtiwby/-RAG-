<script setup>
import {computed,h,onMounted,onUnmounted,ref,watch} from 'vue'
import {onBeforeRouteLeave,useRouter} from 'vue-router'
import {InputPassword} from 'ant-design-vue'
import {api,sessionUser,updateUser,clearSession,getToken} from '../api'
import {User,ShieldCheck,ChevronLeft} from '../icons'
import {confirmAction} from '../ui'
import {normalizeDisplayName,canUpdatePassword} from '../profile-validation'

const router=useRouter(),tab=ref('basic')
const savedName=ref(sessionUser.value?.name||''),name=ref(savedName.value)
const current=ref(''),password=ref(''),confirmation=ref('')
const visibility=ref({current:false,password:false,confirmation:false})
const saving=ref(false),changing=ref(false),nameError=ref(''),passwordError=ref(''),saved=ref(false)
const nameDirty=computed(()=>name.value!==savedName.value)
const passwordDirty=computed(()=>!!(current.value||password.value||confirmation.value))
const busy=computed(()=>saving.value||changing.value)
const dirty=computed(()=>nameDirty.value||passwordDirty.value)
const cleanName=computed(()=>normalizeDisplayName(name.value))
const passwordValid=computed(()=>canUpdatePassword(current.value,password.value,confirmation.value))
const mismatch=computed(()=>confirmation.value&&password.value!==confirmation.value)
const tabs=[{id:'basic',label:'基本资料',description:'显示名与账户信息',icon:User},{id:'security',label:'账户安全',description:'修改登录密码',icon:ShieldCheck}]
const accountStatus=computed(()=>sessionUser.value?.status==='active'?'账户已启用':sessionUser.value?.status==='disabled'?'账户已停用':'账户状态未知')

watch(()=>sessionUser.value?.name,value=>{
  if(!nameDirty.value){savedName.value=value||'';name.value=savedName.value}
})
watch(name,()=>{saved.value=false;nameError.value=''},{flush:'sync'})
watch([current,password,confirmation],()=>passwordError.value='')
watch(tab,()=>{visibility.value={current:false,password:false,confirmation:false}})
function focusTab(event,index){
  const direction=['ArrowRight','ArrowDown'].includes(event.key)?1:['ArrowLeft','ArrowUp'].includes(event.key)?-1:0
  if(!direction&&!['Home','End'].includes(event.key))return
  event.preventDefault()
  const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+direction+tabs.length)%tabs.length
  tab.value=tabs[next].id
  document.getElementById('profile-tab-'+tab.value)?.focus()
}
async function save(){
  if(busy.value||!nameDirty.value)return
  nameError.value='';saved.value=false
  if(!cleanName.value){nameError.value='请输入1–50个字符的显示名，不能只包含空格。';return}
  saving.value=true
  try{
    const user=await api('/api/auth/me',{method:'PATCH',body:JSON.stringify({name:cleanName.value})})
    savedName.value=user.name;name.value=user.name;updateUser(user);saved.value=true
  }catch(e){nameError.value=e.message}finally{saving.value=false}
}
function resetName(){name.value=savedName.value;nameError.value='';saved.value=false}
function clearPasswords(){current.value='';password.value='';confirmation.value='';passwordError.value='';visibility.value={current:false,password:false,confirmation:false}}
function passwordToggleIcon(visible,label){
  return h('button',{type:'button',class:'password-toggle','aria-label':(visible?'隐藏':'显示')+label,'aria-pressed':visible,disabled:busy.value},visible?'隐藏':'显示')
}
async function change(){
  if(busy.value)return
  passwordError.value=''
  if(!passwordValid.value){passwordError.value=mismatch.value?'两次输入的新密码不一致。':'请填写当前密码，并输入8–128位的新密码。';return}
  changing.value=true
  try{
    await api('/api/auth/password',{method:'POST',body:JSON.stringify({current_password:current.value,new_password:password.value})})
    clearPasswords();clearSession();router.push('/login')
  }catch(e){passwordError.value=e.message}finally{changing.value=false}
}
onBeforeRouteLeave(async()=>{
  if(!getToken())return true
  if(busy.value)return false
  return !dirty.value||await confirmAction('有尚未保存的修改，离开后会丢失。密码不会作为草稿保存。',{title:'离开个人账户？',okText:'放弃并离开'})
})
function beforeUnload(event){if(dirty.value||busy.value){event.preventDefault();event.returnValue=''}}
onMounted(()=>window.addEventListener('beforeunload',beforeUnload))
onUnmounted(()=>{window.removeEventListener('beforeunload',beforeUnload);clearPasswords()})
</script>

<template>
  <div class="page-stack profile-page">
    <header class="page-intro"><div><h2>个人账户</h2><p>管理你的个人资料与登录安全。</p></div></header>
    <div class="profile-layout">
      <aside class="card account-sidebar">
        <div class="identity">
          <span class="profile-avatar" aria-hidden="true">{{sessionUser?.name?.slice(0,1)||'用'}}</span>
          <div class="identity-copy"><h3>{{sessionUser?.name||'未命名用户'}}</h3><span class="role-label">{{sessionUser?.role||'未分配角色'}}</span></div>
        </div>
        <div class="account-state"><span class="account-dot" :class="{enabled:sessionUser?.status==='active'}"/>{{accountStatus}}</div>
        <div class="profile-nav" role="tablist" aria-label="账户设置">
          <button v-for="(item,index) in tabs" :id="'profile-tab-'+item.id" :key="item.id" role="tab" :aria-label="item.label" :aria-selected="tab===item.id" :aria-controls="'profile-panel-'+item.id" :tabindex="tab===item.id?0:-1" :class="{selected:tab===item.id}" :disabled="busy" @click="tab=item.id" @keydown="focusTab($event,index)">
            <component :is="item.icon" :size="19"/><span><strong>{{item.label}}</strong><small>{{item.description}}</small></span><ChevronLeft :size="14" class="tab-arrow"/>
          </button>
        </div>
        <p class="sidebar-note"><ShieldCheck :size="15"/><span>角色与知识库权限<br/>由管理员统一分配</span></p>
      </aside>
      <section class="card settings-content">
        <div v-show="tab==='basic'" id="profile-panel-basic" role="tabpanel" aria-labelledby="profile-tab-basic" class="profile-panel">
          <header class="panel-heading"><h3>基本资料</h3><p>设置系统中显示的名称，便于团队成员识别你。</p></header>
          <form @submit.prevent="save">
            <div class="profile-fields">
              <div v-if="nameError" class="error-banner" role="alert">{{nameError}}</div>
              <div v-if="saved" class="success-banner" role="status">显示名已保存，账户信息已同步更新。</div>
              <label for="profile-name" class="field">显示名</label>
              <div class="name-input"><input id="profile-name" v-model="name" autocomplete="nickname" required maxlength="50" :disabled="busy" :aria-invalid="!!nameError" aria-describedby="profile-name-help" placeholder="输入你的显示名"/><span aria-hidden="true">{{name.length}}/50</span></div>
              <p id="profile-name-help" class="field-help">1–50个字符；修改后不会影响登录账号。</p>
              <div class="account-details"><h4>账户信息</h4><dl><div><dt>登录账号</dt><dd>{{sessionUser?.username||'—'}}</dd></div><div><dt>账户角色</dt><dd>{{sessionUser?.role||'—'}}</dd></div></dl><p class="field-help">以上信息仅供查看，如需变更请联系管理员。</p></div>
            </div>
            <footer class="profile-actions"><span class="form-state" role="status"><i :class="{changed:nameDirty}"/>{{saving?'正在保存…':nameDirty?'有未保存的修改':saved?'已保存':'暂无修改'}}</span><div><button type="button" class="secondary-button" :disabled="busy||!nameDirty" @click="resetName">重置</button><button class="primary-button" :disabled="busy||!nameDirty||!cleanName">{{saving?'保存中…':'保存修改'}}</button></div></footer>
          </form>
        </div>
        <div v-show="tab==='security'" id="profile-panel-security" role="tabpanel" aria-labelledby="profile-tab-security" class="profile-panel">
          <header class="panel-heading"><h3>账户安全</h3><p>修改当前账户的登录密码。</p></header>
          <form @submit.prevent="change">
            <div class="profile-fields security-fields">
              <div class="security-notice"><ShieldCheck :size="19"/><div><strong>修改后需要重新登录</strong><p>此账户在所有设备上的登录会话将失效。</p></div></div>
              <div v-if="passwordError" class="error-banner" role="alert">{{passwordError}}</div>
              <label class="field" for="profile-current">当前密码</label><InputPassword id="profile-current" v-model:value="current" v-model:visible="visibility.current" autocomplete="current-password" required :disabled="busy" :icon-render="visible=>passwordToggleIcon(visible,'当前密码')" placeholder="输入当前登录密码"/>
              <label class="field password-label" for="profile-password">新密码</label><InputPassword id="profile-password" v-model:value="password" v-model:visible="visibility.password" autocomplete="new-password" required minlength="8" maxlength="128" :disabled="busy" :icon-render="visible=>passwordToggleIcon(visible,'新密码')" placeholder="设置8–128位新密码" aria-describedby="password-policy"/>
              <p id="password-policy" class="field-help">请设置8–128个字符的密码。</p>
              <label class="field password-label" for="profile-confirm">确认新密码</label><InputPassword id="profile-confirm" v-model:value="confirmation" v-model:visible="visibility.confirmation" autocomplete="new-password" required minlength="8" maxlength="128" :disabled="busy" :icon-render="visible=>passwordToggleIcon(visible,'确认新密码')" :status="mismatch?'error':undefined" :aria-invalid="!!mismatch" aria-describedby="password-confirm-help" placeholder="再次输入新密码"/>
              <p id="password-confirm-help" class="field-help" :class="{'invalid-help':mismatch}" aria-live="polite">{{mismatch?'两次输入的新密码不一致，请检查。':'请再次输入相同的新密码。'}}</p>
            </div>
            <footer class="profile-actions"><span class="form-state password-state">密码不会作为草稿保存</span><div><button type="button" class="secondary-button" :disabled="busy||!passwordDirty" @click="clearPasswords">清空</button><button class="primary-button" :disabled="busy||!passwordValid">{{changing?'更新中…':'更新密码'}}</button></div></footer>
          </form>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.profile-page{max-width:1120px;margin:0 auto;gap:22px}
.profile-layout{display:grid;grid-template-columns:248px minmax(0,1fr);gap:20px;align-items:start}
.account-sidebar{padding:28px 16px 20px;overflow:hidden}
.identity{display:flex;flex-direction:column;align-items:center;text-align:center;padding:0 10px}
.profile-avatar{width:64px;height:64px;border-radius:50%;display:grid;place-items:center;color:#1677ff;background:#eaf2ff;font-size:27px;font-weight:500;border:4px solid #f6f9ff}
.identity-copy{min-width:0;max-width:100%}
.identity h3{font-size:16px;font-weight:600;margin:14px 0 8px;overflow-wrap:anywhere}
.role-label{font-size:11px;color:#65758b;background:#f1f4f9;border:1px solid #e8edf4;border-radius:4px;padding:2px 8px;display:inline-block}
.account-state{display:flex;align-items:center;justify-content:center;gap:6px;font-size:11px;color:#738092;margin:12px 0 26px}
.account-dot{width:5px;height:5px;border-radius:50%;background:#a4acb9}.account-dot.enabled{background:#23835b}
.profile-nav{display:grid;gap:6px;border-top:1px solid #edf0f5;padding-top:18px}
.profile-nav button{border:0;background:transparent;padding:12px 14px;text-align:left;display:flex;align-items:center;gap:11px;color:#697586;border-radius:6px;width:100%}
.profile-nav button:hover{background:#f6f8fb}.profile-nav button.selected{background:#edf5ff;color:#1677ff}
.profile-nav button>span{display:grid;gap:4px;flex:1;min-width:0}.profile-nav strong{font-size:13px;font-weight:500}.profile-nav small{font-size:11px;color:#8993a2;font-weight:400}.profile-nav .selected small{color:#6796d3}
.tab-arrow{transform:rotate(180deg);opacity:.55}
.sidebar-note{display:flex;align-items:flex-start;justify-content:center;gap:8px;margin:28px 0 0;padding-top:18px;border-top:1px solid #edf0f5;font-size:11px;line-height:1.8;color:#8a929f}
.sidebar-note .ui-icon{margin-top:3px}
.settings-content{overflow:hidden;min-width:0}
.panel-heading{padding:26px 32px 22px;border-bottom:1px solid #edf0f5}
.panel-heading h3{margin:0;font-size:17px;font-weight:600;line-height:1.5}
.panel-heading p{margin:7px 0 0;font-size:12px;color:#7b8593;line-height:1.7}
.profile-fields{padding:30px 32px 32px;min-height:323px}
.profile-fields>.error-banner,.profile-fields>.success-banner{margin-bottom:20px}
.profile-fields>.field{font-size:13px;color:#3f4a59;margin-bottom:9px}
.name-input{border:1px solid #dce1e8;border-radius:6px;height:40px;display:flex;align-items:center;max-width:440px;overflow:hidden;background:white}
.name-input:focus-within{border-color:#69b1ff;box-shadow:0 0 0 2px #1677ff16}
.name-input input{border:0;outline:0;min-width:0;flex:1;height:100%;padding:0 12px;font-size:14px;color:#1f2329;background:transparent}
.name-input input:disabled{color:#8893a1;background:#fafbfc}
.name-input>span{color:#a1a9b5;font-size:11px;padding:0 12px;flex-shrink:0}
.field-help{margin:8px 0 0;font-size:12px;color:#7b8593;line-height:1.8}
.account-details{border-top:1px solid #edf0f5;padding-top:22px;margin-top:28px;max-width:440px}
.account-details h4{font-size:12px;font-weight:500;color:#647185;margin:0 0 14px}
.account-details dl{margin:0;display:grid;grid-template-columns:1fr 1fr;gap:20px}
.account-details dl>div{min-width:0}
.account-details dt{font-size:11px;color:#929ba8;margin-bottom:4px}
.account-details dd{margin:0;font-size:13px;color:#46515f;overflow-wrap:anywhere}
.account-details>.field-help{margin-top:18px}
.profile-actions{display:flex;justify-content:space-between;align-items:center;gap:14px;border-top:1px solid #edf0f5;padding:18px 32px;background:#fdfefe}
.profile-actions>div{display:flex;gap:10px;flex-shrink:0}
.profile-actions button{min-width:72px;min-height:36px}
.form-state{font-size:11px;color:#8a929f;display:flex;align-items:center;gap:7px;line-height:1.7}
.form-state i{width:5px;height:5px;border-radius:50%;background:#a9b1bd;flex-shrink:0}
.form-state i.changed{background:#d49b32}
.security-fields{max-width:504px;min-height:0}
.security-notice{display:flex;align-items:flex-start;gap:10px;border:1px solid #deebfc;background:#f6faff;border-radius:6px;padding:12px 14px;margin-bottom:24px;color:#5a7fae}
.security-notice>.ui-icon{margin-top:2px;flex-shrink:0}
.security-notice strong{font-size:12px;font-weight:500;color:#476788}
.security-notice p{font-size:11px;line-height:1.8;margin:4px 0 0;color:#7d90a6}
.password-label{margin-top:22px}
.security-fields :deep(.ant-input-affix-wrapper){height:40px;font-size:13px;border-color:#dce1e8}
.security-fields :deep(.ant-input){font-size:13px}
.security-fields :deep(button.ant-input-password-icon){border:0;background:transparent;font-size:11px;min-width:28px;height:28px;padding:0 2px;color:#7b8593}
.security-fields :deep(button.ant-input-password-icon:hover){color:#1677ff}
.invalid-help{color:#cf3344}
@media(max-width:1000px){.profile-layout{grid-template-columns:214px minmax(0,1fr);gap:16px}.panel-heading{padding:24px}.profile-fields{padding:24px}.profile-actions{padding:18px 24px}.profile-actions{flex-wrap:wrap}.password-state{width:100%}}
@media(max-width:767px){
.profile-page{gap:16px}.profile-layout{grid-template-columns:minmax(0,1fr);gap:16px}
.account-sidebar{padding:18px 18px 0}.identity{flex-direction:row;text-align:left;padding:0;gap:12px}
.profile-avatar{width:48px;height:48px;font-size:22px;flex-shrink:0;border-width:3px}
.identity h3{font-size:15px;margin:0 0 5px}.role-label{font-size:10px;padding:0 6px}
.account-state{justify-content:flex-start;margin:9px 0 16px 60px}
.profile-nav{display:flex;gap:20px;border-top:1px solid #edf0f5;padding-top:0}
.profile-nav button{border-radius:0;justify-content:center;width:auto;gap:7px;padding:12px 2px 11px;border-bottom:2px solid transparent}
.profile-nav button.selected{background:none;border-color:#1677ff}
.profile-nav button>span{flex:0 1 auto;white-space:nowrap}
.profile-nav small,.profile-nav .tab-arrow,.sidebar-note{display:none}
.panel-heading{padding:20px}.panel-heading h3{font-size:16px}.panel-heading p{font-size:12px}
.profile-fields{padding:22px 20px;min-height:0}.account-details{margin-top:24px;padding-top:20px}
.profile-actions{padding:16px 20px;flex-wrap:wrap;gap:12px}.profile-actions>div{margin-left:auto}
.profile-actions button{min-height:38px}.password-state{width:100%}
.security-fields{max-width:none}.security-notice{padding:10px 12px}
}
@media(max-width:380px){.profile-actions>.form-state{width:100%}.profile-actions>div{width:100%;justify-content:flex-end}.account-details dl{gap:12px}.profile-nav{gap:18px}}
</style>


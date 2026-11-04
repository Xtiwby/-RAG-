import { ref } from 'vue'
const API_BASE = import.meta.env.VITE_API_BASE || ''
export const sessionUser = ref(getUser())
export const can = (point) => !!sessionUser.value && (sessionUser.value.system_admin || sessionUser.value.permissions?.includes(point))

export function getToken() {
  return localStorage.getItem('rag_token') || ''
}

export function getUser() {
  try {
    return JSON.parse(localStorage.getItem('rag_user') || 'null')
  } catch {
    return null
  }
}

export function saveSession(payload) {
  localStorage.setItem('rag_token', payload.token)
  localStorage.setItem('rag_user', JSON.stringify(payload.user))
  sessionUser.value = payload.user
}

export function updateUser(user) {
  localStorage.setItem('rag_user', JSON.stringify(user))
  sessionUser.value = user
}

export function clearSession() {
  localStorage.removeItem('rag_token')
  localStorage.removeItem('rag_user')
  sessionUser.value = null
}

export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {})
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (response.status === 401) {
    clearSession()
    if (!location.pathname.includes('/login')) location.href = '/login'
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(errorMessage(data))
  return data
}

function errorMessage(data) {
  if (Array.isArray(data.detail)) return data.detail.map(e => `${e.loc?.slice(1).join('.')}: ${e.msg}`).join('；')
  if(data.detail&&typeof data.detail==='object')return data.detail.message+(data.detail.issues?.length?'：'+data.detail.issues.map(i=>i.message).join('；'):'')
  return data.detail || '请求失败'
}

export async function streamApi(path, body, onEvent, signal) {
  const response = await fetch(`${API_BASE}${path}`, {
    method:'POST', headers:{'Content-Type':'application/json', Authorization:`Bearer ${getToken()}`},
    body:JSON.stringify(body), signal
  })
  if (!response.ok) {
    const data = await response.json().catch(()=>({}))
    if(response.status===401) { clearSession(); location.href='/login' }
    throw new Error(errorMessage(data))
  }
  const reader=response.body.getReader(), decoder=new TextDecoder()
  let pending=''
  const dispatch=(line)=>{
    if(!line.startsWith('data:'))return
    const event=JSON.parse(line.slice(5).trim())
    onEvent(event)
    if(event.type==='error')throw new Error(event.message)
  }
  try {
    while(true){
      const {value,done}=await reader.read()
      pending+=decoder.decode(value || new Uint8Array(),{stream:!done})
      let index
      while((index=pending.indexOf('\n'))>=0){ const line=pending.slice(0,index).trimEnd();pending=pending.slice(index+1);dispatch(line) }
      if(done){if(pending.trim())dispatch(pending.trim());break}
    }
  } finally { await reader.cancel().catch(()=>{});reader.releaseLock() }
}

export async function download(path,name) {
  const response=await fetch(`${API_BASE}${path}`,{headers:{Authorization:`Bearer ${getToken()}`}})
  if(!response.ok)throw new Error(errorMessage(await response.json().catch(()=>({}))))
  const url=URL.createObjectURL(await response.blob())
  const anchor=document.createElement('a');anchor.href=url;anchor.download=name;anchor.click()
  setTimeout(()=>URL.revokeObjectURL(url),1000)
}

export function uploadDocument(form,onProgress) {
  return new Promise((resolve,reject)=>{
    const request=new XMLHttpRequest()
    request.open('POST',`${API_BASE}/api/documents`)
    request.setRequestHeader('Authorization',`Bearer ${getToken()}`)
    request.upload.onprogress=event=>{if(event.lengthComputable)onProgress(Math.round(event.loaded/event.total*100))}
    request.onerror=()=>reject(new Error('文件上传连接失败'))
    request.onabort=()=>reject(new Error('文件上传已中止'))
    request.onload=()=>{
      let data={};try{data=JSON.parse(request.responseText)}catch{}
      if(request.status===401){clearSession();location.href='/login'}
      if(request.status>=200&&request.status<300)resolve(data)
      else reject(new Error(errorMessage(data)))
    }
    request.send(form)
  })
}

export async function originalBlob(id) {
  const response=await fetch(`${API_BASE}/api/documents/${id}/original`,{headers:{Authorization:`Bearer ${getToken()}`}})
  if(!response.ok)throw new Error(errorMessage(await response.json().catch(()=>({}))))
  return URL.createObjectURL(await response.blob())
}


import { reactive, computed } from 'vue'
export const auth=reactive({user:null,csrf:'',needsSetup:false,loaded:false})
export const canOperate=computed(()=>['operator','admin'].includes(auth.user?.role))
export const isAdmin=computed(()=>auth.user?.role==='admin')
export const roleNames={viewer:'查看人员',operator:'巡检操作员',admin:'管理员'}
let pending
export async function loadAuth(){
 if(pending)return pending
 pending=(async()=>{const statusResponse=await fetch('/api/auth/status',{cache:'no-store'});if(!statusResponse.ok)throw Error('无法连接登录服务');const status=await statusResponse.json();if(typeof status.needs_setup!=='boolean')throw Error('初始化状态不可用');auth.needsSetup=status.needs_setup;const r=await fetch('/api/auth/me');if(r.ok){const data=await r.json();auth.user=data.user;auth.csrf=data.csrf_token}else if(r.status===401){auth.user=null;auth.csrf=''}else throw Error('无法连接登录服务');auth.loaded=true})()
 try{await pending}finally{pending=null}
}
export async function signIn(body,setup=false){const r=await fetch(setup?'/api/auth/setup':'/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(data.detail||'登录失败');auth.user=data.user;auth.csrf=data.csrf_token;auth.needsSetup=false;auth.loaded=true}
export function forget(){auth.user=null;auth.csrf='';auth.loaded=false}
export async function signOut(){const r=await fetch('/api/auth/logout',{method:'POST',headers:{'X-CSRF-Token':auth.csrf}});if(!r.ok&&r.status!==401)throw Error('退出失败，请重试');forget()}

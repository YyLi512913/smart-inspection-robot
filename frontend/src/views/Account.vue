<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { auth,roleNames,forget } from '../auth'
import { postJSON } from '../api'
import { ElMessage } from 'element-plus'
const router=useRouter(),oldPassword=ref(''),newPassword=ref(''),confirm=ref(''),busy=ref(false)
async function save(){if(newPassword.value.length<12||newPassword.value!==confirm.value){ElMessage.warning('新密码至少 12 个字符，且两次输入必须一致');return}busy.value=true;try{await postJSON('/api/auth/password',{current_password:oldPassword.value,new_password:newPassword.value});oldPassword.value='';newPassword.value='';confirm.value='';forget();ElMessage.success('密码已修改，请重新登录');router.replace('/login')}catch(e){ElMessage.error(e.message)}finally{busy.value=false}}
</script>
<template><div class="page-heading"><div><div class="eyebrow">YOUR ACCOUNT</div><h1>我的账号</h1><p>你的身份与工作空间权限。</p></div></div><section class="surface account-card"><span class="soft-badge">{{ roleNames[auth.user?.role] }}</span><h2>{{ auth.user?.display_name }}</h2><p>{{ auth.user?.username }}</p><h3>修改密码</h3><form @submit.prevent="save"><label>当前密码<el-input v-model="oldPassword" type="password" show-password autocomplete="current-password"/></label><label>新密码<el-input v-model="newPassword" type="password" show-password maxlength="128" autocomplete="new-password" placeholder="至少 12 个字符"/></label><label>确认新密码<el-input v-model="confirm" type="password" show-password autocomplete="new-password"/></label><el-button native-type="submit" type="primary" :loading="busy">修改并重新登录</el-button></form></section></template>
<style scoped>.account-card{padding:30px;max-width:540px}.account-card h2{font-size:24px;margin:20px 0 8px}.account-card>p{font-size:12px;color:#95a087}.account-card h3{font-size:15px;border-top:1px solid #e7eddf;padding-top:24px;margin-top:26px}.account-card label{display:block;font-size:12px;color:#7d8e70;margin:18px 0}.account-card label .el-input{margin-top:10px}</style>

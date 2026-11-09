<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute,useRouter } from 'vue-router'
import { auth,isAdmin,roleNames,signOut } from './auth'
const router=useRouter()
async function logout(){try{await signOut();router.replace('/login')}catch{window.alert('退出失败，请重试')}}
const route = useRoute()
const nav = computed(()=>[
 { path: '/cockpit', icon: '◫', name: '运行总览' },
 { path: '/equipment', icon: '⌘', name: '设备台账' },
 { path: '/tasks', icon: '▤', name: '巡检任务' },
 { path: '/results', icon: '▧', name: '检测记录' },
 { path: '/alerts', icon: '⚑', name: '异常处理' },
 ...(isAdmin.value?[{path:'/operations',icon:'◉',name:'运行状态'},{path:'/users',icon:'♧',name:'账号管理'}]:[]),
 {path:'/account',icon:'◎',name:'我的账号'}
])
const active = computed(() => nav.value.find(n => route.path.startsWith(n.path)) || nav.value[0])
const now = ref(new Date())
let timer
onMounted(() => { timer = setInterval(() => { now.value = new Date() }, 1000) })
onBeforeUnmount(() => clearInterval(timer))
</script>
<template>
 <router-view v-if="route.meta.public"/>
 <div v-else class="app-shell">
  <aside class="sidebar">
   <router-link to="/cockpit" class="brand"><span class="brand-mark">r·</span><div>巡境<span>ROBOT OPERATIONS</span></div></router-link>
   <div class="workspace-switch"><span class="site-symbol">⌘</span><div>工厂巡检空间<small>Factory · L1</small></div><span class="chevron">⌄</span></div>
   <div class="nav-caption">工作空间</div>
   <nav aria-label="主导航"><router-link v-for="n in nav" :key="n.path" :to="n.path" :class="['nav-link', { selected: active.path === n.path }]"><span class="nav-icon">{{ n.icon }}</span><span>{{ n.name }}</span><i v-if="active.path === n.path" class="nav-active-dot"></i></router-link></nav>
   <div class="sidebar-bottom"><div class="environment"><i></i>仿真环境<span>ROS 2</span></div><div class="profile"><span class="avatar">OP</span><div>{{ auth.user?.display_name }}<small>{{ roleNames[auth.user?.role] }}</small></div></div></div>
  </aside>
  <div class="main-shell">
   <header class="app-header"><div class="breadcrumb">工作空间 <span>/</span> <b>{{ active.name }}</b></div><div class="header-meta"><span class="env-label">SIMULATION</span><span>{{ now.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', timeZone: 'Asia/Shanghai' }) }}</span><time>{{ now.toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }) }}</time><el-button link @click="logout">退出登录</el-button></div></header>
   <main class="page-content"><router-view /></main>
   <footer class="app-footer"><span>巡境 · 智能巡检工作台</span><span>Factory L1 · 仿真工作空间</span></footer>
  </div>
 </div>
</template>
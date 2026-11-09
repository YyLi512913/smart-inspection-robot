import Operations from './views/Operations.vue'
import Login from './views/Login.vue'
import Users from './views/Users.vue'
import Account from './views/Account.vue'
import { auth,loadAuth } from './auth'
import Alerts from './views/Alerts.vue'
import { createRouter, createWebHistory } from 'vue-router'

import Chat from './views/Chat.vue'
import Equipment from './views/Equipment.vue'
import Cockpit from './views/Cockpit.vue'
import Dashboard from './views/Dashboard.vue'
import Results from './views/Results.vue'
import TaskDetail from './views/TaskDetail.vue'
import Tasks from './views/Tasks.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/cockpit' },
    { path:'/login',component:Login,meta:{public:true} },
    { path:'/users',component:Users,meta:{admin:true} },
    { path:'/operations',component:Operations,meta:{admin:true} },
    { path:'/account',component:Account },
    { path: '/cockpit', component: Cockpit },
    { path: '/chat', component: Chat },
    { path: '/dashboard', component: Dashboard },
    { path: '/equipment', component: Equipment },
    { path: '/alerts', component: Alerts },
    { path: '/tasks', component: Tasks },
    { path: '/tasks/:taskNo', component: TaskDetail, props: true },
    { path: '/results', component: Results }
  ]
})

router.beforeEach(async(to)=>{
 if(to.meta.public)return true
 try{if(!auth.loaded||!auth.user)await loadAuth()}catch{return '/login'}
 if(!auth.user)return '/login'
 if(to.meta.admin&&auth.user.role!=='admin')return '/cockpit'
 if(to.path==='/chat'&&auth.user.role==='viewer')return '/cockpit'
 return true
})
window.addEventListener('auth-expired',()=>router.replace('/login'))
export default router

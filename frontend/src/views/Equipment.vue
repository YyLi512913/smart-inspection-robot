<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getJSON } from '../api'
import { canOperate } from '../auth'
const router=useRouter()
const assets=ref([]),query=ref(''),type=ref(''),error=ref(''),loading=ref(false)
const names={control_cabinet:'控制柜',pressure_gauge:'仪表设备',pipe:'管路设备'}
const visible=computed(()=>assets.value.filter(a=>(!type.value||a.equipment_type===type.value)&&(!query.value||(a.equipment_id+a.name).toLowerCase().includes(query.value.toLowerCase()))))
const pointCount=computed(()=>assets.value.reduce((n,a)=>n+a.observations.length,0))
const checkCount=computed(()=>assets.value.reduce((n,a)=>n+a.observations.reduce((m,p)=>m+p.checks.length,0),0))
async function load(){loading.value=true;try{assets.value=await getJSON('/api/equipment');error.value=''}catch(e){error.value=e.message}finally{loading.value=false}}
function arrange(a){router.push({path:'/cockpit',query:{prompt:a?'请巡检设备 '+a.equipment_id+'（'+a.name+'），先评估续航并说明检查项。':'请巡检生产车间的全部设备，先评估续航并说明设备、观察点与检查项。'}})}
onMounted(load)
</script>
<template>
 <div class="page-heading"><div><div class="eyebrow">EQUIPMENT REGISTRY</div><h1>设备台账</h1><p>以设备为对象，让每次观察、检查与异常都有明确归属。</p></div><el-button v-if="canOperate" type="primary" @click="arrange()">＋ 巡检整个车间</el-button></div>
 <div class="asset-summary"><div><small>在册设备</small><b>{{ assets.length }}</b><span>台</span></div><div><small>观察点</small><b>{{ pointCount }}</b><span>个固定拍摄位置</span></div><div><small>检查项</small><b>{{ checkCount }}</b><span>项可追溯规则</span></div></div>
 <div class="asset-toolbar"><span>生产车间 <small>WORKSHOP</small></span><div><el-input v-model="query" placeholder="搜索设备编号 / 名称" clearable style="width:215px"/><el-select v-model="type" clearable placeholder="全部类型" style="width:135px"><el-option v-for="(n,k) in names" :key="k" :label="n" :value="k"/></el-select><el-button :loading="loading" @click="load">↻</el-button></div></div>
 <el-alert v-if="error" :title="'设备加载失败：'+error" type="warning" :closable="false"/>
 <div class="asset-grid"><article v-for="a in visible" :key="a.equipment_id" class="surface asset-card">
   <div class="asset-illustration"><span class="asset-type">{{ names[a.equipment_type] }}</span>
    <svg viewBox="0 0 240 150" aria-hidden="true"><g v-if="a.equipment_type==='control_cabinet'" stroke="#709276" stroke-width="2"><path d="M80 28h70l17 10v99H80Z" fill="#dce8d7"/><path d="M80 28l17 10h70M97 38v99" fill="none"/><rect x="105" y="52" width="49" height="72" rx="2" fill="#f6f8ef"/><circle cx="142" cy="46" r="3" fill="#afbf9d"/><path d="M145 80v14M115 60h26M115 65h20" stroke="#a1b497"/></g><g v-else-if="a.equipment_type==='pressure_gauge'" stroke="#709276" stroke-width="2"><rect x="69" y="90" width="102" height="38" rx="19" fill="#dce8d7"/><path d="M120 95V77M85 128v9M157 128v9"/><circle cx="120" cy="52" r="32" fill="#f6f8ef"/><path d="M96 52a24 24 0 0 1 48 0M120 52l-11-15" fill="none"/><circle cx="120" cy="52" r="3" fill="#709276"/></g><g v-else stroke="#709276" stroke-width="2"><rect x="57" y="85" width="129" height="24" rx="5" fill="#dce8d7"/><rect x="113" y="80" width="15" height="34" rx="3" fill="#f6f8ef"/><path d="M120 81V51M75 111v24M169 111v24"/><ellipse cx="120" cy="49" rx="23" ry="7" fill="#e8edde"/><path d="M97 49h46M120 42v14"/></g><path d="M46 138h148" stroke="#d4dfcd" stroke-width="2"/></svg>
    <small>结构示意 · 非实时状态</small>
   </div>
   <div class="asset-body"><div class="asset-identity"><span>{{ a.equipment_id }}</span><span class="soft-badge">生产车间</span></div><h2>{{ a.name }}</h2>
    <div v-for="p in a.observations" :key="p.observation_id" class="observation"><div><b>◎ {{ p.name }}</b><span>{{ p.checks.length }} 项检查</span></div><small class="mono">({{ p.x.toFixed(2) }}, {{ p.y.toFixed(2) }}) m · {{ Math.round(p.yaw*180/Math.PI) }}°</small><ul><li v-for="c in p.checks" :key="c.code"><b>{{ c.name }}</b><p>{{ c.rule.replaceAll('unreadable', '需复核') }}</p></li></ul></div>
    <el-button plain type="primary" v-if="canOperate" @click="arrange(a)">安排这台设备的巡检 ↗</el-button>
   </div>
 </article></div>
 <div v-if="!visible.length&&!loading" class="surface empty-state">{{ error?'暂时无法获取设备':'没有符合条件的设备' }}</div>
</template>
<style scoped>
.asset-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:26px}.asset-summary>div{background:white;border:1px solid var(--line);border-radius:9px;padding:18px 21px}.asset-summary small{font-size:11px;color:#85917e;display:block}.asset-summary b{font-size:30px;font-weight:550;color:#34563a;display:inline-block;margin-top:8px;margin-right:9px}.asset-summary span{font-size:10px;color:#98a18c}.asset-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;gap:12px;flex-wrap:wrap}.asset-toolbar>span{font-size:13px;font-weight:550}.asset-toolbar small{font-size:8px;letter-spacing:1px;color:#9ca68e;margin-left:8px}.asset-toolbar>div{display:flex;gap:8px;flex-wrap:wrap}.asset-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;align-items:start}.asset-illustration{height:175px;background:#eff3e9;position:relative;display:flex;justify-content:center;align-items:center}.asset-illustration svg{height:150px;width:240px}.asset-type{position:absolute;left:13px;top:13px;font-size:9px;color:#6b8563;background:#ffffffc9;padding:5px 8px;border-radius:4px}.asset-illustration>small{position:absolute;bottom:9px;right:12px;font-size:8px;color:#a2ad94}.asset-body{padding:19px}.asset-identity{display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#8b9e7d;letter-spacing:.5px}.asset-body h2{font-size:16px;font-weight:550;margin:10px 0 20px;color:#35543a}.observation{border-top:1px solid #eaf0e2;padding-top:14px}.observation>div{display:flex;justify-content:space-between;align-items:center}.observation b{font-size:11px;font-weight:550;color:#69845a}.observation>div>span{font-size:9px;color:#a0ab91}.observation>small{display:block;font-size:9px;color:#9cab8e;margin-top:8px}.observation ul{list-style:none;padding:0;margin:16px 0}.observation li{padding:8px 10px;background:#f8faf4;border-radius:5px;margin:7px 0}.observation li b{font-size:10px}.observation p{font-size:10px;line-height:1.8;margin:5px 0 0;color:#8c9c7d}.asset-body>.el-button{width:100%;margin-top:7px}@media(max-width:1200px){.asset-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:760px){.asset-grid{grid-template-columns:1fr}.asset-summary{gap:8px}.asset-summary>div{padding:13px}.asset-summary b{font-size:24px}.asset-summary span{display:block}}
</style>
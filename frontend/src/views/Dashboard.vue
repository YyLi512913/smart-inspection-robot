<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJSON } from '../api'
import { loadMap, toPx } from '../map'
import { PLACES } from '../places'
import { STATUS_TYPE, STATUS_LABEL, TERMINAL, fmtTime } from '../status'

const map = ref(null), robots = ref([]), tasks = ref([]), task = ref(null), trail = ref([])
const equipment=ref([]), alertCount=ref(null)
const failure = ref(''), mapError = ref(''), updated = ref(null)
const showTrail = ref(true), showPoints = ref(true), zoom = ref(1)
let timer, busy = false, disposed = false, ticks = 0
const names = {tinyRobot1_charger:'充电桩',corridor_west:'西走廊',corridor_mid:'中走廊',corridor_east:'东走廊',workshop:'生产车间',power_room:'配电房',server_room:'服务器机房'}
const robot = computed(() => robots.value.find(r => r.robot_id === 'robot1') || robots.value[0])
const pos = computed(() => robot.value?.online ? robot.value.pos : null)
const battery = computed(() => typeof robot.value?.battery === 'number' ? Math.max(0,Math.min(100,robot.value.battery)) : null)
const active = computed(() => tasks.value.filter(t => !TERMINAL.includes(t.status)))
const attention = computed(() => tasks.value.filter(t => ['BLOCKED','TIMEOUT','FAILED'].includes(t.status)))
const places = Object.entries(PLACES).map(([name,xy]) => {const [x,y]=toPx(...xy);return {name,label:names[name],x,y}})
const equipmentMarkers=computed(()=>equipment.value.flatMap(a=>a.observations.map(p=>({id:a.equipment_id,name:a.name,xy:toPx(p.x,p.y)}))))
const robotPx = computed(() => pos.value ? toPx(pos.value.x,pos.value.y) : null)
const trailPath = computed(() => trail.value.map((p,i)=>(i?'L':'M')+toPx(...p).join(' ')).join(' '))
const points = computed(() => (task.value?.points || []).map(p=>{const [x,y]=toPx(p.x,p.y);return {...p,x,y}}))
const viewBox = computed(() => {const w=2900/zoom.value,h=1800/zoom.value;return [1300-w/2,800-h/2,w,h].join(' ')})
const modes = {idle:'待命',charging:'充电中',waiting:'等待中',navigating:'导航中',inspecting:'巡检中',error:'设备异常'}
async function refresh(force=false) {
 if(busy) return
 busy=true
 try {
  const list=await getJSON('/api/robots')
  if(disposed)return
  robots.value=list;failure.value='';updated.value=new Date()
  if(pos.value){const p=pos.value,last=trail.value.at(-1);if(!last||Math.hypot(p.x-last[0],p.y-last[1])>.05){trail.value.push([p.x,p.y]);if(trail.value.length>400)trail.value.shift()}}
  if(force||ticks++%4===0){
   tasks.value=await getJSON('/api/tasks?limit=100')
   try{alertCount.value=(await getJSON('/api/alerts/summary')).active}catch{alertCount.value=null}
   const cur=robot.value?.current_task
   const no=typeof cur==='string'?cur:cur?.task_id
   const candidate=no?{task_no:no}:tasks.value.find(t=>t.robot_id===robot.value?.robot_id&&!TERMINAL.includes(t.status))
   task.value=candidate?await getJSON('/api/tasks/'+encodeURIComponent(candidate.task_no)):null
  }
 }catch(e){failure.value=e.message;robots.value=[]}finally{busy=false}
}
onMounted(async()=>{try{equipment.value=await getJSON('/api/equipment')}catch{}try{map.value=await loadMap()}catch{mapError.value='地图加载失败，请刷新页面重试'}await refresh(true);if(!disposed)timer=setInterval(refresh,1000)})
onBeforeUnmount(()=>{disposed=true;clearInterval(timer)})
</script>
<template>
 <div class="operations">
  <div class="metrics">
   <div class="metric"><span>在线设备 <i>◉</i></span><strong>{{ failure ? '—' : robots.filter(r=>r.online).length }}<small>/ {{ failure ? '—' : robots.length }}</small></strong><em :class="{warn:failure||!robot?.online}">{{ failure ? '连接服务失败' : robot?.online ? '遥测连接正常' : '等待设备连接' }}</em></div>
   <div class="metric"><span>执行中任务 <i>↗</i></span><strong>{{ updated ? active.length : '—' }}<small>项</small></strong><em>最近 {{ tasks.length }} 条任务记录</em></div>
   <div class="metric"><span>待关注事件 <i>⚑</i></span><strong :class="{warning:attention.length}">{{ updated ? attention.length : '—' }}<small>项</small></strong><em>阻塞、超时与失败任务</em></div>
  </div>
  <router-link to="/alerts" class="alert-entry surface"><span>⚑ 设备异常与待复核</span><b>{{ alertCount ?? '—' }} 项待处理</b><span>查看处理进度 ↗</span></router-link>
  <section class="surface map-panel">
   <div class="panel-heading"><h2><span class="section-dot"></span>实时地图 <small>LIVE MAP</small></h2><div class="map-heading-right"><span class="soft-badge">L1 · 工厂一层</span><button class="text-btn" @click="refresh(true)" aria-label="刷新运行状态">↻</button></div></div>
   <div class="map-stage">
    <div class="map-overlay"><span :class="['live-pill',{offline:!robot?.online}]"><i></i>{{ robot?.online?'实时连接':'设备离线' }}</span><span>26 × 16 m</span></div>
    <svg v-if="map" :viewBox="viewBox" role="img" aria-label="工厂一层巡检地图" class="factory-map">
     <defs><pattern id="map-grid" width="100" height="100" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="2.3" fill="#d4ddd2"/></pattern></defs>
     <rect x="-3000" y="-3000" width="9000" height="9000" fill="url(#map-grid)"/>
     <rect x="0" y="1000" width="2600" height="600" fill="#e9eee4"/>
     <rect x="0" y="0" width="1200" height="1000" fill="#edf0e9"/>
     <rect x="1200" y="0" width="600" height="1000" fill="#e6ede5"/>
     <rect x="1800" y="0" width="800" height="1000" fill="#eaf0e9"/>
     <g fill="#f7f8f3" stroke="#d8e0d4" stroke-width="5"><rect x="100" y="120" width="310" height="180" rx="12"/><rect x="100" y="390" width="310" height="180" rx="12"/><rect x="760" y="120" width="310" height="180" rx="12"/><rect x="760" y="390" width="310" height="180" rx="12"/><rect x="1290" y="120" width="150" height="290" rx="8"/><rect x="1550" y="120" width="150" height="290" rx="8"/><rect v-for="x in [1900,2120,2340]" :key="x" :x="x" y="120" width="130" height="320" rx="8"/></g>
     <g font-size="58" fill="#8b9d80" font-weight="600" text-anchor="middle"><text x="600" y="800">01 / 生产车间</text><text x="1500" y="800">02 / 配电房</text><text x="2200" y="800">03 / 服务器机房</text><text x="1450" y="1430" font-size="45" letter-spacing="9">主 通 道</text></g>
     <line v-for="(w,i) in map.walls" :key="'w'+i" :x1="w[0]" :y1="w[1]" :x2="w[2]" :y2="w[3]" stroke="#8c9e89" stroke-width="15" stroke-linecap="square"/>
     <line v-for="(d,i) in map.doors" :key="'d'+i" :x1="d[0]" :y1="d[1]" :x2="d[2]" :y2="d[3]" stroke="#a9c4a1" stroke-width="9" stroke-dasharray="15 9"/>
     <polyline v-if="points.length>1" :points="points.map(p=>p.x+','+p.y).join(' ')" fill="none" stroke="#b9974e" stroke-width="7" stroke-dasharray="18 14"/>
     <path v-if="showTrail" :d="trailPath" fill="none" stroke="#32876d" stroke-width="9" stroke-linejoin="round"/>
     <g v-if="showPoints"><g v-for="p in places" :key="p.name"><circle :cx="p.x" :cy="p.y" r="17" fill="white" stroke="#8ca183" stroke-width="6"/><circle :cx="p.x" :cy="p.y" r="5" fill="#8ca183"/><text :x="p.x" :y="p.y+83" text-anchor="middle" fill="#62785f" font-size="53">{{ p.label }}</text></g></g>
<g v-if="showPoints"><g v-for="a in equipmentMarkers" :key="a.id" :transform="'translate('+a.xy.join(' ')+')'"><title>{{ a.name }} · 观察点</title><rect x="-20" y="-20" width="40" height="40" rx="8" fill="#f9fbf2" stroke="#6c8d61" stroke-width="5"/><text y="-38" text-anchor="middle" font-size="34" fill="#607e53">{{ a.id }}</text></g></g>
     <g v-for="p in points" :key="'p'+p.seq"><circle :cx="p.x" :cy="p.y" r="27" :fill="p.status==='blocked'?'#cf6557':p.status==='inspected'?'#23856b':'#b9974e'"/><text :x="p.x" :y="p.y+10" text-anchor="middle" fill="white" font-size="28">{{ p.seq }}</text></g>
     <g v-if="robotPx" :transform="'translate('+robotPx.join(' ')+')'"><circle r="69" fill="#23856b" opacity=".10"/><circle r="43" fill="white" stroke="#23856b" stroke-width="6"/><g :transform="'rotate('+(-(pos.yaw||0)*180/Math.PI)+')'"><path d="M26 0L-17 -15L-10 0L-17 15Z" fill="#23856b"/></g><rect x="-65" y="-105" width="130" height="38" rx="10" fill="#214d3d"/><text y="-78" text-anchor="middle" font-size="25" fill="white">{{ robot.robot_id }}</text></g>
    </svg>
    <div v-else class="empty-state">{{ mapError || '正在加载工厂地图…' }}</div>
    <div class="map-controls"><button @click="zoom=Math.min(2.5,zoom+.25)" aria-label="放大地图">+</button><button @click="zoom=Math.max(.75,zoom-.25)" aria-label="缩小地图">−</button><button @click="zoom=1" aria-label="重置地图视图">⌖</button></div>
    <div class="map-legend"><button :class="{dim:!showTrail}" @click="showTrail=!showTrail"><i></i>移动轨迹</button><button :class="{dim:!showPoints}" @click="showPoints=!showPoints"><i class="point"></i>巡检点位</button><span>布局示意</span></div>
   </div>
   <div class="robot-strip"><span class="robot-icon">◈</span><div><b>{{ robot?.robot_id || 'robot1' }}</b><small>{{ robot?.online ? modes[robot.mode] || robot.mode : '暂无在线遥测' }}</small></div><div class="robot-value"><small>电量</small><b>{{ robot?.online && battery!==null ? battery.toFixed(0)+'%' : '—' }}</b></div><div class="robot-value"><small>位置 / m</small><b class="mono">{{ pos ? pos.x.toFixed(1)+', '+pos.y.toFixed(1) : '—' }}</b></div><router-link v-if="task" :to="'/tasks/'+task.task_no" class="task-shortcut">当前任务 ↗</router-link></div>
  </section>
  <section class="surface mission-panel"><div class="panel-heading"><h2>任务动态 <small>RECENT MISSIONS</small></h2><router-link to="/tasks" class="view-all">查看全部 ↗</router-link></div><div v-if="failure" class="empty-state">{{ failure }} · 暂时无法获取最新任务</div><template v-else-if="tasks.length"><router-link v-for="t in tasks.slice(0,2)" :key="t.task_no" :to="'/tasks/'+t.task_no" class="mission-row"><span class="mission-symbol">↗</span><div><b>{{ t.task_no }}</b><small>{{ t.robot_id }} <span>·</span> {{ fmtTime(t.created_at) }}</small></div><el-tag :type="STATUS_TYPE[t.status]">{{ STATUS_LABEL[t.status] || t.status }}</el-tag><span class="row-arrow">↗</span></router-link></template><div v-else class="empty-state">还没有巡检任务，向右侧助手描述你的巡检需求。</div></section>
  <div v-if="attention.length" class="incident-note"><span>⚑ {{ attention.length }} 项任务需要关注</span><router-link to="/tasks">进入任务管理处理 →</router-link></div>
 </div>
</template>
<style scoped>
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px}.metric{padding:17px 18px;background:white;border:1px solid var(--line);border-radius:9px}.metric>span{display:flex;justify-content:space-between;font-size:11px;color:#7f8e81}.metric i{font-style:normal;color:#a5b39c}.metric strong{display:block;font-size:30px;font-weight:550;line-height:1.7;letter-spacing:-1px;color:#264c39}.metric strong small{font-size:12px;letter-spacing:0;font-weight:400;color:#99a38f;margin-left:6px}.metric em{font-size:9px;font-style:normal;color:#91a087}.metric em.warn{color:#b28b58}.metric .warning{color:#b28b58}.map-panel{margin-bottom:18px}.section-dot{display:inline-block;width:7px;height:7px;background:#23856b;border-radius:50%;margin-right:9px}.panel-heading h2 small{margin-left:10px;font-size:8px;letter-spacing:1px}.map-heading-right{display:flex;align-items:center;gap:12px}.text-btn{border:0;background:none;color:#81967e;font-size:20px}.map-stage{height:332px;background:#f4f6ef;position:relative;overflow:hidden}.factory-map{width:100%;height:100%;padding:32px 18px 24px}.map-overlay{position:absolute;top:14px;left:16px;display:flex;gap:13px;align-items:center;color:#97a18d;font-size:9px}.live-pill{display:flex;gap:6px;align-items:center;background:white;border:1px solid #e1e8d9;border-radius:4px;padding:5px 8px;color:#528158}.live-pill i{width:5px;height:5px;background:#5a9161;border-radius:50%}.live-pill.offline{color:#a18a6a}.live-pill.offline i{background:#b59872}.map-controls{position:absolute;right:14px;bottom:42px;display:flex;flex-direction:column;background:white;border:1px solid #dfe5d7;border-radius:6px;box-shadow:0 2px 8px #4e654014}.map-controls button{width:29px;height:28px;background:none;border:0;color:#66815b;font-size:18px}.map-controls button+button{border-top:1px solid #edf0e7}.map-legend{position:absolute;bottom:10px;left:17px;right:17px;display:flex;gap:15px;font-size:9px;color:#8e9b86}.map-legend button{display:flex;gap:5px;align-items:center;border:0;background:none;color:#6e8264;font-size:9px;padding:0}.map-legend i{width:13px;height:3px;background:#32876d;display:inline-block}.map-legend .point{width:6px;height:6px;border-radius:50%;background:#9aab8d}.map-legend .dim{opacity:.4}.map-legend>span{margin-left:auto}.robot-strip{display:flex;align-items:center;gap:11px;padding:15px 18px;border-top:1px solid #e6ebdf}.robot-icon{background:#edf3e8;border-radius:8px;padding:8px 10px;color:#587e47;font-size:21px}.robot-strip b{font-size:11px;font-weight:550}.robot-strip small{font-size:9px;display:block;margin-top:4px;color:#8f9c87}.robot-value{margin-left:auto}.robot-value small{margin:0 0 5px}.task-shortcut{font-size:10px;margin-left:auto}.mission-row{display:flex;gap:12px;align-items:center;padding:15px 19px;color:#334c3a;border-bottom:1px solid #eef1eb}.mission-row:last-child{border:0}.mission-row:hover{background:#f8faf5}.mission-symbol{width:30px;height:30px;display:grid;place-items:center;background:#f0f4eb;color:#7b956b;border-radius:6px}.mission-row b{font-size:11px;font-weight:550}.mission-row small{display:block;margin-top:6px;color:#929d8b;font-size:9px}.mission-row small span{padding:0 5px}.mission-row .el-tag{margin-left:auto}.row-arrow{color:#91a087;font-size:12px}.view-all{font-size:10px;color:#7c9372}.incident-note{margin-top:12px;padding:10px 14px;background:#fbf2df;border-radius:7px;font-size:10px;display:flex;justify-content:space-between;color:#a18143}@media(max-width:500px){.metrics{gap:6px}.metric{padding:12px 8px}.metric strong{font-size:25px}.metric em{font-size:8px}.robot-strip{gap:7px;padding:12px 10px;flex-wrap:wrap}.map-stage{height:280px}.panel-heading h2 small{display:none}}
</style>

<style scoped>.alert-entry{display:flex;justify-content:space-between;gap:15px;align-items:center;padding:15px 20px;margin-bottom:18px;color:#6e825f;font-size:12px}.alert-entry b{color:#a27850;font-weight:550}.alert-entry span:last-child{font-size:10px}</style>

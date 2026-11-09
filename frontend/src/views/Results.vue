<script setup>
import { ElMessage } from 'element-plus'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getJSON } from '../api'
import { fmtTime } from '../status'

const rows = ref([])
const error = ref('')
const names = {workshop:'生产车间', power_room:'配电房', server_room:'服务器机房',tinyRobot1_charger:'充电桩',corridor_west:'西走廊',corridor_mid:'中走廊',corridor_east:'东走廊'}
const taskNo = ref('')
const equipmentId=ref('')
const equipment=ref([])
let timer = null

async function load() {
  const params = new URLSearchParams({limit:'500'})
  if(taskNo.value)params.set('task_no',taskNo.value)
  if(equipmentId.value)params.set('equipment_id',equipmentId.value)
  const q='?'+params.toString()
  try { rows.value = await getJSON(`/api/results${q}`); error.value = '' }
  catch (e) { error.value = e.message }
}
onMounted(() => { load(); getJSON('/api/equipment').then(r=>equipment.value=r).catch(()=>{}); timer = setInterval(load, 10000) })
onBeforeUnmount(() => clearInterval(timer))

// 前端导出 CSV（Excel 可直接打开；含 VLM 结论）
function vlmText(r) {
  if (r.normal === 1) return '正常' + (r.reading ? `（${r.reading}）` : '')
  if (r.normal === 0) return `异常：${r.anomaly || r.reading}`
  return r.error ? `检测失败：${r.error}` : r.review_required ? '需要人工复核' : analysisText(r)
}
function analysisText(r) {
  if(r.analysis_status==='retrying')return '等待自动重试'
  if(r.analysis_status==='processing')return '正在分析'
  if(r.analysis_status==='pending')return '等待分析'
  return '待检测'
}
function exportCsv() {
  if (!rows.value.length) { ElMessage.warning('无数据'); return }
  const escape=(v)=>'"'+String(v??'').replaceAll('"','""')+'"'
  const head=['任务号','机器人','设备编号','设备名称','观察点','点位序号','拍照时间','图片','VLM结论','逐项检查']
  const lines=[head.map(escape).join(',')]
  for(const r of rows.value)lines.push([r.task_no,r.robot_id,r.equipment_id,r.equipment_name,r.observation_name,r.point_seq,r.captured_at?fmtTime(r.captured_at):'',r.image_url,vlmText(r),JSON.stringify(r.checks||[])].map(escape).join(','))
  const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `巡检结果_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
 <div class="page-heading"><div><div class="eyebrow">INSPECTION RECORDS</div><h1>检测记录</h1><p>现场影像与视觉检测结论，让巡检结果有据可查。</p></div><el-button type="primary" @click="exportCsv">↓ 导出记录 CSV</el-button></div>
 <div class="results-overview"><span>最近 {{ rows.length }} 条记录</span><span><i class="normal"></i>{{ rows.filter(r=>r.normal===1).length }} 正常</span><span><i class="abnormal"></i>{{ rows.filter(r=>r.normal===0).length }} 异常</span><span><i></i>{{ rows.filter(r=>r.normal==null).length }} 待检测 / 失败</span></div>
 <div class="result-toolbar"><div><h2>巡检影像</h2><small>采集图片与检测结果 · 每 10 秒更新</small></div><div class="result-filters"><el-select v-model="equipmentId" placeholder="全部设备" clearable style="width:170px" @change="load"><el-option v-for="a in equipment" :key="a.equipment_id" :value="a.equipment_id" :label="a.equipment_id+' · '+a.name"/></el-select><el-input v-model="taskNo" placeholder="按任务号筛选" clearable style="width:240px" @keyup.enter="load" @clear="load"/><el-button @click="load">查询</el-button></div></div>
 <el-alert v-if="error" :title="'结果加载失败：'+error" type="warning" :closable="false"/>
 <div v-if="rows.length" class="result-grid"><article v-for="(row,i) in rows" :key="row.task_no+'-'+row.point_seq+'-'+i" class="surface result-card"><div class="result-image"><el-image v-if="row.image_url" :src="row.image_url" :preview-src-list="[row.image_url]" preview-teleported fit="cover"><template #error><div class="image-empty">图片暂不可用</div></template></el-image><div v-else class="image-empty">暂无采集图片</div><span :class="['verdict',{abnormal:row.normal===0,pending:row.normal==null}]">{{ row.normal===1?'● 检测正常':row.normal===0?'● 发现异常':row.capture_error?'● 采集失败':row.error?'● 检测失败':row.review_required?'● 需要复核':'◌ '+analysisText(row) }}</span><span class="source-badge">{{ row.image_source === 'camera' ? '实时相机' : '历史采集' }}</span><span class="point-badge">POINT {{ String(row.point_seq).padStart(2,'0') }}</span></div><div class="result-body"><div class="result-title"><h3>{{ row.equipment_name || names[row.place] || row.place }}</h3><span>↗</span></div><router-link :to="'/tasks/'+row.task_no" class="result-task">{{ row.task_no }}</router-link><small v-if="row.equipment_id" class="equipment-caption">{{ row.equipment_id }} · {{ row.observation_name }}</small><div v-if="row.checks?.length" class="check-results"><div v-for="c in row.checks" :key="c.code"><span :class="c.status">{{ c.status==='normal'?'● 正常':c.status==='abnormal'?'● 异常':'◌ 需复核' }}</span><b>{{ c.name }}</b><small>{{ c.evidence }}</small></div></div><p :class="{danger:row.normal===0}">{{ row.error || row.anomaly || row.reading || '图片已上报，等待视觉分析结果。' }}</p><div v-if="row.analysis_status" class="analysis-progress"><span>{{ row.analysis_status==='succeeded'?'分析已完成':row.analysis_status==='failed'?'自动分析已停止':analysisText(row) }}</span><small>已尝试 {{ row.analysis_attempts }} / {{ row.analysis_max_attempts }} 次</small><p v-if="row.analysis_status==='retrying'">暂时无法完成分析，将自动重试。</p><p v-if="row.analysis_status==='failed'">请人工复核或重新安排巡检。</p></div><router-link v-if="row.alert_id" :to="'/alerts?alert='+row.alert_id" class="equipment-caption">查看告警与处理进度 ↗</router-link><div class="result-meta"><span>◈ {{ row.robot_id }}</span><time>{{ fmtTime(row.captured_at || row.created_at) }}</time></div></div></article></div>
 <div v-else class="surface empty-state"> {{ error ? '暂时无法获取检测记录' : '暂无检测记录，完成一次巡检后，图片与检测结论将在这里展示。' }} </div>
</template>
<style scoped>
.results-overview{display:flex;gap:24px;align-items:center;padding:16px 20px;border:1px solid var(--line);background:white;border-radius:8px;font-size:11px;color:#7c8d74;margin-bottom:25px;flex-wrap:wrap}.results-overview>span:first-child{color:#385b3d;font-weight:550;margin-right:auto}.results-overview i{display:inline-block;width:6px;height:6px;border-radius:50%;background:#b6bfa9;margin-right:7px}.results-overview .normal{background:#488361}.results-overview .abnormal{background:#cb7c62}.result-toolbar{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:18px;flex-wrap:wrap}.result-toolbar h2{font-size:14px;font-weight:550;margin:0 0 6px}.result-toolbar small{font-size:10px;color:#97a18d}.result-filters{display:flex;gap:8px}.result-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}.result-image{height:190px;position:relative;background:#edf1e8}.result-image .el-image{height:100%;width:100%;display:block}.result-image :deep(img){transition:transform .3s}.result-image:hover :deep(img){transform:scale(1.04)}.image-empty{display:grid;place-items:center;height:190px;width:100%;color:#9ca78d;font-size:12px}.verdict{position:absolute;left:12px;top:12px;border-radius:5px;padding:6px 9px;font-size:10px;background:#f1f9ed;color:#427c42;box-shadow:0 2px 8px #00000010}.verdict.abnormal{background:#fff0e5;color:#bc6549}.verdict.pending{background:#f6f3e6;color:#9b8b55}.point-badge{position:absolute;bottom:12px;right:12px;color:white;background:#203b2db3;font-size:8px;letter-spacing:1px;padding:5px 7px;border-radius:3px}.result-body{padding:17px}.result-title{display:flex;justify-content:space-between;color:#94a084}.result-title h3{font-size:14px;color:#365332;margin:0 0 6px;font-weight:550}.result-task{font-size:10px;color:#8e9d80}.result-body p{font-size:11px;line-height:1.8;color:#849578;min-height:40px;margin:15px 0}.result-body p.danger{color:#b87354}.result-meta{display:flex;justify-content:space-between;font-size:9px;color:#a1ab94;border-top:1px solid #edf1e7;padding-top:13px;gap:5px}.result-meta time{font-size:8px}@media(max-width:1200px){.result-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:700px){.result-grid{grid-template-columns:1fr}.results-overview{gap:12px}.result-filters{width:100%}}
</style>
<style scoped>.source-badge{position:absolute;bottom:12px;left:12px;color:white;background:#203b2db3;font-size:9px;padding:5px 7px;border-radius:3px}</style>

<style scoped>.equipment-caption{display:block;font-size:9px;color:#93a182;margin-top:8px}.check-results{margin-top:14px;display:grid;gap:7px}.check-results>div{background:#f6f8f0;padding:8px;border-radius:4px}.check-results span{font-size:8px;margin-right:7px;color:#8d9a7b}.check-results .normal{color:#38845e}.check-results .abnormal{color:#c06b50}.check-results b{font-size:9px;font-weight:550;color:#6a805a}.check-results small{display:block;font-size:9px;line-height:1.7;color:#92a082;margin-top:4px}.result-filters{flex-wrap:wrap}</style>

<style scoped>.analysis-progress{display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;border-top:1px solid #edf1e7;padding:10px 0;font-size:10px;color:#809271}.analysis-progress small{font-size:9px;color:#a0aa94}.analysis-progress p{flex-basis:100%;min-height:0;margin:0;font-size:10px}</style>

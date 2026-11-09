<script setup>
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
import { canOperate } from '../auth'
import { POINT_COLOR, STATUS_TYPE, STATUS_LABEL, TERMINAL, fmtTime } from '../status'

const props = defineProps({ taskNo: String })
const task = ref(null)
const busy = ref(false)
let timer = null

const isTerminal = computed(() => task.value && TERMINAL.includes(task.value.status))
const blocked = computed(() => task.value && task.value.status === 'BLOCKED')

async function load() {
  try { task.value = await getJSON(`/api/tasks/${props.taskNo}`) }
  catch (e) { ElMessage.error(e.message) }
}
onMounted(() => { load(); timer = setInterval(load, 3000) })
onBeforeUnmount(() => clearInterval(timer))

async function cancel() {
  await ElMessageBox.confirm(`确认取消任务 ${props.taskNo}？机器人将中止导航。`, '取消任务')
  try { task.value = await postJSON(`/api/tasks/${props.taskNo}/cancel`); ElMessage.success('已取消') }
  catch (e) { ElMessage.error(e.message) }
}

async function decide(action) {
  await ElMessageBox.confirm(
    action === 'retry' ? '重试：机器人重新导航到阻塞点。' : '跳过：跳过阻塞点继续后续点位。',
    `决策 ${action}`)
  try { task.value = await postJSON(`/api/tasks/${props.taskNo}/decision`, { action })
        ElMessage.success(`已${action === 'retry' ? '重试' : '跳过'}`) }
  catch (e) { ElMessage.error(e.message) }
}

// 时间线事件着色
function evType(ev) {
  if (ev.includes('fail') || ev.includes('timeout') || ev.includes('blocked') || ev.includes('stuck')) return 'danger'
  if (ev === 'done' || ev.includes('task_done')) return 'success'
  return ''
}
</script>

<template>
  <div class="page-heading"><div><div class="eyebrow">MISSION DETAIL</div><h1>任务详情</h1><p>巡检点位、执行状态与事件审计。</p></div></div>
  <div v-if="task">
    <div class="bar">
      <el-button @click="$router.push('/tasks')">← 返回列表</el-button>
      <h2 class="title">{{ task.task_no }}</h2>
      <el-tag :type="STATUS_TYPE[task.status]">{{ STATUS_LABEL[task.status] || task.status }}</el-tag>
      <div class="spacer"></div>
      <template v-if="canOperate && !isTerminal">
        <el-button type="danger" plain @click="cancel" :disabled="busy">取消任务</el-button>
      </template>
      <template v-if="canOperate && blocked">
        <el-button type="primary" @click="decide('retry')" :disabled="busy">重试</el-button>
        <el-button type="warning" @click="decide('skip')" :disabled="busy">跳过此点</el-button>
      </template>
    </div>

    <el-alert v-if="task.execution_held && ['CANCELED','TIMEOUT'].includes(task.status)" title="正在等待执行节点确认撤销；确认前机器人仍被该任务占用，不能创建新任务。" type="warning" :closable="false" show-icon style="margin-bottom:16px" />
    <el-alert v-if="task.events.some(e => e.event === 'recovery_required') && task.status === 'BLOCKED'" title="执行节点重启或导航响应不确定，需要恢复核对。请先取消任务；无法确认撤销时不会自动释放机器人。" type="warning" :closable="false" show-icon style="margin-bottom:16px" />
    <section v-if="task.delivery?.length" class="surface" style="margin-bottom:16px">
      <div class="panel-heading"><h2>指令交付</h2><small>接单确认与重发记录</small></div>
      <el-table :data="task.delivery" size="small">
        <el-table-column label="操作" width="100"><template #default="{row}">{{ row.action === 'patrol' ? '执行巡检' : '撤销执行' }}</template></el-table-column>
        <el-table-column label="交付状态" width="140"><template #default="{row}"><el-tag :type="row.status === 'acked' ? 'success' : 'info'">{{ row.status === 'acked' ? '执行节点已确认' : row.status === 'superseded' ? '已被后续指令替代' : '等待执行节点确认' }}</el-tag></template></el-table-column>
        <el-table-column prop="attempts" label="发送次数" width="100" />
        <el-table-column label="最近发送" min-width="160"><template #default="{row}">{{ fmtTime(row.last_sent_at) }}</template></el-table-column>
        <el-table-column label="确认时间" min-width="160"><template #default="{row}">{{ fmtTime(row.acked_at) }}</template></el-table-column>
      </el-table>
    </section>
    <el-descriptions :column="4" border class="desc">
      <el-descriptions-item label="机器人">{{ task.robot_id }}</el-descriptions-item>
      <el-descriptions-item label="来源">{{ task.source }}</el-descriptions-item>
      <el-descriptions-item label="优先级">{{ task.priority }}</el-descriptions-item>
      <el-descriptions-item label="创建人">{{ task.created_by || '-' }}</el-descriptions-item>
      <el-descriptions-item label="创建">{{ fmtTime(task.created_at) }}</el-descriptions-item>
      <el-descriptions-item label="下发">{{ fmtTime(task.dispatched_at) }}</el-descriptions-item>
      <el-descriptions-item label="开始">{{ fmtTime(task.started_at) }}</el-descriptions-item>
      <el-descriptions-item label="结束">{{ fmtTime(task.finished_at) }}</el-descriptions-item>
    </el-descriptions>

    <div class="cols">
      <el-card shadow="never">
        <template #header>点位（{{ task.points.length }}）</template>
        <el-table :data="task.points" size="small">
          <el-table-column prop="seq" label="#" width="46" />
          <el-table-column label="设备 / 观察点" min-width="170"><template #default="{row}"><b>{{ row.inspection?.equipment_name || row.place }}</b><small v-if="row.inspection" style="display:block;color:#8a9c7c;margin-top:5px">{{ row.inspection.equipment_id }} · {{ row.inspection.observation_name }}</small></template></el-table-column>
          <el-table-column label="坐标" width="130">
            <template #default="{ row }">({{ row.x }}, {{ row.y }})</template>
          </el-table-column>
          <el-table-column prop="check_item" label="检查项" min-width="120" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <span class="dot" :style="{ background: POINT_COLOR[row.status] }"></span>
              {{ row.status }}
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never">
        <template #header>事件时间线（{{ task.events.length }}）</template>
        <el-timeline class="tl">
          <el-timeline-item v-for="(e, i) in task.events" :key="i"
                            :timestamp="fmtTime(e.created_at)" :type="evType(e.event)" placement="top">
            <b>{{ e.event }}</b>
            <span v-if="e.point_seq" class="seq">点 {{ e.point_seq }}</span>
            <pre v-if="e.detail" class="detail">{{ JSON.stringify(e.detail, null, 1) }}</pre>
          </el-timeline-item>
        </el-timeline>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.title { margin: 0; font-size: 18px; }
.spacer { flex: 1; }
.desc { margin-bottom: 12px; }
.cols { display: flex; gap: 12px; align-items: flex-start; }
.cols > .el-card { flex: 1; min-width: 0; }
.tl { max-height: 60vh; overflow: auto; padding-top: 6px; }
.seq { color: #909399; margin-left: 8px; }
.detail { margin: 6px 0 0; font-size: 12px; color: #738465; background: #f4f7ef; padding: 6px; border-radius: 4px; white-space: pre-wrap; word-break: break-all; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
</style>

<style scoped>@media(max-width:950px){.cols{flex-direction:column}.cols>.el-card{width:100%}}.bar{flex-wrap:wrap}.detail{border:1px solid #e6ecdf}</style>

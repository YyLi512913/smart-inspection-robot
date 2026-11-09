// 任务状态 → Element tag type 映射（设计文档 4.3 状态机）
export const STATUS_TYPE = {
  CREATED: 'info',
  DEFERRED: 'warning',
  DISPATCHED: 'primary',
  ACCEPTED: 'primary',
  NAVIGATING: 'primary',
  INSPECTING: 'primary',
  REPORTING: 'primary',
  BLOCKED: 'danger',
  DONE: 'success',
  CANCELED: 'info',
  TIMEOUT: 'warning',
  FAILED: 'danger'
}

export const TERMINAL = ['DONE', 'CANCELED', 'TIMEOUT', 'FAILED']

// 点位状态 → 颜色（大屏 SVG 用）
export const POINT_COLOR = {
  pending: '#909399',
  arrived: '#e6a23c',
  inspected: '#67c23a',
  blocked: '#f56c6c',
  skipped: '#f7ba2a'
}

export function fmtTime(s) {
  if (!s) return '-'
  return new Date(s).toLocaleString('zh-CN', { hour12: false })
}

export const STATUS_LABEL = { CREATED: '待调度', DEFERRED: '等待充电', DISPATCHED: '已下发', ACCEPTED: '已接单', NAVIGATING: '导航中', INSPECTING: '巡检中', REPORTING: '上报中', BLOCKED: '等待处理', DONE: '已完成', CANCELED: '已取消', TIMEOUT: '已超时', FAILED: '执行失败' }

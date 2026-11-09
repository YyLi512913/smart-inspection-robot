<script setup>
import { nextTick, ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { auth,canOperate,forget } from '../auth'

// 消息块类型：user / assistant / process(执行过程) / ask(ask_user 确认卡)
const blocks = ref([])
const input = ref('')
const route = useRoute()
onMounted(() => { if (typeof route.query.prompt === 'string') input.value = route.query.prompt })
const sessionId = ref('')
const streaming = ref(false)
const scroller = ref(null)
let controller = null

function scrollBottom() {
  nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}

function fmt(v) {
  try { return JSON.stringify(v) } catch { return String(v) }
}

async function send(text) {
  const msg = (text ?? input.value).trim()
  if (!msg || streaming.value || !canOperate.value) return
  blocks.value.push({ kind: 'user', text: msg })
  if (text === undefined) input.value = ''
  scrollBottom()
  await streamChat(msg)
}

async function streamChat(msg) {
  streaming.value = true
  controller = new AbortController()
  let proc = null

  function handleEvent(chunk) {
    let ev = 'message'
    let data = '{}'
    for (const line of chunk.split('\n')) {
      if (line.startsWith('event:')) ev = line.slice(6).trim()
      else if (line.startsWith('data:')) data = line.slice(5).trim()
    }
    let d
    try { d = JSON.parse(data) } catch { return }
    if (ev === 'session') { sessionId.value = d.session_id; return }
    if (ev === 'message') {
      proc = null
      blocks.value.push({ kind: 'assistant', text: d.text })
      scrollBottom(); return
    }
    if (ev === 'ask_user') {
      proc = null
      blocks.value.push({ kind: 'ask', question: d.question, options: d.options, answered: false })
      scrollBottom(); return
    }
    // reasoning / tool_call / tool_result 归入执行过程块
    if (!proc) {
      proc = { kind: 'process', steps: [], open: false }
      blocks.value.push(proc)
    }
    proc.steps.push({ ev, ...d })
    scrollBottom()
  }

  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token':auth.csrf },
      body: JSON.stringify({ session_id: sessionId.value, message: msg }),
      signal: controller.signal
    })
    if(r.status===401){forget();window.dispatchEvent(new Event('auth-expired'))}
    if (!r.ok){const data=await r.json();throw new Error(data.detail||'请求失败')}
    const reader = r.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        handleEvent(chunk)
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      blocks.value.push({ kind: 'assistant', text: '（已停止本次执行）' })
    } else {
      ElMessage.error(e.message)
      blocks.value.push({ kind: 'assistant', text: `请求失败：${e.message}` })
    }
  } finally {
    streaming.value = false
    controller = null
    scrollBottom()
  }
}

function answer(ask, opt) {
  if (ask.answered || streaming.value) return
  ask.answered = true
  ask.chosen = opt
  send(opt)
}

function stop() {
  if (controller) controller.abort()
}

function reset() {
  if (streaming.value) return
  blocks.value = []
  sessionId.value = ''
}
</script>

<template>
  <div v-if="canOperate" class="chat">
    <div class="head">
      <span class="assistant-mark">✳</span><div><span class="title">巡检助手</span><small class="assistant-sub">你的巡检协作伙伴</small></div>
      <span class="sid" v-if="sessionId">{{ sessionId.slice(0, 6) }}</span>
      <el-button size="small" :disabled="streaming" @click="reset">＋ 新会话</el-button>
    </div>

    <div class="msgs" ref="scroller">
<div v-if="!blocks.length" class="hint"><div class="welcome-symbol">✳</div><span class="welcome-label">YOUR INSPECTION COPILOT</span><h3>今天，想巡检哪里？</h3><p>用自然语言安排巡检、查询进度，<br>或一起处理执行中的异常。</p><div class="suggestions"><button @click="send('帮我评估去配电房和服务器机房巡检的续航')"><span>↗</span> 评估一次工厂巡检 <b>↗</b></button><button @click="send('查询机器人当前状态')"><span>◉</span> 机器人现在怎么样？ <b>↗</b></button><button @click="send('查看最近巡检结果及异常')"><span>▤</span> 查看最近巡检结果 <b>↗</b></button></div><div class="assistant-tip">任务执行前将评估电量与可达性</div></div>
      <template v-for="(b, i) in blocks" :key="i">
        <div v-if="b.kind === 'user'" class="row user">
          <div class="bubble">{{ b.text }}</div>
        </div>
        <div v-else-if="b.kind === 'assistant'" class="row bot">
          <div class="bubble">{{ b.text }}</div>
        </div>
        <div v-else-if="b.kind === 'process'" class="proc">
          <div class="proc-head" role="button" tabindex="0"
               @click="b.open = !b.open" @keydown.enter="b.open = !b.open">
            执行过程（{{ b.steps.length }} 步）{{ b.open ? '收起' : '展开' }}
          </div>
          <div v-if="b.open" class="proc-body">
            <div v-for="(s, j) in b.steps" :key="j" class="step">
              <template v-if="s.ev === 'reasoning'">
                <span class="tag reasoning">思考</span><span class="txt">{{ s.text }}</span>
              </template>
              <template v-else-if="s.ev === 'tool_call'">
                <span class="tag call">调用</span><span class="txt">{{ s.tool }} <span class="args">({{ fmt(s.args) }})</span></span>
              </template>
              <template v-else-if="s.ev === 'tool_result'">
                <span class="tag" :class="s.ok ? 'res-ok' : 'res-err'">{{ s.ok ? '结果' : '失败' }}</span>
                <span class="txt">{{ s.summary }}</span>
                <div v-if="s.detail" class="detail">{{ fmt(s.detail) }}</div>
              </template>
            </div>
          </div>
        </div>
        <div v-else-if="b.kind === 'ask'" class="ask" :class="{ done: b.answered }">
          <div class="ask-q">{{ b.question }}</div>
          <div class="ask-opts">
            <el-button
              v-for="o in b.options" :key="o" size="small"
              :type="o === '确认执行' ? 'danger' : 'default'"
              :disabled="b.answered" @click="answer(b, o)"
            >{{ o }}</el-button>
          </div>
          <div v-if="b.answered" class="ask-done">已选择：{{ b.chosen }}</div>
        </div>
      </template>
    </div>

    <div class="inputbar">
      <el-input
        v-model="input" type="textarea" :rows="2" resize="none"
        placeholder="描述巡检需求，或询问任务进度…"
        @keydown.enter.exact.prevent="send()"
      />
      <div class="btns">
        <el-button v-if="streaming" type="warning" @click="stop">停止</el-button>
        <el-button v-else type="primary" @click="send()">发送 ↗</el-button>
      </div>
    </div><div class="input-note">Enter 发送 · Shift + Enter 换行</div>
  </div>
</template>

<style scoped>
.chat{height:100%;display:flex;flex-direction:column;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}.head{display:flex;align-items:center;gap:9px;padding:17px 15px;border-bottom:1px solid #edf0ec;flex-wrap:wrap}.assistant-mark{display:grid;place-items:center;width:30px;height:32px;border-radius:7px;background:#edf3e6;color:#6b8a51;font-size:23px}.title{font-size:13px;font-weight:600;color:#284332}.assistant-sub{display:block;font-size:9px;color:#98a18d;margin-top:4px}.sid{display:none}.head .el-button{margin-left:auto;font-size:10px;padding:7px 8px;height:27px;background:#f7f9f4}.msgs{flex:1;min-height:0;overflow-y:auto;padding:17px 14px}.hint{text-align:center;padding-top:44px}.welcome-symbol{height:51px;width:51px;display:grid;place-items:center;border-radius:15px;background:#eaf1df;font-size:36px;color:#6d8a4b;margin:0 auto 22px;transform:rotate(-8deg)}.welcome-label{font-size:8px;letter-spacing:1.6px;color:#94a285}.hint h3{font-size:20px;color:#294733;font-weight:550;letter-spacing:-.6px;margin:13px 0}.hint p{font-size:11px;line-height:1.9;color:#8a9987}.suggestions{display:grid;gap:9px;margin:29px 0 18px}.suggestions button{display:flex;align-items:center;gap:9px;text-align:left;border:1px solid #e7ecdf;background:#fafbf7;border-radius:7px;padding:13px 11px;font-size:11px;color:#536c48;transition:background .15s}.suggestions button:hover{background:#edf4e6;border-color:#bbd1a8}.suggestions button>span{color:#89a071;font-size:14px}.suggestions b{margin-left:auto;font-size:12px;font-weight:400;color:#a1af93}.assistant-tip{font-size:9px;color:#a0ab96}.row{display:flex;margin-bottom:14px}.row.user{justify-content:flex-end}.bubble{max-width:94%;padding:11px 12px;border-radius:9px;font-size:12px;line-height:1.8;white-space:pre-wrap;word-break:break-word}.user .bubble{background:#eaf2e3;color:#36552d;border-bottom-right-radius:2px}.bot .bubble{background:#f5f7f2;color:#52654c;border-bottom-left-radius:2px}.proc{margin:0 0 12px;border:1px solid #e3eadd;border-radius:7px;background:#fbfcf9}.proc-head{padding:9px 10px;font-size:10px;color:#8b9c7b;cursor:pointer}.proc-body{border-top:1px dashed #e0e6d8;padding:9px}.step{font-size:10px;line-height:1.8;margin-bottom:7px;color:#637b54}.tag{display:inline-block;padding:0 5px;border-radius:3px;font-size:9px;margin-right:5px;background:#ebefdf;color:#7b8b5b}.tag.res-err{color:#b85e49;background:#f8e9df}.args{color:#8c9b7f;word-break:break-all}.detail{margin-top:4px;padding:6px;background:#f0f3ea;border-radius:4px;color:#80916e;max-height:130px;overflow:auto;word-break:break-all}.ask{margin:0 0 12px;padding:13px;border:1px solid #e9d8b6;background:#fffbef;border-radius:7px}.ask.done{opacity:.65}.ask-q{font-size:12px;color:#91713c;line-height:1.8;margin-bottom:10px}.ask-opts{display:flex;gap:8px}.ask-done{font-size:10px;margin-top:8px;color:#a18a5d}.inputbar{display:flex;gap:8px;align-items:flex-end;margin:0 14px;border:1px solid #dfe7d7;border-radius:8px;padding:9px;background:#fafcf7}.inputbar :deep(.el-textarea){flex:1}.inputbar :deep(.el-textarea__inner){box-shadow:none;background:transparent;font-size:11px;padding:3px;min-height:43px!important;color:#4d6441}.inputbar :deep(.el-textarea__inner::placeholder){color:#9aa78d}.btns{flex:none}.btns .el-button{padding:8px 10px;height:30px;font-size:10px}.input-note{padding:9px 0 13px;text-align:center;font-size:8px;color:#a7b09b}
</style>
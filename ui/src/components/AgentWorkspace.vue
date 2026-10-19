<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import ChatComposer from './ChatComposer.vue'
import CsvAnalyzer from './CsvAnalyzer.vue'
import SalesDashboard from './SalesDashboard.vue'
import ChatMessage from './ChatMessage.vue'
import SessionSidebar from './SessionSidebar.vue'
import SkillManager from './SkillManager.vue'
import UiIcon from './UiIcon.vue'
import { useAgentStore } from '../stores/agent'
import { dashboardApi } from '../api/client.js'
const agent = useAgentStore()
const messageList = ref(null)
const composer = ref(null)
const showSkills = ref(false)
const showCsv = ref(false)
const activeView = ref('sales')
const salesReport = ref(null)
const reportLoading = ref(false)
const reportError = ref('')
let reportRequest = 0
const sidebarOpen = ref(false)
const stickToBottom = ref(true)
const statusLabels = { finished: '已完成', running: '执行中', error: '执行失败', pending: '待执行', interrupted: '已中断' }
const status = computed(() => agent.streaming ? '执行中' : agent.loadingHistory ? '读取历史' : statusLabels[agent.currentThread?.status] || '准备就绪')
const suggestions = [
  { icon: 'search', title: '了解一个项目', description: '梳理结构、技术栈与启动方式', prompt: '请分析当前仓库的目录结构、技术栈和启动测试方式。' },
  { icon: 'code', title: '实现一个想法', description: '从技术方案到代码与验证', prompt: '我想在当前仓库新增一个功能，请先帮我分析并制定实施方案。需求是：' },
  { icon: 'shield', title: '检查代码质量', description: '审查变更，找到真实问题', prompt: '请审查当前分支的代码变更，说明实际风险、定位依据和测试建议。' },
]
function openSkills() { sidebarOpen.value = false; showSkills.value = true }
function analyzeCsvInChat(prompt) { showCsv.value = false; activeView.value = 'chat'; nextTick(() => composer.value?.setDraft(prompt)) }
function selectThread(id) { activeView.value = 'chat'; sidebarOpen.value = false; stickToBottom.value = true; agent.selectThread(id) }
function newThread() { activeView.value = 'chat'; sidebarOpen.value = false; agent.createThread(); nextTick(() => { if (messageList.value) messageList.value.scrollTop = 0 }) }
function acceptReport(report) { reportRequest++; salesReport.value = report; reportLoading.value = false; reportError.value = ''; showCsv.value = false; activeView.value = 'sales' }
async function loadLatestReport() {
  const request = ++reportRequest
  reportLoading.value = true; reportError.value = ''
  try { const result = await dashboardApi.latestCsvReport(); if (request === reportRequest) salesReport.value = result.report }
  catch (error) { if (request === reportRequest) reportError.value = error.response?.data?.detail || error.message || '读取销售报表失败' }
  finally { if (request === reportRequest) reportLoading.value = false }
}
async function filterReport(range) {
  if (!salesReport.value?.analysis_options || reportLoading.value) return
  const request = ++reportRequest
  reportLoading.value = true; reportError.value = ''
  try { const result = await dashboardApi.analyzeCsv({ ...salesReport.value.analysis_options, ...range }); if (request === reportRequest) acceptReport(result) }
  catch (error) { if (request === reportRequest) reportError.value = error.response?.data?.detail || error.message || '更新统计失败' }
  finally { if (request === reportRequest) reportLoading.value = false }
}
function observeScroll() { const el = messageList.value; if (el) stickToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 120 }
async function scrollToBottom() { await nextTick(); if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight; stickToBottom.value = true }
watch(() => { const last = agent.messages.at(-1); return [agent.currentThreadId, agent.messages.length, last?.id, last?.chunks?.map((chunk) => chunk.kind === 'tool' ? chunk.tool?.status : chunk.kind === 'todo' ? JSON.stringify(chunk.todos) : chunk.text?.length).join('|')] }, () => { if (agent.messages.length && stickToBottom.value) scrollToBottom() })
watch(() => agent.streaming, (running, previous) => { if (previous && !running && !reportLoading.value) loadLatestReport() })
onMounted(() => { agent.bootstrap(); loadLatestReport() })
</script>
<template>
  <main class="app-shell" @keydown.esc="sidebarOpen = false">
    <button v-if="sidebarOpen" class="sidebar-scrim" aria-label="关闭会话导航" @click="sidebarOpen = false"></button>
    <SessionSidebar :threads="agent.threads" :active-id="agent.currentThreadId" :disabled="agent.streaming" :mobile-open="sidebarOpen" @new-thread="newThread" @select-thread="selectThread" @delete-thread="agent.deleteThread" @manage-skills="openSkills" @refresh="agent.refreshThreads" @close="sidebarOpen = false" />
    <section class="workspace">
      <header class="workspace-header"><button class="icon-button mobile-menu" aria-label="打开会话导航" aria-controls="session-sidebar" :aria-expanded="sidebarOpen" @click="sidebarOpen = !sidebarOpen"><UiIcon name="menu" /></button><div class="workspace-heading"><div class="workspace-breadcrumb"><span>工作台</span><span>/</span><strong>{{ activeView === 'sales' ? '销售经营分析' : agent.currentThread?.repoFullName || '新任务' }}</strong></div><h1>{{ activeView === 'sales' ? '销售经营看板' : agent.currentThread?.title || '开始你的下一个项目' }}</h1></div><div class="header-actions"><span class="run-status" :class="{ running: agent.streaming }"><span></span>{{ activeView === 'sales' ? reportLoading ? '读取报表' : '订单数据快照' : status }}</span><button class="secondary-button skills-button" @click="openSkills"><UiIcon name="skill" :size="16" /><span>Skill 管理</span></button><button class="icon-button header-new" :disabled="agent.streaming" aria-label="新建任务" @click="newThread"><UiIcon name="plus" /></button></div></header>
      <div v-if="activeView === 'chat' && (agent.currentThread?.branch || agent.currentThread?.pr?.url)" class="context-bar"><span v-if="agent.currentThread?.branch"><UiIcon name="branch" :size="14" />{{ agent.currentThread.branch }}</span><span class="context-separator" v-if="agent.currentThread?.branch">·</span><span>Gitee 仓库</span><a v-if="agent.currentThread?.pr?.url" :href="agent.currentThread.pr.url" target="_blank" rel="noopener noreferrer">查看 Pull Request<UiIcon name="link" :size="13" /></a></div>
      <div class="csv-entry"><nav class="workspace-views" aria-label="工作台视图"><button :class="{ active: activeView === 'sales' }" :aria-pressed="activeView === 'sales'" @click="activeView = 'sales'"><UiIcon name="chart" :size="15" />销售看板</button><button :class="{ active: activeView === 'chat' }" :aria-pressed="activeView === 'chat'" @click="activeView = 'chat'"><UiIcon name="chat" :size="15" />智能体对话</button></nav><button class="secondary-button" :disabled="agent.streaming" @click="showCsv = true"><UiIcon name="upload" :size="15" />CSV统计</button></div>
      <div v-if="activeView === 'sales'" class="sales-scroll"><SalesDashboard :report="salesReport" :loading="reportLoading" :error="reportError" @upload="showCsv = true" @refresh="loadLatestReport" @filter="filterReport" /></div>
      <div v-show="activeView === 'chat'" ref="messageList" class="message-list" @scroll.passive="observeScroll">
        <div v-if="agent.loading || agent.loadingHistory" class="loading-state" role="status"><span class="loading-ring"></span><strong>{{ agent.loading ? '正在加载工作区' : '正在恢复会话历史' }}</strong><p>加载完成后，会显示对话和工具执行记录。</p></div>
        <div v-else-if="agent.historyError" class="history-failure" role="alert"><UiIcon name="refresh" :size="25" /><h2>历史会话暂时无法加载</h2><p>{{ agent.historyError }}</p><button class="secondary-button" @click="agent.selectThread(agent.currentThreadId)">重试加载历史</button></div>
        <div v-else-if="!agent.messages.length" class="welcome-state"><span class="welcome-icon"><UiIcon name="code" :size="30" /></span><div class="welcome-eyebrow">AI Coding · AI 编程助手</div><h2>把想法，变成<br /><span>可运行的代码。</span></h2><p>连接你的 Gitee 仓库，一起分析、实现与验证。<br class="desktop-break" />你把握方向，智能体完成具体工作。</p><div class="suggestion-grid"><button v-for="item in suggestions" :key="item.title" @click="composer?.setDraft(item.prompt)"><UiIcon :name="item.icon" :size="21" /><strong>{{ item.title }}</strong><span>{{ item.description }}</span><span class="suggestion-arrow">↗</span></button></div><button class="welcome-skill-link" @click="openSkills"><UiIcon name="upload" :size="15" />上传 Skill，让助手掌握你的工作方法</button></div>
        <ChatMessage v-for="message in agent.messages" :key="message.id" :message="message" :thread-id="agent.currentThreadId" :streaming="agent.streaming" />
        <div v-if="agent.error" class="error-banner" role="alert"><strong>暂时未能完成请求</strong><span>{{ agent.error }}</span></div>
      </div>
      <button v-if="activeView === 'chat' && !stickToBottom && agent.messages.length" class="jump-bottom icon-button" aria-label="回到最新消息" @click="scrollToBottom"><UiIcon name="down" /></button>
      <ChatComposer v-show="activeView === 'chat'" ref="composer" v-model:repo="agent.selectedRepo" :disabled="agent.streaming || agent.loading || agent.loadingHistory || !!agent.historyError" :streaming="agent.streaming" :model="agent.selectedModel" :effort="agent.selectedEffort" @send="agent.submit" @stop="agent.stopStream" />
    </section><SkillManager v-if="showSkills" @close="showSkills = false" /><CsvAnalyzer v-if="showCsv" @close="showCsv = false" @analyze="analyzeCsvInChat" @generated="acceptReport" />
  </main>
</template>
<style scoped>
.history-failure { display:flex; flex-direction:column; align-items:center; text-align:center; gap:14px; max-width:460px; margin:50px auto; color:#929caf; }.history-failure h2 { font-size:18px; margin:0; color:#58657e; }.history-failure p { line-height:1.9; font-size:12px; margin:0; }
.csv-entry { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:9px 24px; border-bottom:1px solid var(--border); background:#fff; flex-shrink:0; }.csv-entry button { display:flex; align-items:center; gap:6px; white-space:nowrap; }.workspace-views { display:flex; gap:5px; }.workspace-views button { border:0; padding:8px 11px; background:transparent; color:#98a0b2; font-size:11px; border-radius:7px; cursor:pointer; }.workspace-views button.active { color:#7463e8; background:#f2effc; }.sales-scroll { flex:1; min-height:0; overflow-y:auto; background:#f7f8fc; }@media(max-width:600px) { .csv-entry { padding:7px 15px; }.workspace-views button { font-size:10px; padding:8px; } }
</style>

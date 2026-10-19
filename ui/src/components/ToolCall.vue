<script setup>
import { computed } from 'vue'

const props = defineProps({ tool: { type: Object, required: true } })
const labels = { in_progress: '执行中', completed: '已完成', error: '执行失败', interrupted: '已中断', unknown: '结果未记录' }
const statusLabel = computed(() => labels[props.tool.status] || '等待执行')
const duration = computed(() => {
  const ms = props.tool.duration_ms
  return Number.isFinite(ms) ? (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`) : ''
})
</script>

<template>
  <details class="tool-call" :class="`tool-call-${tool.status}`">
    <summary>
      <span class="tool-indicator" aria-hidden="true"></span>
      <strong>{{ tool.tool_name }}</strong>
      <span class="tool-status" role="status">{{ statusLabel }}</span>
      <span v-if="duration" class="tool-duration">{{ duration }}</span>
      <span class="tool-status">详情 ▾</span>
    </summary>
    <div class="tool-details">
      <h4>调用参数</h4>
      <pre>{{ tool.input || '无参数' }}</pre>
      <template v-if="tool.output">
        <h4>{{ tool.status === 'error' ? '错误信息' : '执行结果' }}</h4>
        <pre>{{ tool.output }}</pre>
      </template>
      <p v-else-if="tool.status === 'in_progress'">正在等待工具返回结果…</p>
    </div>
  </details>
</template>

<style scoped>
.tool-call { border: 1px solid var(--border); border-radius: 10px; background: #f8fafb; font-size: 13px; overflow: hidden; }
summary { display: flex; align-items: center; flex-wrap: wrap; gap: 9px; padding: 12px 14px; cursor: pointer; }
summary strong { overflow-wrap: anywhere; }
.tool-indicator { width: 8px; height: 8px; border-radius: 50%; background: #718096; flex-shrink: 0; }
.tool-call-in_progress .tool-indicator { background: #1686a0; animation: pulse 1.2s ease-in-out infinite; }
.tool-call-completed .tool-indicator { background: #25834b; }
.tool-call-error { border-color: #e6b9b9; }
.tool-call-error .tool-indicator { background: #c53c3c; }
.tool-call-interrupted .tool-indicator { background: #ad7417; }
.tool-status, .tool-duration { color: var(--muted); font-size: 12px; }
.tool-duration { margin-left: auto; }
.tool-details { border-top: 1px solid var(--border); padding: 0 14px 12px; }
h4 { margin: 12px 0 6px; font-size: 12px; }
pre { margin: 0; max-height: 280px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: 10px; background: #edf2f5; border-radius: 6px; font-size: 12px; }
p { color: var(--muted); }
@keyframes pulse { 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) { .tool-call-in_progress .tool-indicator { animation: none; } }
</style>

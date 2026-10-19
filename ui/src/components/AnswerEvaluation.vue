<script setup>
import { computed, onUnmounted, reactive, watch } from 'vue'
import { dashboardApi } from '../api/client.js'
import { createEvaluationLoader } from '../lib/answerEvaluation.js'

const props = defineProps({ threadId: String, feedback: { type: Object, required: true } })
const state = reactive({ document: null, error: '', absent: false, loading: true })
const loader = createEvaluationLoader({ fetchEvaluation: dashboardApi.getEvaluation, onChange: value => Object.assign(state, value) })
const dimensions = [ ['correctness', '正确性'], ['relevance', '相关性'], ['completeness', '完整性'], ['clarity', '清晰度'] ]
const result = computed(() => state.document?.result)
const total = computed(() => result.value?.quality_total)
const label = computed(() => ({ pass: '达到参考标准', needs_improvement: '仍有改进空间', needs_verification: '证据不足，待核实', needs_review: '需要人工复核' })[result.value?.quality_label] || '评测完成')
const status = computed(() => {
  if (state.loading) return '正在读取评分…'
  if (state.absent) return '本回答暂无自动评分记录。'
  const document = state.document
  if (!document) return ''
  if (document.state === 'skipped') return props.feedback.rating === 'not_helpful' ? '已记录不满意评价，等待补充评测…' : '本回答未抽样进行 AI 评分。'
  if (document.state === 'failed') return '自动评测暂未完成，已标记待复核。'
  if (document.state === 'pending') return '已进入评测队列，评分将在完成后显示。'
  if (document.state === 'running') return '正在评测回答，结果将自动更新…'
  return ''
})
function refresh() {
  if (!props.threadId || !props.feedback.question_id) return
  loader.start({ threadId: props.threadId, questionId: props.feedback.question_id, rating: props.feedback.rating })
}
watch(() => [props.threadId, props.feedback.question_id, props.feedback.rating], refresh, { immediate: true })
onUnmounted(() => loader.stop())
</script>

<template>
  <section class="answer-evaluation" aria-label="回答自动评分">
    <div class="evaluation-heading"><strong>回答评分</strong><span class="evaluation-caption">AI 自动评测，仅供参考</span></div>
    <template v-if="result">
      <div class="evaluation-summary">
        <span v-if="total !== null && total !== undefined" class="evaluation-total">{{ total }}<small> / 100</small></span>
        <span v-else class="evaluation-unverified">暂无总分</span>
        <span>{{ label }}</span>
      </div>
      <p v-if="state.document.review_required && result.quality_label !== 'needs_review'" class="evaluation-note">此回答已标记待复核。</p>
      <dl class="evaluation-dimensions">
        <div v-for="[name, title] in dimensions" :key="name"><dt>{{ title }}</dt><dd>{{ result.dimensions?.[name]?.value == null ? '待核实' : `${result.dimensions[name].value} / 4` }}</dd></div>
      </dl>
      <details><summary>查看评分依据</summary>
        <p v-for="[name, title] in dimensions" :key="name"><strong>{{ title }}：</strong>{{ result.dimensions?.[name]?.reason || '暂无依据' }}</p>
        <p v-for="(violation, index) in result.dimensions?.violations || []" :key="`risk-${index}`">待复核线索：{{ violation }}</p>
        <p class="evaluation-note">有效维度覆盖率 {{ Math.round((result.coverage || 0) * 100) }}%；证据不足时不计算完整总分。评分标准 v{{ state.document.standard_version }}，尚未完成人工校准。</p>
      </details>
    </template>
    <p v-else-if="status" class="evaluation-status" role="status">{{ status }}</p>
    <p v-if="state.error" class="evaluation-error" role="alert">{{ state.error }}</p>
    <button v-if="!state.loading" class="evaluation-refresh" @click="refresh">刷新评分</button>
  </section>
</template>

<style scoped>
.answer-evaluation { margin-top:16px; padding:14px 16px; border:1px solid var(--border,#e5e5ed); border-radius:10px; background:var(--surface,#fafafe); font-size:12px; color:#59647a; }
.evaluation-heading { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }.evaluation-heading strong { color:#41485d; font-size:13px; }.evaluation-caption,.evaluation-note { color:#7d8596; }
.evaluation-summary { display:flex; align-items:baseline; gap:14px; margin:10px 0; flex-wrap:wrap; }.evaluation-total { font-size:26px; font-weight:700; color:#6356a3; }.evaluation-total small { font-size:12px; font-weight:400; color:#7d8596; }.evaluation-unverified { font-weight:600; }
.evaluation-dimensions { display:flex; flex-wrap:wrap; gap:12px 24px; margin:10px 0; }.evaluation-dimensions div { display:flex; gap:8px; }.evaluation-dimensions dd { margin:0; font-weight:600; color:#41485d; }
details { margin-top:10px; }summary { cursor:pointer; color:#6356a3; }p { line-height:1.6; margin:8px 0 0; }.evaluation-error { color:#b33f54; }.evaluation-refresh { margin-top:10px; border:0; padding:0; color:#6356a3; background:transparent; cursor:pointer; font:inherit; }
</style>

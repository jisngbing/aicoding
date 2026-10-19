<script setup>
import { ref } from 'vue'
import { dashboardApi } from '../api/client.js'

const props = defineProps({ threadId: { type: String, required: true }, feedback: { type: Object, required: true } })
const emit = defineEmits(['submitted'])
const negative = ref(false)
const reason = ref('')
const saving = ref(false)
const error = ref('')

async function submit(rating) {
  if (saving.value || props.feedback.rating) return
  if (rating === 'not_helpful' && !reason.value.trim()) {
    error.value = '请写一下不满意的原因'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const result = await dashboardApi.submitFeedback(props.threadId, props.feedback.question_id, rating, reason.value.trim())
    emit('submitted', result)
  } catch (cause) { error.value = cause.message || '提交失败，请重试' }
  finally { saving.value = false }
}
</script>

<template>
  <section class="answer-feedback" aria-label="回答评价">
    <p v-if="feedback.rating" role="status">{{ feedback.rating === 'helpful' ? '感谢反馈，很高兴能帮到你。' : '感谢反馈，已记录你的意见。' }}</p>
    <template v-else>
      <div class="feedback-actions"><span>这次回答对你有帮助吗？</span><button :disabled="saving" @click="submit('helpful')">有帮助</button><button :disabled="saving" :aria-expanded="negative" @click="negative = !negative; error = ''">不满意</button></div>
      <form v-if="negative" @submit.prevent="submit('not_helpful')">
        <label :for="`reason-${feedback.question_id}`">哪里不满意？</label>
        <textarea :id="`reason-${feedback.question_id}`" v-model="reason" required maxlength="2000" rows="3" placeholder="请简单写一下原因，帮助我们改进回答" :disabled="saving"></textarea>
        <button type="submit" :disabled="saving || !reason.trim()">{{ saving ? '正在提交…' : '提交评价' }}</button>
      </form>
      <p v-if="error" class="feedback-error" role="alert">{{ error }}</p>
    </template>
  </section>
</template>

<style scoped>
.answer-feedback { margin-top:16px; padding-top:12px; border-top:1px solid var(--border, #eceef3); color:#778197; font-size:12px; }
.answer-feedback p { margin:0; }.feedback-actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
button { border:1px solid #e1dfed; background:#fff; color:#6356a3; border-radius:7px; padding:6px 12px; cursor:pointer; font:inherit; }
button:hover { background:#f4f1fe; }button:disabled { opacity:.5; cursor:default; }form { margin-top:12px; display:grid; gap:8px; max-width:480px; }textarea { border:1px solid #dddde8; border-radius:8px; padding:10px; resize:vertical; font:inherit; }form button { justify-self:start; }.feedback-error { color:#b33f54; margin-top:8px!important; }
</style>

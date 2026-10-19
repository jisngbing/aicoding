<script setup>
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { computed, onUnmounted, ref } from 'vue'

import TodoPlan from './TodoPlan.vue'
import ToolCall from './ToolCall.vue'
import UiIcon from './UiIcon.vue'
import AnswerFeedback from './AnswerFeedback.vue'
import AnswerEvaluation from './AnswerEvaluation.vue'

const props = defineProps({
  threadId: String,
  streaming: Boolean,
  message: {
    type: Object,
    required: true,
  },
})

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})
markdown.renderer.rules.table_open = () => '<div class="table-scroll"><table>'
markdown.renderer.rules.table_close = () => '</table></div>'
const copied = ref(false)
const copyError = ref(false)
let copyTimer
const hasText = computed(() => textChunks().length > 0)
const time = computed(() => {
  const date = new Date(props.message.timestamp)
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(date)
})
async function copyReply() {
  try {
    await navigator.clipboard.writeText(textChunks().map((chunk) => chunk.text).join('\n'))
    copied.value = true
    copyError.value = false
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = false }, 2000)
  } catch { copyError.value = true }
}
onUnmounted(() => clearTimeout(copyTimer))

function renderMarkdown(text) {
  return DOMPurify.sanitize(markdown.render(text || ''))
}

function textChunks() {
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'text')
}

function todoChunks() {
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'todo')
}

function errorChunks() {
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'error')
}

function toolChunks() {
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'tool')
}
</script>

<template>
  <article class="message" :class="`message-${message.author}`">
    <div v-if="message.author === 'user'" class="user-bubble">
      <div
        v-for="(chunk, index) in textChunks()"
        :key="`text-${index}`"
        class="plain-text"
      >
        {{ chunk.text }}
      </div>
    </div>

    <template v-else>
      <div v-if="hasText" class="message-meta"><span class="assistant-avatar"><UiIcon name="code" :size="17" /></span><strong>编程助手</strong><time>{{ time }}</time><button class="icon-button copy-reply" :aria-label="copied ? '已复制回复' : '复制回复'" @click="copyReply"><UiIcon :name="copied ? 'check' : 'copy'" :size="14" /></button><span v-if="copyError" class="copy-error">复制失败，请手动选择文本</span></div>
      <div class="message-content">
      <div
        v-for="(chunk, index) in textChunks()"
        :key="`text-${index}`"
        class="markdown-body"
        v-html="renderMarkdown(chunk.text)"
      ></div>

      <TodoPlan
        v-for="(chunk, index) in todoChunks()"
        :key="`todo-${index}`"
        :todos="chunk.todos || []"
      />

      <ToolCall
        v-for="(chunk, index) in toolChunks()"
        :key="`tool-${index}`"
        :tool="chunk.tool"
      />

      <div
        v-for="(chunk, index) in errorChunks()"
        :key="`error-${index}`"
        class="error-banner"
      >
        {{ chunk.text }}
      </div>
      </div>
      <AnswerFeedback v-if="hasText && message.feedback && !streaming" :thread-id="threadId" :feedback="message.feedback" @submitted="message.feedback = $event" />
      <AnswerEvaluation v-if="hasText && message.feedback && !streaming" :thread-id="threadId" :feedback="message.feedback" />
    </template>
  </article>
</template>

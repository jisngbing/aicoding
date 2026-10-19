<script setup>
import { nextTick, ref, watch } from 'vue'
import UiIcon from './UiIcon.vue'
const props = defineProps({ disabled: Boolean, streaming: Boolean, model: String, effort: String, repo: String })
const emit = defineEmits(['send', 'stop', 'update:repo'])
const draft = ref('')
const input = ref(null)
function send() { const content = draft.value.trim(); if (!content || props.disabled) return; emit('send', content); draft.value = '' }
function onKeydown(event) { if (event.isComposing || event.keyCode === 229) return; if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send() } }
function setDraft(text) { draft.value = text; nextTick(() => input.value?.focus()) }
watch(draft, async () => { await nextTick(); if (input.value) { input.value.style.height = 'auto'; input.value.style.height = `${Math.min(input.value.scrollHeight, 160)}px` } })
defineExpose({ setDraft })
</script>
<template>
  <div class="composer-area"><footer class="composer" :class="{ 'is-busy': disabled }"><label class="repo-field"><UiIcon name="branch" :size="15" /><span>仓库</span><input :value="repo" :disabled="disabled" aria-label="Gitee 仓库地址" placeholder="https://gitee.com/owner/repo" @input="emit('update:repo', $event.target.value)" /><span class="repo-provider">GITEE</span></label><textarea ref="input" v-model="draft" :disabled="disabled" rows="2" aria-label="任务指令" placeholder="描述你的需求，或继续追问…" @keydown="onKeydown" /><div class="composer-footer"><div class="model-label"><UiIcon name="code" :size="14" /><span>{{ model || 'qwen3.8-flash' }}</span><span class="model-mode">{{ effort === 'default' || !effort ? '默认' : effort }}</span></div><div class="composer-send"><span class="keyboard-hint">Enter 发送 <span>·</span> Shift + Enter 换行</span><button v-if="streaming" class="stop-button" @click="emit('stop')"><UiIcon name="stop" :size="13" />停止</button><button v-else class="send-button" :disabled="disabled || !draft.trim()" aria-label="发送指令" @click="send"><UiIcon name="arrow" :size="18" /></button></div></div></footer><p class="composer-note">AI 生成的代码需要验证。执行记录与测试结果会显示在对话中。</p></div>
</template>

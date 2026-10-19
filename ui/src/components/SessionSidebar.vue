<script setup>
import { computed, ref } from 'vue'
import UiIcon from './UiIcon.vue'
const props = defineProps({ threads: { type: Array, default: () => [] }, activeId: String, disabled: Boolean, mobileOpen: Boolean })
const emit = defineEmits(['new-thread', 'select-thread', 'delete-thread', 'manage-skills', 'refresh', 'close'])
const search = ref('')
const filtered = computed(() => props.threads.filter((thread) => `${thread.title || ''} ${thread.repoFullName || thread.repo || ''}`.toLowerCase().includes(search.value.trim().toLowerCase())))
function titleOf(thread) { return thread.title || thread.repoFullName || thread.repo || '未命名任务' }
function confirmDelete(thread) { if (window.confirm('确认删除该会话吗？该操作会同时删除会话历史、checkpoint 和业务记录。')) emit('delete-thread', thread.id) }
</script>
<template>
  <aside id="session-sidebar" class="sidebar" :class="{ 'is-open': mobileOpen }" aria-label="会话导航">
    <div class="sidebar-brand"><span class="brand-mark">AI<span></span></span><div><strong>AI Coding</strong><small>AI CODING WORKSPACE</small></div><button class="icon-button sidebar-close" aria-label="关闭会话导航" @click="emit('close')"><UiIcon name="close" /></button></div>
    <button class="new-button" :disabled="disabled" @click="emit('new-thread')"><UiIcon name="plus" />新建任务<span>＋</span></button>
    <label class="session-search"><UiIcon name="search" :size="16" /><input v-model="search" aria-label="搜索会话" placeholder="搜索会话或仓库" /></label>
    <div class="sidebar-section-title"><span>最近会话 <b>{{ threads.length }}</b></span><button class="icon-button" aria-label="刷新会话列表" @click="emit('refresh')"><UiIcon name="refresh" :size="15" /></button></div>
    <nav class="thread-list" aria-label="最近会话"><div v-for="thread in filtered" :key="thread.id" class="thread-row" :class="{ active: thread.id === activeId }"><button class="thread-item" :aria-current="thread.id === activeId ? 'page' : undefined" @click="emit('select-thread', thread.id)"><UiIcon name="chat" :size="17" /><span class="thread-content"><span class="thread-title">{{ titleOf(thread) }}</span><small>{{ thread.repoFullName || thread.repo || '本地项目' }}</small></span><span class="status-dot" :class="thread.status" :title="thread.status"></span></button><button class="thread-delete icon-button" :aria-label="`删除会话：${titleOf(thread)}`" @click="confirmDelete(thread)"><UiIcon name="trash" :size="14" /></button></div><p v-if="!filtered.length" class="sidebar-empty">{{ search ? '没有匹配的会话' : '还没有会话，开始一个新任务吧。' }}</p></nav>
    <div class="sidebar-bottom"><button class="skill-nav-button" @click="emit('manage-skills')"><span class="skill-nav-icon"><UiIcon name="skill" /></span><span><strong>Skill 技能库</strong><small>上传文件 · 网络导入</small></span><UiIcon name="upload" :size="16" /></button><div class="workspace-identity"><span class="identity-avatar">AI</span><span><strong>本地工作区</strong><small>你的专属编程助手</small></span><span class="identity-dot"></span></div></div>
  </aside>
</template>

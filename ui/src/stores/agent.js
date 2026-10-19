import { defineStore } from 'pinia'

import { dashboardApi } from '../api/client.js'
import { streamAgentMessage } from '../api/sse.js'

const DEFAULT_REPO = ''

function repoKey(value) {
  return String(value || '').trim().replace(/^https?:\/\/gitee\.com\//, '').replace(/\.git\/?$/, '').replace(/\/$/, '')
}

function nowIso() {
  return new Date().toISOString()
}

function createTextMessage(author, text, idPrefix = author) {
  return {
    id: `${idPrefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    author,
    timestamp: nowIso(),
    chunks: text ? [{ kind: 'text', text }] : [],
  }
}

function findLocalUserMessage(messages, text) {
  return [...messages].reverse().find((message) => {
    if (message.author !== 'user' || !String(message.id || '').startsWith('local-user-')) {
      return false
    }
    const content = (message.chunks || [])
      .filter((chunk) => chunk.kind === 'text')
      .map((chunk) => chunk.text || '')
      .join('')
      .trim()
    return content === text
  })
}

function normalizeThreadMessages(messages) {
  if (!Array.isArray(messages)) return []
  return messages
    .filter((message) => message && Array.isArray(message.chunks))
    .map((message) => ({
      id: String(message.id || `${message.author || 'message'}-${Date.now()}`),
      author: message.author || 'agent',
      timestamp: message.timestamp || nowIso(),
      chunks: message.chunks,
      hidden: !!message.hidden,
      feedback: message.feedback || null,
    }))
}

function ensureAgentMessage(messages, messageId) {
  let message = messages.find((item) => item.id === messageId)
  if (!message) {
    message = {
      id: messageId,
      author: 'agent',
      timestamp: nowIso(),
      chunks: [],
    }
    messages.push(message)
  }
  return message
}

function upsertTodoMessage(messages, messageId, todos) {
  const message = ensureAgentMessage(messages, messageId)
  message.author = 'agent'
  message.chunks = [
    { kind: 'text', text: '任务计划' },
    { kind: 'todo', todos },
  ]
}

function appendTextDelta(messages, messageId, content, mode = 'append') {
  if (!messageId || !content) return
  const message = ensureAgentMessage(messages, messageId)
  const chunk = message.chunks.find((item) => item.kind === 'text')
  if (chunk) {
    chunk.text = mode === 'replace' ? content : `${chunk.text || ''}${content}`
  } else {
    message.chunks.push({ kind: 'text', text: content })
  }
}

function mergeThreadMeta(target, source) {
  if (!target || !source) return
  target.status = source.status || target.status
  target.branch = source.branch || target.branch
  target.pr = source.pr || target.pr
  target.updatedAt = source.updatedAt || target.updatedAt
}

export const useAgentStore = defineStore('agent', {
  state: () => ({
    user: null,
    options: null,
    threads: [],
    currentThread: null,
    messages: [],
    selectedRepo: DEFAULT_REPO,
    selectedModel: '',
    selectedEffort: 'default',
    streaming: false,
    loading: false,
    loadingHistory: false,
    historyError: '',
    historyRequestId: 0,
    error: '',
    controller: null,
    activeRunThreadId: null,
  }),
  getters: {
    currentThreadId: (state) => state.currentThread?.id || null,
    modelOptions: (state) => state.options?.models || [],
    defaultRepo: (state) => state.options?.default_repo_url || DEFAULT_REPO,
    canSend: (state) => !state.streaming && !state.loading && !state.loadingHistory && !state.historyError,
  },
  actions: {
    async bootstrap() {
      this.loading = true
      this.error = ''
      try {
        const [user, options, threads] = await Promise.all([
          dashboardApi.me(),
          dashboardApi.options(),
          dashboardApi.listThreads(),
        ])
        this.user = user
        this.options = options
        this.threads = threads
        if (!this.selectedRepo) this.selectedRepo = this.defaultRepo
        this.selectedModel = options.default_agent_model || options.models?.[0]?.id || ''
        this.selectedEffort = options.default_agent_reasoning_effort || 'default'
        this.loading = false
        if (!this.currentThread && threads.length) {
          const defaultThread = threads.find((thread) => this.defaultRepo && repoKey(thread.repoFullName || thread.repo) === repoKey(this.defaultRepo)) || threads[0]
          if (defaultThread) await this.selectThread(defaultThread.id)
        }
      } catch (error) {
        this.error = error.message || '初始化前端失败'
      } finally {
        this.loading = false
      }
    },
    async refreshThreads() {
      try {
        this.threads = await dashboardApi.listThreads()
      } catch {
        // 侧边栏刷新失败不影响当前对话继续展示。
      }
    },
    async selectThread(threadId) {
      if (this.streaming) {
        this.controller?.abort()
        this.streaming = false
        this.activeRunThreadId = null
        this.controller = null
      }
      this.error = ''
      this.historyError = ''
      const requestId = ++this.historyRequestId
      this.loadingHistory = true
      this.currentThread = this.threads.find((thread) => thread.id === threadId) || { id: threadId }
      this.messages = []
      try {
        const thread = await dashboardApi.getThread(threadId)
        if (requestId !== this.historyRequestId) return
        this.currentThread = thread
        this.selectedRepo = thread.repo || thread.repoFullName || this.defaultRepo
        this.messages = normalizeThreadMessages(thread.messages)
      } catch (error) {
        if (requestId === this.historyRequestId) this.historyError = error.message || '读取会话历史失败'
      } finally {
        if (requestId === this.historyRequestId) this.loadingHistory = false
      }
    },
    async createThread() {
      if (this.streaming) return
      this.historyRequestId++
      this.loadingHistory = false
      this.historyError = ''
      this.currentThread = null
      this.messages = []
      this.error = ''
      this.activeRunThreadId = null
      if (!this.selectedRepo) this.selectedRepo = this.defaultRepo
    },
    async deleteThread(threadId) {
      if (this.streaming) return
      await dashboardApi.deleteThread(threadId)
      this.threads = this.threads.filter((thread) => thread.id !== threadId)
      if (this.currentThread?.id === threadId) {
        this.historyError = ''
        this.currentThread = null
        this.messages = []
        if (this.threads.length) await this.selectThread(this.threads[0].id)
      }
    },
    stopStream() {
      this.controller?.abort()
    },
    async submit(content) {
      const prompt = content.trim()
      if (!prompt || !this.canSend) return

      this.error = ''
      this.streaming = true
      this.controller = new AbortController()
      const localUserMessage = createTextMessage('user', prompt, 'local-user')
      this.messages.push(localUserMessage)

      try {
        const initialThreadId = this.currentThread?.id || null
        this.activeRunThreadId = initialThreadId || 'pending'
        await this.consumeMessageStream(initialThreadId, prompt)
      } catch (error) {
        if (error.name !== 'AbortError') {
          this.error = error.message || 'Agent 执行失败'
          this.currentThread = {
            ...(this.currentThread || {}),
            status: 'error',
          }
        }
      } finally {
        this.streaming = false
        this.controller = null
        this.activeRunThreadId = null
        await this.refreshThreads()
      }
    },
    async consumeMessageStream(threadId, prompt) {
      await streamAgentMessage(
        threadId,
        {
          content: prompt,
          repo: this.selectedRepo || this.defaultRepo,
          model_id: this.selectedModel || null,
          effort: this.selectedEffort || null,
        },
        {
          signal: this.controller.signal,
          onEvent: (event, data) => {
            const eventThreadId = data.thread_id || data.id || threadId
            if (eventThreadId && this.currentThread?.id && this.currentThread.id !== eventThreadId) return

            if (event === 'thread_snapshot' || event === 'thread_done') {
              if (!this.currentThread) {
                this.currentThread = data
                this.selectedRepo = data.repo || data.repoFullName || this.selectedRepo || this.defaultRepo
              } else {
                mergeThreadMeta(this.currentThread, data)
              }
              this.activeRunThreadId = data.id || data.thread_id || eventThreadId || this.activeRunThreadId
              return
            }

            if (event === 'user_message') {
              const text = String(data.content || '').trim()
              const local = findLocalUserMessage(this.messages, text)
              if (local) {
                local.id = data.message_id || local.id
                local.timestamp = data.timestamp || local.timestamp
              } else if (text) {
                this.messages.push(createTextMessage('user', text, data.message_id || 'user'))
              }
              return
            }

            if (event === 'todo_delta') {
              this.messages = this.messages.filter((message) => !String(message.id).includes('-assistant-startup-'))
              upsertTodoMessage(
                this.messages,
                data.message_id || `todo-${Date.now()}`,
                Array.isArray(data.todos) ? data.todos : [],
              )
              return
            }

            if (event === 'tool_delta') {
              if (!data.message_id) return
              this.messages = this.messages.filter((message) => !String(message.id).includes('-assistant-startup-'))
              const message = ensureAgentMessage(this.messages, data.message_id)
              message.timestamp = data.timestamp || message.timestamp
              message.chunks = [{ kind: 'tool', tool: { ...data } }]
              return
            }

            if (event === 'message_start') {
              if (data.message_id) ensureAgentMessage(this.messages, data.message_id)
              return
            }

            if (event === 'answer_feedback') {
              const message = this.messages.find((item) => item.id === data.message_id)
              if (message) message.feedback = data
              return
            }

            if (event === 'text_delta') {
              if (!String(data.message_id || '').includes('-assistant-startup-')) {
                this.messages = this.messages.filter((message) => !String(message.id).includes('-assistant-startup-'))
              }
              appendTextDelta(
                this.messages,
                data.message_id || `assistant-${Date.now()}`,
                data.content || '',
                data.mode || 'append',
              )
              return
            }

            if (event === 'status') {
              const startup = this.messages.find((message) => String(message.id).includes('-assistant-startup-'))
              if (startup) startup.chunks = [{ kind: 'text', text: data.message || '' }]
              return
            }

            if (event === 'error') {
              this.messages = this.messages.filter((message) => !String(message.id).includes('-assistant-startup-'))
              this.error = data.message || data.detail || 'Agent 执行失败'
              if (this.currentThread) this.currentThread.status = 'error'
              return
            }

            if (event === 'done') {
              this.messages = this.messages.filter((message) => !String(message.id).includes('-assistant-startup-'))
              if (this.currentThread && this.currentThread.status === 'running') {
                this.currentThread.status = 'finished'
              }
            }
          },
        },
      )
    },
  },
})

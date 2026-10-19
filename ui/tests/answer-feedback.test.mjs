import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { useAgentStore } from '../src/stores/agent.js'
import { dashboardApi } from '../src/api/client.js'

test('Feedback events attach to the correct completed answer and survive history reload', async () => {
  setActivePinia(createPinia())
  const store = useAgentStore()
  store.currentThread = { id: 'thread' }
  store.controller = new AbortController()
  const feedback = { question_id: 'question-2', trace_id: 'trace-2', rating: null }
  const events = [
    ['text_delta', { thread_id: 'thread', message_id: 'answer-1', content: 'First answer' }],
    ['text_delta', { thread_id: 'thread', message_id: 'answer-2', content: 'Second answer' }],
    ['answer_feedback', { thread_id: 'another-thread', message_id: 'answer-1', question_id: 'wrong' }],
    ['answer_feedback', { thread_id: 'thread', message_id: 'answer-2', ...feedback }],
    ['done', { thread_id: 'thread' }],
  ]
  const originalFetch = globalThis.fetch
  const originalHistory = dashboardApi.getThread
  globalThis.fetch = async () => new Response(events.map(([event, data]) =>
    `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''))
  try {
    await store.consumeMessageStream('thread', 'question')
    assert.equal(store.messages[0].feedback, undefined)
    assert.equal(store.messages[1].feedback.question_id, 'question-2')
    dashboardApi.getThread = async () => ({ id: 'thread', messages: [{
      id: 'persisted-answer', author: 'agent', chunks: [{ kind: 'text', text: 'Second answer' }],
      feedback: { ...feedback, rating: 'not_helpful', reason: 'wrong date' },
    }] })
    await store.selectThread('thread')
    assert.equal(store.messages[0].feedback.rating, 'not_helpful')
    assert.equal(store.messages[0].feedback.reason, 'wrong date')
  } finally {
    globalThis.fetch = originalFetch
    dashboardApi.getThread = originalHistory
  }
})

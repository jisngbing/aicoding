import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { useAgentStore } from '../src/stores/agent.js'

test('SSE tool lifecycle updates cards by ID while preserving text and parallel calls', async () => {
  setActivePinia(createPinia())
  const store = useAgentStore()
  store.currentThread = { id: 'thread' }
  store.controller = new AbortController()
  const tool = (id, status, output = '') => ['tool_delta', {
    thread_id: 'thread', message_id: id, tool_call_id: id,
    tool_name: 'read_file', input: '{"path":"a.py"}', status, output,
  }]
  const events = [
    tool('call-a', 'in_progress'), tool('call-b', 'in_progress'),
    ['text_delta', { thread_id: 'thread', message_id: 'answer', content: '正在读取', mode: 'replace' }],
    tool('call-b', 'completed', 'file b'), tool('call-a', 'error', 'not found'),
    ['tool_delta', { thread_id: 'another-thread', message_id: 'wrong' }],
  ]
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(events.map(([event, data]) =>
    `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' },
  })
  try {
    await store.consumeMessageStream('thread', '测试')
    assert.deepEqual(store.messages.map(message => message.id), ['call-a', 'call-b', 'answer'])
    assert.equal(store.messages[0].chunks[0].tool.status, 'error')
    assert.equal(store.messages[1].chunks[0].tool.output, 'file b')
    assert.equal(store.messages[2].chunks[0].text, '正在读取')
  } finally {
    globalThis.fetch = originalFetch
  }
})

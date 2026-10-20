import assert from 'node:assert/strict'
import test from 'node:test'
import axios from 'axios'
import { createPinia, setActivePinia } from 'pinia'

let respond
axios.defaults.adapter = async (config) => ({
  data: await respond(config.url), status: 200, statusText: 'OK', headers: {}, config,
})
const { useAgentStore } = await import('../src/stores/agent.js')
const thread = { id: 'thread-a', repoFullName: 'bing-jiang123456/test' }

function setup(detail) {
  setActivePinia(createPinia())
  respond = async (url) => {
    if (url === '/me') return { login: 'local' }
    if (url === '/options') return { models: [] }
    if (url === '/threads') return [thread]
    if (url === '/threads/thread-a') return detail()
    throw new Error(`unexpected request ${url}`)
  }
  return useAgentStore()
}

test('slow history does not block sidebar; creating a task ignores stale history', async () => {
  let resolveHistory
  const history = new Promise((resolve) => { resolveHistory = resolve })
  const store = setup(() => history)
  const bootstrap = store.bootstrap()
  for (let i = 0; i < 20 && !store.loadingHistory; i++) {
    await new Promise((resolve) => setImmediate(resolve))
  }
  assert.equal(store.loading, false)
  assert.equal(store.loadingHistory, true)
  assert.equal(store.threads.length, 1)
  await store.createThread()
  resolveHistory({ ...thread, messages: [] })
  await bootstrap
  assert.equal(store.currentThread, null)
  assert.equal(store.loadingHistory, false)
})

test('history timeout clears loading and displays an actionable error', async () => {
  const store = setup(() => { throw Object.assign(new Error('timeout'), { code: 'ECONNABORTED' }) })
  await store.bootstrap()
  assert.equal(store.loading, false)
  assert.equal(store.loadingHistory, false)
  assert.match(store.error, /加载会话超时/)
})

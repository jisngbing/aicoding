import assert from 'node:assert/strict'
import test from 'node:test'
import axios from 'axios'
import { createPinia, setActivePinia } from 'pinia'

let respond
axios.defaults.adapter = async (config) => ({
  data: await respond(config.url), status: 200, statusText: 'OK', headers: {}, config,
})
const { useAgentStore } = await import('../src/stores/agent.js')
const thread = { id: 'thread-a', repoFullName: 'example/repo' }

function setup(detail, options = { models: [] }, threads = [thread]) {
  setActivePinia(createPinia())
  respond = async (url) => {
    if (url === '/me') return { login: 'local' }
    if (url === '/options') return options
    if (url === '/threads') return threads
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

test('configured repository is shared by bootstrap and new tasks without a personal fallback', async () => {
  const configured = 'https://gitee.com/example/repo.git'
  const store = setup(() => ({ ...thread, messages: [] }), { models: [], default_repo_url: configured }, [
    { id: 'other-thread', repoFullName: 'other/project' }, thread,
  ])
  await store.bootstrap()
  assert.equal(store.defaultRepo, configured)
  assert.equal(store.currentThread.id, thread.id)
  await store.createThread()
  store.selectedRepo = ''
  await store.createThread()
  assert.equal(store.selectedRepo, configured)
})

test('empty installation starts without silently choosing a repository', async () => {
  const store = setup(() => { throw new Error('no history expected') }, { models: [] }, [])
  await store.bootstrap()
  assert.equal(store.defaultRepo, '')
  assert.equal(store.selectedRepo, '')
  assert.equal(store.currentThread, null)
})

test('history timeout clears loading and displays an actionable error', async () => {
  const store = setup(() => { throw Object.assign(new Error('timeout'), { code: 'ECONNABORTED' }) })
  await store.bootstrap()
  assert.equal(store.loading, false)
  assert.equal(store.loadingHistory, false)
  assert.match(store.historyError, /加载会话超时/)
})

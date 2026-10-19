import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { useAgentStore } from '../src/stores/agent.js'
import { dashboardApi } from '../src/api/client.js'

test('History failure exits loading, blocks incomplete context, and retries successfully', async () => {
  setActivePinia(createPinia())
  const store = useAgentStore()
  const original = dashboardApi.getThread
  try {
    dashboardApi.getThread = async () => { throw new Error('MongoDB隧道未连接') }
    await store.selectThread('history-a')
    assert.equal(store.loadingHistory, false)
    assert.equal(store.historyError, 'MongoDB隧道未连接')
    assert.equal(store.canSend, false)
    dashboardApi.getThread = async () => ({ id: 'history-a', repo: 'owner/repo', messages: [
      { id: 'm1', author: 'user', chunks: [{ kind: 'text', text: '历史问题' }] },
    ] })
    await store.selectThread('history-a')
    assert.equal(store.historyError, '')
    assert.equal(store.canSend, true)
    assert.equal(store.messages[0].chunks[0].text, '历史问题')
    store.historyError = 'previous failure'
    await store.createThread()
    assert.equal(store.historyError, '')
  } finally { dashboardApi.getThread = original }
})

test('A stale history error cannot replace the newly selected conversation', async () => {
  setActivePinia(createPinia())
  const store = useAgentStore()
  const original = dashboardApi.getThread
  let rejectOlder
  try {
    dashboardApi.getThread = async id => id === 'old' ? new Promise((resolve, reject) => { rejectOlder = reject }) : ({ id, messages: [] })
    const older = store.selectThread('old')
    await store.selectThread('new')
    rejectOlder(new Error('old connection failed'))
    await older
    assert.equal(store.currentThread.id, 'new')
    assert.equal(store.historyError, '')
    assert.equal(store.loadingHistory, false)
  } finally { dashboardApi.getThread = original }
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { createEvaluationLoader } from '../src/lib/answerEvaluation.js'

const tick = () => new Promise(resolve => setImmediate(resolve))

test('Pending evaluation polls until completed and then stops', async () => {
  const updates = [], timers = []
  let calls = 0
  const loader = createEvaluationLoader({
    fetchEvaluation: async () => ++calls === 1 ? { state: 'running' } : { state: 'completed', result: { quality_total: 85 } },
    onChange: state => updates.push(state), schedule: fn => timers.push(fn), cancel: () => {},
  })
  loader.start({ threadId: 't', questionId: 'q' })
  await tick()
  assert.equal(timers.length, 1)
  timers.shift()()
  await tick()
  assert.equal(updates.at(-1).document.result.quality_total, 85)
  assert.equal(timers.length, 0)
  loader.stop()
})

test('Changing answers and unmounting discard stale responses', async () => {
  const requests = [], updates = []
  const loader = createEvaluationLoader({ fetchEvaluation: () => new Promise(resolve => requests.push(resolve)), onChange: state => updates.push(state) })
  loader.start({ threadId: 't', questionId: 'old' })
  loader.start({ threadId: 't', questionId: 'new' })
  requests[1]({ state: 'completed', question_id: 'new' })
  await tick()
  requests[0]({ state: 'completed', question_id: 'old' })
  await tick()
  assert.equal(updates.at(-1).document.question_id, 'new')
  loader.start({ threadId: 't', questionId: 'last' })
  loader.stop()
  requests[2]({ state: 'completed', question_id: 'last' })
  await tick()
  assert.equal(updates.at(-1).document, null)
})

test('Sampled out answers stop polling; negative feedback waits for promotion', async () => {
  const timers = []
  const loader = createEvaluationLoader({ fetchEvaluation: async () => ({ state: 'skipped' }), onChange: () => {}, schedule: fn => timers.push(fn), cancel: () => {} })
  loader.start({ threadId: 't', questionId: 'q' })
  await tick()
  assert.equal(timers.length, 0)
  loader.start({ threadId: 't', questionId: 'q', rating: 'not_helpful' })
  await tick()
  assert.equal(timers.length, 1)
  loader.stop()
})

test('Missing records retry briefly; a missing backend route reports restart guidance', async () => {
  const timers = [], updates = []
  let detail = '本轮尚无评测记录'
  const loader = createEvaluationLoader({ fetchEvaluation: async () => { throw { response: { status: 404, data: { detail } } } },
    onChange: state => updates.push(state), schedule: fn => timers.push(fn), cancel: () => {} })
  loader.start({ threadId: 't', questionId: 'q' })
  await tick()
  timers.shift()(); await tick()
  timers.shift()(); await tick()
  assert.equal(updates.at(-1).absent, true)
  assert.equal(timers.length, 0)
  detail = 'Not Found'
  loader.start({ threadId: 't', questionId: 'q' })
  await tick()
  assert.match(updates.at(-1).error, /重启后端/)
  assert.equal(timers.length, 0)
  loader.stop()
})

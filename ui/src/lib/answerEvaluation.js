// A generation token prevents slow responses from a previous answer replacing this one.
export function createEvaluationLoader({ fetchEvaluation, onChange, schedule = setTimeout, cancel = clearTimeout, now = Date.now }) {
  let generation = 0
  let timer
  let target
  let started
  let missing = 0
  let failures = 0

  function later(token, delay) {
    if (token === generation && now() - started < 180000) {
      timer = schedule(() => load(token), delay)
    }
  }

  async function load(token) {
    if (!target || token !== generation) return
    const current = target
    try {
      const document = await fetchEvaluation(current.threadId, current.questionId)
      if (token !== generation) return
      missing = failures = 0
      onChange({ document, error: '', loading: false })
      if (['pending', 'running'].includes(document.state) || (document.state === 'skipped' && current.rating === 'not_helpful')) {
        later(token, 5000)
      }
    } catch (cause) {
      if (token !== generation) return
      const status = cause.response?.status
      if (status === 404 && cause.response?.data?.detail !== 'Not Found') {
        missing += 1
        onChange({ document: null, error: '', loading: missing < 3, absent: missing >= 3 })
        if (missing < 3) later(token, 2000)
      } else {
        failures += 1
        onChange({ error: status === 404 ? '评分服务尚未就绪，请重启后端后重试。' : '评分暂时无法读取，请稍后重试。', loading: false })
        if (status !== 404 && failures < 3) later(token, 10000)
      }
    }
  }

  return {
    start(value) {
      generation += 1
      cancel(timer)
      target = value
      started = now()
      missing = failures = 0
      onChange({ document: null, error: '', absent: false, loading: true })
      void load(generation)
    },
    stop() { generation += 1; cancel(timer); target = null },
  }
}

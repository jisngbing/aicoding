import axios from 'axios'

const http = axios.create({
  baseURL: '/dashboard/api',
  withCredentials: true,
  timeout: 20000,
})

http.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') error.message = detail
    if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
      error.message = String(error.config?.url || '').startsWith('/csv')
        ? 'CSV处理超时，请稍后重试或缩小文件。'
        : String(error.config?.url || '').startsWith('/skills')
        ? 'Skill 请求超时，请检查下载地址或后端连接后重试。'
        : '加载会话超时，请检查后端和 MongoDB 连接后重试。'
    }
    return Promise.reject(error)
  },
)

export const dashboardApi = {
  async getEvaluation(threadId, questionId) {
    const { data } = await http.get(`/threads/${encodeURIComponent(threadId)}/questions/${encodeURIComponent(questionId)}/evaluation`)
    return data
  },
  async submitFeedback(threadId, questionId, rating, reason = '') {
    const { data } = await http.post(`/threads/${encodeURIComponent(threadId)}/questions/${encodeURIComponent(questionId)}/feedback`, { rating, reason })
    return data
  },
  async latestCsvReport() {
    const { data } = await http.get('/csv/latest')
    return data
  },
  async uploadCsv(file) {
    const { data } = await http.post('/csv/uploads', file, {
      params: { filename: file.name }, headers: { 'Content-Type': 'application/octet-stream' }, timeout: 60000,
    })
    return data
  },
  async analyzeCsv(options) {
    const { data } = await http.post('/csv/reports', options, { timeout: 90000 })
    return data
  },
  async me() {
    const { data } = await http.get('/me')
    return data
  },
  async options() {
    const { data } = await http.get('/options')
    return data
  },
  async listThreads() {
    const { data } = await http.get('/threads')
    return data
  },
  async getThread(threadId) {
    const { data } = await http.get(`/threads/${threadId}`)
    return data
  },
  async deleteThread(threadId) {
    await http.delete(`/threads/${threadId}`)
  },
  async listSkills() {
    const { data } = await http.get('/skills')
    return data
  },
  async uploadSkill(file, replace = false) {
    const { data } = await http.post('/skills/upload', file, {
      params: { filename: file.name, replace },
      headers: { 'Content-Type': 'application/octet-stream' },
      timeout: 60000,
    })
    return data
  },
  async importSkillUrl(url, replace = false) {
    const { data } = await http.post('/skills/import-url', { url, replace }, { timeout: 90000 })
    return data
  },
}

<script setup>
import { computed, onMounted, ref } from 'vue'
import { dashboardApi } from '../api/client.js'
import UiIcon from './UiIcon.vue'

const emit = defineEmits(['close'])
const skills = ref([])
const loading = ref(false)
const importing = ref(false)
const url = ref('')
const replace = ref(false)
const error = ref('')
const notice = ref('')
const fileInput = ref(null)
const dragging = ref(false)
const count = computed(() => skills.value.filter((skill) => skill.valid).length)

function errorText(reason) {
  const detail = reason.response?.data?.detail
  return typeof detail === 'string' ? detail : reason.message || 'Skill 导入失败'
}

async function reload() {
  loading.value = true
  try {
    const result = await dashboardApi.listSkills()
    skills.value = result.skills || []
  } catch (reason) {
    error.value = errorText(reason)
  } finally {
    loading.value = false
  }
}

async function chooseFiles(event) {
  const files = [...(event.target.files || [])]
  event.target.value = ''
  await uploadFiles(files)
}

function dropFiles(event) {
  dragging.value = false
  uploadFiles([...(event.dataTransfer?.files || [])])
}

async function uploadFiles(files) {
  if (!files.length || importing.value) return
  importing.value = true
  error.value = ''
  notice.value = ''
  const installed = []
  const failures = []
  try {
    for (const file of files) {
      if (file.size > 10 * 1024 * 1024) {
        failures.push(`${file.name}：文件不能超过 10 MB`)
        continue
      }
      try {
        const result = await dashboardApi.uploadSkill(file, replace.value)
        installed.push(...result.installed.map((skill) => skill.name))
      } catch (reason) {
        failures.push(`${file.name}：${errorText(reason)}`)
      }
    }
    if (installed.length) notice.value = `已导入：${installed.join('、')}。下一轮任务自动加载。`
    error.value = failures.join('\n')
    await reload()
  } finally {
    importing.value = false
  }
}

async function importUrl() {
  if (importing.value || !url.value.trim()) return
  importing.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await dashboardApi.importSkillUrl(url.value.trim(), replace.value)
    notice.value = `已导入：${result.installed.map((skill) => skill.name).join('、')}。下一轮任务自动加载。`
    url.value = ''
    await reload()
  } catch (reason) {
    error.value = errorText(reason)
  } finally {
    importing.value = false
  }
}

onMounted(reload)
</script>

<template>
  <div class="skill-overlay" @click.self="!importing && emit('close')" @keydown.esc="!importing && emit('close')">
    <section class="skill-manager" role="dialog" aria-modal="true" aria-labelledby="skills-title">
      <header>
        <div><h2 id="skills-title">Skill 管理</h2><p>现有功能继续保留，导入的技能在下一轮任务中按需使用。</p></div>
        <button type="button" :disabled="importing" aria-label="关闭 Skill 管理" @click="emit('close')">关闭</button>
      </header>
      <div class="skill-import">
        <h3>本地导入</h3>
        <p>选择文件后自动上传。支持 .zip、.skill 或 SKILL.md，每个文件不超过 10 MB。</p>
        <div class="skill-dropzone" :class="{ dragging }" @dragover.prevent="dragging = true" @dragleave.self="dragging = false" @drop.prevent="dropFiles">
          <span class="upload-illustration"><UiIcon name="upload" :size="25" /></span>
          <strong>拖拽 Skill 文件到这里</strong><span>或点击按钮选择文件，选择后自动上传</span>
          <button class="skill-choose" type="button" :disabled="importing" @click="fileInput?.click()">选择 Skill 文件</button>
          <input ref="fileInput" class="skill-file-input" aria-label="选择 Skill 文件，选择后自动上传" type="file" accept=".zip,.skill,.md" multiple :disabled="importing" @change="chooseFiles" />
        </div>
        <form @submit.prevent="importUrl">
          <label for="skill-url">网络导入</label>
          <div class="skill-url-row">
            <input id="skill-url" v-model="url" type="url" placeholder="https://example.com/my-skill.zip" :disabled="importing" required />
            <button :disabled="importing || !url.trim()" type="submit">下载并导入</button>
          </div>
          <p>填写公开可下载的压缩包或 SKILL.md 链接，仓库首页链接不能直接导入。</p>
        </form>
        <label class="skill-replace"><input v-model="replace" type="checkbox" :disabled="importing" />覆盖同名技能（默认保留已有版本）</label>
        <p v-if="importing" role="status">正在导入，请稍候…</p>
        <p v-if="notice" class="skill-success" role="status">{{ notice }}</p>
        <p v-if="error" class="skill-error" role="alert">{{ error }}</p>
      </div>
      <div class="skill-list-header"><h3>已安装 {{ count }} 项</h3><button type="button" :disabled="loading || importing" @click="reload">刷新列表</button></div>
      <p v-if="loading" role="status">正在读取技能…</p>
      <p v-else-if="!skills.length">暂无技能，请先导入。</p>
      <ul class="skill-list">
        <li v-for="skill in skills" :key="skill.path">
          <strong>{{ skill.name }}</strong>
          <span :class="skill.valid ? 'skill-badge' : 'skill-error'">{{ skill.valid ? '可用' : '格式待修复' }}</span>
          <p>{{ skill.description || skill.error }}</p>
          <code>{{ skill.path }}</code>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.skill-dropzone { display: flex; flex-direction: column; align-items: center; gap: 9px; padding: 25px 15px; border: 1px dashed #c7c6e6; border-radius: 12px; background: white; text-align: center; }
.skill-dropzone.dragging { border-color: var(--accent); background: var(--accent-soft); }
.upload-illustration { display: grid; place-items: center; width: 48px; height: 48px; margin-bottom: 4px; border-radius: 14px; background: var(--accent-soft); color: var(--accent); }
.skill-dropzone > span:not(.upload-illustration) { font-size: 12px; color: var(--muted); }
.skill-file-input { display: none; }
.skill-dropzone .skill-choose { margin-top: 6px; background: var(--accent); color: white; border-color: var(--accent); }
.skill-overlay { position: fixed; inset: 0; z-index: 100; display: flex; align-items: center; justify-content: center; padding: 20px; background: #13232d66; }
.skill-manager { width: min(760px, 100%); max-height: 90vh; overflow: auto; padding: 24px; border-radius: 14px; background: white; box-shadow: 0 12px 60px #0003; }
header, .skill-list-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
h2, h3 { margin: 0 0 8px; }
p { margin: 8px 0 14px; color: var(--muted); font-size: 13px; line-height: 1.6; }
button { flex-shrink: 0; padding: 8px 12px; border: 1px solid var(--border); border-radius: 7px; background: white; color: var(--accent); cursor: pointer; }
button:disabled { cursor: wait; opacity: .55; }
.skill-import { margin: 18px 0 22px; padding: 18px; border: 1px solid var(--border); border-radius: 10px; background: #f7fafb; }
form { margin-top: 22px; }
label { display: block; font-size: 14px; font-weight: 600; }
.skill-url-row { display: flex; gap: 8px; margin-top: 8px; }
.skill-url-row input { min-width: 0; flex: 1; padding: 10px; border: 1px solid var(--border); border-radius: 7px; font: inherit; font-size: 13px; }
.skill-replace { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: normal; }
.skill-list { padding: 0; list-style: none; }
.skill-list li { padding: 16px 0; border-bottom: 1px solid var(--border); }
.skill-list code { font-size: 12px; overflow-wrap: anywhere; }
.skill-badge { margin-left: 10px; padding: 2px 7px; color: #25834b; background: #eaf7ee; border-radius: 5px; font-size: 12px; }
.skill-success { color: #25834b; }
.skill-error { color: var(--danger); white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 550px) { .skill-overlay { padding: 10px; } .skill-manager { padding: 16px; } .skill-url-row { flex-direction: column; } }
</style>

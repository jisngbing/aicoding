<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { dashboardApi } from '../api/client.js'
import { csvAnalysisOptions, csvAnalysisPrompt } from '../lib/csvAnalytics.js'
import UiIcon from './UiIcon.vue'

const emit = defineEmits(['close', 'analyze', 'generated'])
const input = ref(null)
const upload = ref(null)
const filename = ref('')
const fields = ref({})
const busy = ref(false)
const error = ref('')
const report = ref(null)
const settings = reactive({ start: '', end: '', currency: 'CNY', granularity: 'auto', assumePaid: false, assumeNoRefunds: false })
const labels = {
  order_id: '订单号 *', paid_date: '支付日期 *', paid_amount: '实收金额 *',
  refund_amount: '退款金额', status: '订单状态', line_id: '订单明细ID',
  buyer_id: '买家ID', product_id: '商品ID', product_name: '商品名称',
  quantity: '商品件数', currency: '币种', category: '品类', channel: '销售渠道',
}
const summaryLabels = { paid: '实收销售额', refund: '订单归属退款', net: '净销售额', orders: '支付订单数', buyers: '购买人数', aov: '实收客单价', quantity: '支付件数' }
const options = computed(() => upload.value ? csvAnalysisOptions(upload.value, fields.value, settings) : null)
const ready = computed(() => upload.value && fields.value.order_id && fields.value.paid_date && fields.value.paid_amount && !busy.value)
watch([fields, settings], () => { report.value = null }, { deep: true })

function message(reason) {
  return typeof reason.response?.data?.detail === 'string' ? reason.response.data.detail : reason.message || 'CSV处理失败'
}
async function selectFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || busy.value) return
  error.value = ''; report.value = null; upload.value = null; fields.value = {}
  if (!file.name.toLowerCase().endsWith('.csv') || file.size > 10 * 1024 * 1024 || !file.size) {
    error.value = '请选择非空CSV文件，最大10MB。Excel文件请先导出CSV。'
    return
  }
  busy.value = true
  try {
    const result = await dashboardApi.uploadCsv(file)
    upload.value = result; fields.value = result.field_map || {}; filename.value = file.name
  } catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
async function generate() {
  if (!ready.value) return
  busy.value = true; error.value = ''; report.value = null
  try { report.value = await dashboardApi.analyzeCsv(options.value); emit('generated', report.value) }
  catch (reason) { error.value = message(reason) }
  finally { busy.value = false }
}
function delegate() {
  if (!ready.value) return
  emit('analyze', csvAnalysisPrompt(options.value))
}
function display(key, value) {
  if (value == null) return '不可计算'
  return ['orders', 'buyers', 'quantity'].includes(key) ? value : `${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${report.value.currency}`
}
</script>

<template>
  <div class="csv-overlay" @click.self="!busy && emit('close')" @keydown.esc="!busy && emit('close')">
    <section class="csv-dialog" role="dialog" aria-modal="true" aria-labelledby="csv-title">
      <header><div><h2 id="csv-title">CSV 销售统计</h2><p>上传订单、匹配字段，生成销售分析看板</p></div><button class="icon-button" :disabled="busy" aria-label="关闭CSV统计" @click="emit('close')"><UiIcon name="close" /></button></header>
      <div class="csv-body">
        <input ref="input" type="file" accept=".csv,text/csv" hidden @change="selectFile" />
        <button class="csv-upload" :disabled="busy" @click="input?.click()"><UiIcon name="upload" /><strong>{{ upload ? '更换订单CSV' : '选择订单CSV' }}</strong><span>最多10MB / 50000行 · UTF-8、GB18030</span></button>
        <p v-if="busy" role="status">正在处理CSV，请稍候…</p>
        <template v-if="upload">
          <p class="csv-file">{{ filename }} · {{ upload.row_count }} 条明细 · {{ upload.encoding }}</p>
          <h3>字段匹配</h3><p class="csv-hint">* 为必需字段。金额必须是本行实收，订单总额不能在商品明细上重复填写。</p>
          <div class="csv-fields"><label v-for="(label, key) in labels" :key="key"><span>{{ label }}</span><select v-model="fields[key]" :disabled="busy"><option value="">未提供</option><option v-for="column in upload.columns" :key="column" :value="column">{{ column }}</option></select></label></div>
          <div class="csv-fields csv-options">
            <label>开始日期<input v-model="settings.start" type="date" :disabled="busy" /></label><label>结束日期<input v-model="settings.end" type="date" :disabled="busy" /></label>
            <label>统计币种<input v-model="settings.currency" maxlength="3" :disabled="busy" /></label>
            <label>数据粒度<select v-model="settings.granularity" :disabled="busy"><option value="auto">自动识别</option><option value="order">一行一订单（订单实收）</option><option value="line">一行一商品（商品实收）</option></select></label>
          </div>
          <p class="csv-hint">日期留空时按数据范围。支付时间统一按 Asia/Shanghai 处理；退款归属原支付日。</p>
          <label v-if="!fields.status" class="csv-check"><input v-model="settings.assumePaid" type="checkbox" :disabled="busy" />我确认文件中所有记录均已支付</label>
          <label v-if="!fields.refund_amount" class="csv-check"><input v-model="settings.assumeNoRefunds" type="checkbox" :disabled="busy" />我确认这些订单没有退款（否则请补充退款金额列）</label>
        </template>
        <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
        <section v-if="report" class="csv-result"><h3>看板已生成</h3><div class="csv-cards"><div v-for="(value, key) in report.summary" :key="key"><small>{{ summaryLabels[key] }}</small><strong>{{ display(key, value) }}</strong></div></div><p>纳入 {{ report.included_lines }} 行，排除 {{ report.excluded_lines }} 行。</p><ul><li v-for="note in report.notes" :key="note">{{ note }}</li></ul><div class="csv-links"><a :href="report.artifacts.dashboard" target="_blank" rel="noopener noreferrer">打开统计看板 ↗</a><a :href="report.artifacts.summary_csv">下载汇总CSV</a><a :href="report.artifacts.products_csv">下载商品明细</a><a :href="report.artifacts.daily_csv">下载日统计</a></div></section>
      </div>
      <footer><button class="secondary-button" :disabled="!ready" @click="delegate">填入对话，让智能体分析</button><button class="primary-button" :disabled="!ready" @click="generate">{{ busy ? '处理中…' : '生成统计看板' }}</button></footer>
    </section>
  </div>
</template>

<style scoped>
.csv-overlay { position:fixed; inset:0; z-index:90; background:#15182b80; display:grid; place-items:center; padding:20px; }
.csv-dialog { display:flex; flex-direction:column; width:min(840px,100%); max-height:90dvh; background:var(--surface,#fff); color:var(--text,#19233b); border-radius:18px; box-shadow:0 20px 80px #0003; }
header,footer { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:20px 24px; flex-shrink:0; }header { border-bottom:1px solid var(--border,#e5e7eb); }footer { border-top:1px solid var(--border,#e5e7eb); }
h2 { font-size:20px; margin:0 0 6px; }h3 { font-size:15px; margin:20px 0 8px; }header p,.csv-hint,.csv-file,.csv-result p,li { color:var(--muted,#64748b); font-size:12px; line-height:1.7; }header p { margin:0; }.csv-body { overflow:auto; padding:20px 24px; }.csv-upload { display:flex; align-items:center; justify-content:center; gap:12px; flex-wrap:wrap; width:100%; padding:24px 12px; border:1px dashed var(--accent,#6d5ce7); border-radius:12px; background:var(--accent-soft,#f4f2ff); color:var(--text,#19233b); cursor:pointer; }.csv-upload span { font-size:11px; color:var(--muted,#64748b); }
.csv-fields { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }.csv-fields label { display:flex; flex-direction:column; gap:6px; min-width:0; font-size:12px; }.csv-fields select,.csv-fields input { width:100%; min-width:0; border:1px solid var(--border,#dbe0e9); border-radius:8px; padding:9px; background:var(--surface,#fff); color:inherit; }.csv-options { grid-template-columns:repeat(2,minmax(0,1fr)); margin-top:22px; }.csv-check { display:flex; gap:8px; align-items:center; font-size:12px; margin:12px 0; }.csv-check input { flex-shrink:0; }
.primary-button { border:0; border-radius:9px; padding:10px 16px; background:var(--accent,#6d5ce7); color:#fff; cursor:pointer; }.csv-dialog button:disabled { opacity:.5; cursor:not-allowed; }.csv-result { border-top:1px solid var(--border,#e5e7eb); margin-top:20px; }.csv-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }.csv-cards > div { padding:13px; background:var(--accent-soft,#f4f2ff); border-radius:9px; }.csv-cards strong { display:block; margin-top:7px; font-size:16px; }.csv-cards small { color:var(--muted,#64748b); }.csv-links { display:flex; flex-wrap:wrap; gap:14px; font-size:13px; }.csv-links a { color:var(--accent,#6d5ce7); }.csv-result ul { padding-left:20px; }
@media(max-width:600px) { .csv-overlay { padding:10px; }.csv-dialog { max-height:95dvh; }header,footer,.csv-body { padding:16px; }.csv-fields { grid-template-columns:repeat(2,minmax(0,1fr)); }footer { flex-wrap:wrap; }footer button { flex:1; white-space:nowrap; font-size:12px; }h2 { font-size:18px; } }
</style>

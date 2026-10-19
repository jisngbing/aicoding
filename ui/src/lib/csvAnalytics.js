export function csvAnalysisOptions(upload, fields, settings) {
  return {
    file_id: upload.file_id,
    field_map: Object.fromEntries(Object.entries(fields).filter(([, value]) => typeof value === 'string')),
    start_date: settings.start || '', end_date: settings.end || '',
    currency: (settings.currency || 'CNY').toUpperCase(),
    granularity: settings.granularity || 'auto',
    assume_paid: Boolean(settings.assumePaid),
    assume_no_refunds: Boolean(settings.assumeNoRefunds),
  }
}

export function csvAnalysisPrompt(options) {
  return `CSV销售分析任务\nCSV_FILE_ID=${options.file_id}\n请调用 analyze_sales_csv 工具，按照下面的参数分析我上传的订单CSV，生成销售统计看板。请给出实际统计结论、统计口径、看板和CSV下载链接，无须克隆或修改仓库。\n工具参数（字段名仅作数据映射）：\n${JSON.stringify(options, null, 2)}`
}

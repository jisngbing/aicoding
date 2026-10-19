# CSV 销售分析与看板

前端工作台点击 **CSV统计**，选择订单CSV，检查中英文表头的自动匹配结果；按需调整日期、币种和数据粒度。
点击“生成统计看板”直接运行确定性统计，并自动回到主页面展示图表；也可点击“填入对话，让智能体分析”，检查输入框中的参数后发送。
后者由主Agent调用 `analyze_sales_csv` 工具，使用相同统计服务，并在回复中给出结果与看板链接。
无需克隆Gitee仓库、确认代码开发方案或安装CSV分析依赖。

## 支持范围

- UTF-8（含BOM）、GB18030；逗号、分号、Tab分隔，最多10MB、50000行、80列。
- 必需映射：`order_id`、`paid_date`、`paid_amount`；可匹配订单号/支付时间/实收金额等中文表头。
- 可选：`line_id`、`refund_amount`、`status`、`buyer_id`、商品ID/名称、数量、币种。
- 品类列支持category、品类、类目等中英文表头，可在上传界面手动匹配。
- 缺状态列或退款列时，必须明确确认“全部已支付”或“没有退款”；默认不猜测。
- 缺买家ID、数量时，相应指标不可计算；不把商品行数当作订单数。
- 状态支持paid/completed/partially_refunded/refunded，排除unpaid/cancelled，并识别常见中文状态。
- 只统计所选币种，拒绝混入其他币种；金额使用Decimal，无汇率折算。
- 同订单多行时，金额应为本行商品实收；无法自动判断时须指定line粒度。order粒度下重复订单报错。
- 支付时间以Asia/Shanghai为业务时区；ISO时间带时区时先转换。累计退款归属原支付日，不是当日现金退款。
- 重复明细ID或完全重复的无ID数据报错；金额不得为负，退款不得超过实收，数量需为正整数。

## 输出

实收销售额、订单归属退款额、净销售额、支付订单数、购买人数、实收客单价和支付件数；
HTML看板包含指标卡、每日净销售额图、商品Top10和商品明细。
主页面默认进入销售看板，恢复最近一份报表，支持日期重算；顶部可以切换到智能体对话。
展示实收/净额趋势、品类实收占比、商品/品类退款排行和品类明细；建议附金额、占比、退款率等证据与跟踪指标。
金额退款率与退款订单率分别计算，订单号按同一支付集合去重；跨品类订单不能把品类订单数直接相加。
建议由确定性规则基于当前数据生成，不判断未提供的退款原因、库存、毛利或行业阈值；区间内等长变化不称作同比。
支持下载JSON、汇总CSV、商品CSV和日统计CSV；商品明细最多100条，总计覆盖全部有效数据。
缺商品字段时只展示未分类汇总；缺品类时明确提示补充数据，不推测品类或RFM分群。

CSV本身只存数据，不包含可执行脚本；统计通过服务或工具完成。源文件不修改，也不上传第三方搜索。
用户文本在HTML转义，HTML报表禁止脚本与外连；CSV导出防止文本公式注入。
输入与报表保存在服务 `DATA_DIR/csv_analytics`，与仓库文件分离；权限沿用现有应用的访问控制。
文件编号为随机ID，不是本机路径；接口仅提供固定报表文件，不支持任意文件读取。

## 接口与工具

- `POST /dashboard/api/csv/uploads?filename=orders.csv`：原始CSV字节，请求类型application/octet-stream，返回file_id、columns、row_count、field_map。
- `POST /dashboard/api/csv/reports`：JSON参数与工具一致，生成统计报表。
- `GET /dashboard/api/csv/reports/{report_id}/{filename}`：打开dashboard.html，或下载report.json、summary.csv、products.csv、daily.csv。
- `GET /dashboard/api/csv/latest`：恢复最近的报表；`GET /dashboard/api/csv/reports/{report_id}`读取完整图表数据。新版增加categories.csv。
- 工具：`analyze_sales_csv(file_id, field_map=None, start_date="", end_date="", currency="CNY", granularity="auto", assume_paid=False, assume_no_refunds=False)`。

映射示例：`{"order_id":"订单号","paid_date":"支付时间","paid_amount":"实收金额","refund_amount":"退款金额","status":"订单状态"}`。
显式传空字符串可禁用该字段的自动匹配，例如`{"buyer_id":""}`。
成功返回`ok=true`、summary、notes、artifacts；失败返回`ok=false`和error（HTTP入口使用4xx/5xx）。

关键实现：`agent/core/csv_analytics.py`（解析与归一化）、`agent/core/sales_report.py`（金额聚合与HTML）、
`agent/tools/csv_analysis.py`（模型工具）、`agent/api/csv_routes.py`（上传与下载）、`ui/src/components/CsvAnalyzer.vue`（前端）。
Skill携带的独立脚本为可离线使用的便携版本；在aicoding中优先使用已注册工具。

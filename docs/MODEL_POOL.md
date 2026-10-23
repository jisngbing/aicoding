# 多模型连接池

默认仍使用现有千问配置。主任务、子 Agent、意图识别及评测通过统一连接池创建模型，
消息和 Langfuse 回调仍由每次调用传入，不把会话消息存在池中。

在项目 .env 配置主模型及备用顺序。默认优先主模型，同一问题固定节点：

```dotenv
MODEL_POOL_PRIMARY=qwen
MODEL_POOL_STRATEGY=primary
MODEL_POOL_MAIN=qwen,deepseek,doubao,zhipu
MODEL_POOL_RECOVERY_SECONDS=60
# 可选：单独指定某角色的节点范围；不设置则跟随 MODEL_POOL_MAIN。
# MODEL_POOL_SUBAGENT=qwen,deepseek,doubao,zhipu
# MODEL_POOL_INTENT=qwen,deepseek,doubao,zhipu
# MODEL_POOL_EVALUATION=qwen,deepseek,doubao,zhipu
MODEL_POOL_MAX_CONNECTIONS=20
MODEL_POOL_MAX_KEEPALIVE=10
MODEL_POOL_WAIT_TIMEOUT=10
MODEL_POOL_REQUEST_TIMEOUT=120

DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MAX_TOKENS=4096
ARK_API_KEY=
DOUBAO_MODEL=
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
DOUBAO_MAX_TOKENS=4096
```

例如 `MODEL_POOL_MAIN=qwen,deepseek,doubao` 表示千问优先，故障时依次使用已配置的后备。
只有显式设置 `MODEL_POOL_STRATEGY=round_robin` 才轮询，当前项目设置为主模型优先。
缺少密钥或模型名的节点不参与选择；路由拼错会报错。豆包模型填写方舟控制台实际开通的
模型 ID 或推理接入点 ID，不预填一个未开通的型号。使用 Coding Plan 时需自行配置对应地址。
模型是否支持工具调用、JSON 结构化输出取决于所选型号，编码与意图角色应选支持这些能力的模型。
现有 INTENT_MODEL、EVAL_MODEL 仍可覆盖角色的模型名；切换厂商时需同步修改或清空旧覆盖值。

更多账号或模型可使用自定义节点，配置仅引用密钥变量，不在 JSON 中明文放密钥：

```dotenv
MODEL_POOL_NODES='[{"id":"deepseek-secondary","provider":"deepseek","model":"deepseek-flash","base_url":"https://api.deepseek.com","api_key_env":"DEEPSEEK_SECONDARY_API_KEY"}]'
MODEL_POOL_MAIN=deepseek,deepseek-secondary
DEEPSEEK_SECONDARY_API_KEY=
```

连接池按地址、凭据和连接限制隔离；同地址不同模型可复用网络连接。
同步连接由 HTTPX 线程安全池管理；异步连接按事件循环隔离，循环正常结束时释放。
HTTP/1.1 每池最多20条连接；流式回答占用连接直到响应关闭，满池最多等待10秒。
这是每个进程、每个池的连接上限，不是整个集群的 QPS 或 Token 限流。
模型包装器最多缓存64种配置，避免配置不断变化造成内存增长。配置或密钥变更后建议重启。
服务退出时统一释放连接；退出前应停止新任务。强制杀进程由操作系统回收套接字。

不在已经输出部分答案后自动换模型重放，避免重复输出和重复工具执行。
当前包含进程内熔断及后续请求切换，不包含分布式限流或跨进程共享健康状态。
新增厂商的真实联网验证需要配置其密钥，本次测试使用模拟 HTTP，不产生厂商调用费用。

官方协议参考：[DeepSeek](https://api-docs.deepseek.com/)、[豆包方舟](https://docs.volcengine.com/docs/ark/compatible-with-openai-sdk?lang=zh)。

## 主模型超时与半开恢复

正常时优先 `MODEL_POOL_PRIMARY=qwen`。账户额度耗尽、调用超时、网络请求失败或模型服务内部故障后，
本次问答返回“调用模型异常”，关闭 SDK 自动重试，也不回退关键词分类继续完成本轮。
已经输出部分回答时会保留已发送文本并发出错误，不从其他模型补接或重放工具。

从失败时刻起等待 `MODEL_POOL_RECOVERY_SECONDS` 秒，后续调用按顺序使用其他可用节点。
没有配置密钥、没有模型 ID、仍处于等待期的节点会跳过。没有任何可用节点时直接报错。

等待结束不会主动产生一条收费探测请求，而是在后续真实调用中只允许一个请求试用主模型：

- 等待期（OPEN，熔断）：全部避开故障主模型。
- 半开（HALF_OPEN）：只有持有探测票据的请求调用主模型，其余并发请求继续使用备用。
- 探测完成且 SDK 成功解析响应：恢复主模型正常流量（CLOSED，正常可用）。
- 探测失败：重新等待配置时间，下次到期再半开；本次仍直接报错。

成功恢复正常流量是业务上的“全量放开”，标准熔断器术语称 CLOSED；OPEN 表示熔断阻断。
探测票据绑定问题及一次探测代次，旧请求迟到的成功不能关闭新一轮熔断。
流式探测须正常结束才判成功，仅收到 HTTP 200 或第一个 token 不算恢复。
若探测尚未调用就被取消，或进程中的任务异常中断，探测租约在请求超时加池等待超时后失效，
随后重新等待，防止节点永远卡在半开。健康状态按地址和凭据隔离，仅在当前进程保存，重启后重置。

## 错误码分类与限流退避

错误定义统一位于 `agent/core/models/model_error_codes.py`，包括系统码、用户提示、是否重试、
是否熔断、5/15/30秒间隔，以及厂商业务码映射和官方来源。
`model_errors.py` 只负责读取响应、分类和异常转换，`model_failover.py` 执行策略。

| 系统错误码 | 含义 |
|---|---|
| MODEL_RATE_LIMITED | 限流；同模型退避，最终提示稍后重试 |
| MODEL_QUOTA_EXHAUSTED | 额度耗尽或欠费；后续走后备模型 |
| MODEL_CALL_FAILED | 超时、网络或服务故障；后续走后备模型 |
| MODEL_AUTH_FAILED | 鉴权或权限配置错误 |
| MODEL_CONTEXT_TOO_LONG | 输入超出上下文限制 |
| MODEL_INVALID_REQUEST | 其他请求参数错误 |

SSE 的 `error_code` 返回上述系统码，`provider_error_code` 保留厂商码，两者分别用于业务处理和诊断。

| 分类 | 本次调用 | 影响后续模型选择 |
|---|---|---|
| 账户 Token/消费额度耗尽、欠费 | 直接返回额度耗尽提示，不重试 | 熔断该节点，等待期及半开机制保持不变 |
| 网络/读取超时、HTTP 408/5xx 模型内部故障 | 直接返回调用模型异常，不重试 | 熔断该节点 |
| RPM/TPM/并发限流 | 同一个模型依次等待5、15、30秒，额外重试3次 | 不因限流切换或熔断 |
| 参数、鉴权、访问权限、输入上下文过长 | 直接返回对应提示，不重试 | 不误判为模型故障，不切换 |

限流是“首次调用 + 3次重试”，最多4次网络请求，等待合计50秒。
每次退避前关闭失败响应，归还池连接；异步路径使用 asyncio.sleep，可取消且不阻塞事件循环。
SDK 自带重试仍关闭，避免两层重试叠加。任一次成功立即结束退避；重试期间出现额度耗尽或其他错误，
立即按新错误类别终止。三次重试后仍限流，页面显示“模型服务繁忙，重试3次仍失败，请稍后重试”。
SSE 错误事件额外包含 error_type、provider_error_code、http_status、retry_count，方便接口调用方判断。
不向用户展示上游原始文案，避免其中包含请求内容、鉴权信息等敏感数据。

厂商映射优先于 HTTP 状态：

- 千问：429/Throttling.RateQuota、Throttling.AllocationQuota、insufficient_quota 按速率/TPM限流；
  403/Arrearage、AllocationQuota.FreeTierOnly，以及明确的免费/周期额度耗尽按额度类。
- DeepSeek：402余额不足按额度；429按限流；500/503按调用故障。
- 豆包：403/AccountOverdueError、429/AccountQuotaExceeded、每日 Token 配额等按额度；
  AccountRateLimitExceeded、并发/排队限流按退避。QuotaExceeded 同码多义，结合官方文案的额度/排队语义区分。
- 智谱：429/1113欠费、1308/1310及周期消费上限按额度；1302/1305/1313按限流；1261按输入过长。

“账户 Token 额度耗尽”与生成结束 finish_reason=length 不同。后者只说明本次输出到达长度上限，
不能据此认定账号没额度或切换厂商。已开始输出的 SSE 不重放，避免重复答案及工具动作；
如果厂商在流式输出后报限流，直接提示稍后重试，不承诺已经执行过3次重试。

官方错误码：[千问](https://help.aliyun.com/zh/model-studio/error-code)、
[DeepSeek](https://api-docs.deepseek.com/quick_start/error_codes/)、
[豆包](https://console.volcengine.com/ark/region%3Acn-beijing/docs/error-codes)、
[智谱](https://docs.bigmodel.cn/cn/api/api-code)。

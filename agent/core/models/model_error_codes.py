"""大模型错误码的唯一配置入口。

本文件只定义规则，不发请求、不读取密钥，也不改变熔断状态。
增加厂商业务码或调整用户提示时优先修改这里，解析、重试和 SSE 层共同复用。
系统码用于前端/业务判断，厂商码仅用于精确分类和排障，二者不能混为一谈。
"""
from dataclasses import dataclass
from enum import Enum


class ModelErrorCode(str, Enum):
    """对外稳定系统错误码；不依赖具体厂商 HTTP 或业务码。"""
    RATE_LIMITED = 'MODEL_RATE_LIMITED'
    QUOTA_EXHAUSTED = 'MODEL_QUOTA_EXHAUSTED'
    CALL_FAILED = 'MODEL_CALL_FAILED'
    AUTH_FAILED = 'MODEL_AUTH_FAILED'
    CONTEXT_TOO_LONG = 'MODEL_CONTEXT_TOO_LONG'
    INVALID_REQUEST = 'MODEL_INVALID_REQUEST'


@dataclass(frozen=True)
class ErrorDefinition:
    """错误的用户提示及处理策略，所有调用路径必须遵循同一份定义。"""
    code: ModelErrorCode
    message: str
    retryable: bool = False
    trip_circuit: bool = False
    exhausted_message: str = ''


# 首次调用不计入重试次数，因此下列三个间隔对应最多四次网络请求。
RETRY_DELAYS = (5, 15, 30)
ERROR_DEFINITIONS = {
    'rate_limit': ErrorDefinition(ModelErrorCode.RATE_LIMITED, '模型服务繁忙，请稍后重试',
        retryable=True, exhausted_message='模型服务繁忙，重试3次仍失败，请稍后重试'),
    'quota': ErrorDefinition(ModelErrorCode.QUOTA_EXHAUSTED, '模型额度已耗尽，调用模型异常', trip_circuit=True),
    'call_failure': ErrorDefinition(ModelErrorCode.CALL_FAILED, '调用模型异常', retryable=True, trip_circuit=True,
        exhausted_message='调用模型异常，重试3次仍失败，请稍后重试'),
    'auth': ErrorDefinition(ModelErrorCode.AUTH_FAILED, '模型鉴权或访问权限异常，请检查模型配置'),
    'context_limit': ErrorDefinition(ModelErrorCode.CONTEXT_TOO_LONG, '输入超出模型上下文限制，请缩短内容后重试'),
    'bad_request': ErrorDefinition(ModelErrorCode.INVALID_REQUEST, '模型请求参数异常，请检查请求配置'),
}

# 键统一小写。厂商映射优先，尤其不能把千问的 insufficient_quota 误判为余额耗尽。
VENDOR_CODES = {
    'qwen': {
        'arrearage': 'quota', 'allocationquota.freetieronly': 'quota',
        'insufficient_quota': 'rate_limit', 'throttling.ratequota': 'rate_limit',
        'throttling.allocationquota': 'rate_limit', 'throttling.burstrate': 'rate_limit',
    },
    'doubao': {
        'accountoverdueerror': 'quota', 'accountquotaexceeded': 'quota',
        'modelaccounttpdratelimitexceeded': 'quota', 'quotaexceeded.agentplanquotaexceeded': 'quota',
        'accountratelimitexceeded': 'rate_limit', 'modelaccountrpmlimitexceeded': 'rate_limit',
        'requestbursttoofast': 'rate_limit', 'serveroverloaded': 'rate_limit',
    },
    'zhipu': {
        **{code: 'quota' for code in ('1113', '1308', '1309', '1310', '1314', '1316', '1317', '1318', '1319', '1320', '1321')},
        **{code: 'rate_limit' for code in ('1302', '1305', '1313')},
        '1311': 'auth', '1315': 'auth', '1261': 'context_limit',
    },
    'deepseek': {},  # DeepSeek 主要按 HTTP 状态区分402余额不足及429限流。
}

COMMON_CODES = {
    'insufficient_quota': 'quota',
    'context_length_exceeded': 'context_limit', 'max_context_length_exceeded': 'context_limit', 'inputtoolong': 'context_limit',
    'rate_limit_exceeded': 'rate_limit', 'rate_limit_error': 'rate_limit',
    'ratelimitexceeded': 'rate_limit', 'limit_requests': 'rate_limit',
}
HTTP_CATEGORIES = {401: 'auth', 402: 'quota', 403: 'auth', 408: 'call_failure', 429: 'rate_limit'}
QUOTA_HTTP_STATUSES = frozenset({402})
SERVER_ERROR_MIN_STATUS = 500
RATE_CODE_PREFIXES = ('throttling.',)

# 少数厂商同一码存在多个含义，只在该业务码下检查官方文案，不能全局模糊匹配 quota。
AMBIGUOUS_QUOTA_CODE = 'quotaexceeded'
QUEUE_HINTS = ('queued', 'concurrency', 'concurrent')
QUOTA_HINTS = ('free trial', 'usage quota', 'monthly', 'weekly', 'exhausted')
QWEN_PERIOD_QUOTA_HINTS = ('free allocated quota exceeded', 'hour allocated quota exceeded',
                          'week allocated quota exceeded', 'month allocated quota exceeded')

# 官方依据：新增规则应同时补充测试，避免 HTTP429 的额度与限流混淆。
ERROR_CODE_SOURCES = {
    'qwen': 'https://help.aliyun.com/zh/model-studio/error-code',
    'deepseek': 'https://api-docs.deepseek.com/quick_start/error_codes/',
    'doubao': 'https://console.volcengine.com/ark/region%3Acn-beijing/docs/error-codes',
    'zhipu': 'https://docs.bigmodel.cn/cn/api/api-code',
}

"""厂商错误码归一化：区分限流、账户额度、模型故障及请求自身错误。

不能把所有429归为限流，也不能把 insufficient_quota 一律当作账户没钱。
先判断厂商业务码，再回退到 HTTP 状态码；不使用上游原始文案直接展示，避免泄密。
"""
from dataclasses import dataclass, replace

from openai import APIConnectionError, APIStatusError, APITimeoutError, APIError
from agent.core.models.model_error_codes import (
    ERROR_DEFINITIONS, RETRY_DELAYS, VENDOR_CODES, COMMON_CODES, HTTP_CATEGORIES,
    RATE_CODE_PREFIXES, AMBIGUOUS_QUOTA_CODE, QUEUE_HINTS, QUOTA_HINTS, QWEN_PERIOD_QUOTA_HINTS,
    QUOTA_HTTP_STATUSES, SERVER_ERROR_MIN_STATUS,
)


@dataclass(frozen=True)
class ErrorInfo:
    category: str
    status: int | None = None
    code: str = ''
    retries: int = 0

    @property
    def definition(self):
        return ERROR_DEFINITIONS[self.category]

    @property
    def error_code(self):
        return self.definition.code.value

    @property
    def retryable(self):
        return self.definition.retryable

    @property
    def trip_circuit(self):
        return self.definition.trip_circuit

    @property
    def message(self):
        definition = self.definition
        return definition.exhausted_message if definition.retryable and self.retries >= len(RETRY_DELAYS) else definition.message


class ModelRequestError(RuntimeError):
    """保留可安全展示的错误类别，阻止 Agent 内部忽略异常后继续请求本轮模型。"""
    def __init__(self, info):
        self.info = info
        super().__init__(info.message)


def classify(status, body, provider=''):
    """兼容 error.code、顶层 code 和 SDK 已剥离 error 层的正文结构。"""
    data = body if isinstance(body, dict) else {}
    error = data.get('error', data)
    error = error if isinstance(error, dict) else {}
    raw = str(error.get('code') or error.get('type') or '')[:128]
    code = raw.lower()
    message = str(error.get('message', '')).lower()
    def result(kind):
        return ErrorInfo(kind, status, raw)

    # 厂商明确的欠费、试用耗尽、周期消费上限优先于429的默认含义。
    if status in QUOTA_HTTP_STATUSES:
        return result('quota')
    # 同一码的文案细分必须在普通映射之前，否则周期额度可能被误归为瞬时限流。
    if provider == 'qwen' and any(word in message for word in QWEN_PERIOD_QUOTA_HINTS):
        return result('quota')
    if code == AMBIGUOUS_QUOTA_CODE and any(word in message for word in QUEUE_HINTS):
        return result('rate_limit')
    if code == AMBIGUOUS_QUOTA_CODE and any(word in message for word in QUOTA_HINTS):
        return result('quota')
    category = VENDOR_CODES.get(provider, {}).get(code) or COMMON_CODES.get(code)
    if category:
        return result(category)
    if status in HTTP_CATEGORIES:
        return result(HTTP_CATEGORIES[status])
    if code.startswith(RATE_CODE_PREFIXES):
        return result('rate_limit')
    if status is not None and status >= SERVER_ERROR_MIN_STATUS:
        return result('call_failure')
    return result('bad_request')


def exception_info(exc, provider=''):
    """先读取应用异常，再读取 SDK 状态码，避免外层连接异常掩盖内部限流分类。"""
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen:
        chain.append(exc); seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    for error in chain:
        if isinstance(error, ModelRequestError):
            return error.info
    for error in chain:
        if isinstance(error, APIStatusError):
            return classify(error.status_code, error.body, provider)
    for error in chain:
        # 流式推理可能以 SSE 错误事件终止，而不是返回新的 HTTP 错误状态。
        if isinstance(error, APIError) and isinstance(error.body, dict) and error.body:
            return classify(None, error.body, provider)
    if any(isinstance(error, (APITimeoutError, APIConnectionError)) for error in chain):
        return ErrorInfo('call_failure')
    return None


def remember_error(info):
    """本轮保存结构化错误，SSE 及意图分类统一读取；后台调用没有上下文则跳过。"""
    from agent.core.answer_feedback import current_question
    context = current_question.get()
    if context is not None:
        previous = context.get('model_pool_error')
        if previous and previous.category == info.category:
            info = replace(info, retries=max(previous.retries, info.retries))
        context['model_pool_error'] = info


def request_error_info(exc):
    from agent.core.answer_feedback import current_question
    return (current_question.get() or {}).get('model_pool_error') or exception_info(exc)

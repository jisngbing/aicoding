"""模型基础设施包。

业务代码从本包取得模型；连接复用、厂商错误归一化和熔断恢复由内部模块负责。
模块分工：model 提供角色入口，model_pool 管理客户端与路由，
model_error_codes 定义错误码，model_errors 分类异常，model_failover 执行退避和熔断。
"""
from .model import get_main_model_name, make_intent_model, make_main_model

__all__ = ["get_main_model_name", "make_intent_model", "make_main_model"]

from .model import make_subagent_model
__all__.append("make_subagent_model")

"""模型创建的兼容入口：业务代码无需感知各厂商 SDK 差异。"""
from agent.core.models.model_pool import get_model, model_name

DEFAULT_MAIN_MODEL_MAX_TOKENS = 25600
INTENT_MODEL_MAX_TOKENS = 200


def get_main_model_name():
    """返回本轮实际选中的主模型名称，保留旧接口给日志及页面使用。"""
    return model_name("main")


def make_main_model():
    """取得主任务模型；同一问题固定节点，底层连接可跨请求复用。"""
    return get_model("main")


def make_subagent_model():
    """子 Agent 可独立配置路由，缺省跟随主任务路由。"""
    return get_model("subagent")


def make_intent_model():
    """意图分类默认低温度、短输出，复用池但不与主任务混用参数。"""
    return get_model("intent")

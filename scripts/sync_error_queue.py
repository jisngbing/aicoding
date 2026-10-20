"""手动创建或复用 err-queue，并补投一批历史负反馈；不调用大模型。

在项目根目录执行：.venv\\Scripts\\python.exe scripts\\sync_error_queue.py。
使用现有 MongoDB 和 Langfuse 配置，不修改 .env；每批最多投递20条。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.core.answer_feedback import collection
from agent.core.feedback_error_queue import (
    ensure_error_queue,
    sync_negative_feedback_queue,
)
from agent.core.observability import get_langfuse_client, shutdown_tracing


def main():
    """执行一次投递并输出汇总，便于在重启后台服务前进行验证。"""
    client = get_langfuse_client()
    if client is None:
        raise RuntimeError("Langfuse is not configured or tracing is disabled")
    try:
        # 手动脚本显式准备队列；常驻后台则仅在有待处理负反馈时才准备队列。
        target = ensure_error_queue(client)
        mongo = collection()
        sync_negative_feedback_queue()
        negatives = {"rating": "not_helpful"}
        # remaining 包括待重试及缺失 Trace 的记录，不代表这些记录已经确认有错误。
        # 仅输出数量和队列标识，不输出用户问题、反馈原文或连接凭据。
        print(json.dumps({
            "queue_name": target.name,
            "queue_id": target.id,
            "negative_feedback": mongo.count_documents(negatives),
            "synced": mongo.count_documents({**negatives, "err_queue_sync_status": "synced"}),
            "remaining": mongo.count_documents({**negatives, "err_queue_sync_status": {"$ne": "synced"}}),
        }, ensure_ascii=False), flush=True)
    finally:
        # 即使 MongoDB 或投递失败，也释放观测客户端并刷新待发送的追踪数据。
        shutdown_tracing()


if __name__ == "__main__":
    main()

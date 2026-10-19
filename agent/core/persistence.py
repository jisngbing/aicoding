from __future__ import annotations

import atexit
from functools import lru_cache
from threading import RLock

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.store.mongodb import MongoDBStore
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from agent.core import settings

_client_lock = RLock()


@lru_cache(maxsize=1)
def _connected_client() -> MongoClient:
    """共享连接池；连接失败时不在异常中暴露 URI 或认证信息。"""
    options = {
        "serverSelectionTimeoutMS": settings.MONGODB_TIMEOUT_MS,
        "connectTimeoutMS": settings.MONGODB_TIMEOUT_MS,
        "socketTimeoutMS": max(settings.MONGODB_TIMEOUT_MS, 30000),
        "appname": "aicoding",
    }
    if settings.MONGODB_USERNAME:
        options.update(
            username=settings.MONGODB_USERNAME,
            password=settings.MONGODB_PASSWORD,
            authSource=settings.MONGODB_AUTH_SOURCE,
        )
    client = None
    try:
        client = MongoClient(settings.MONGODB_URI, **options)
        client.admin.command("ping")
    except (PyMongoError, ValueError) as exc:
        if client is not None:
            client.close()
        raise RuntimeError(
            f"MongoDB 连接失败（{type(exc).__name__}），请检查服务地址、端口、认证库和账号权限。"
        ) from None
    atexit.register(client.close)
    return client


def get_mongo_client() -> MongoClient:
    with _client_lock:
        return _connected_client()


def make_checkpointer() -> MongoDBSaver:
    """保存会话状态和消息写入，保留 LangGraph checkpoint 协议。"""
    return MongoDBSaver(
        get_mongo_client(),
        db_name=settings.MONGODB_DATABASE,
        checkpoint_collection_name=settings.MONGODB_CHECKPOINT_COLLECTION,
        writes_collection_name=settings.MONGODB_WRITES_COLLECTION,
    )


def make_langgraph_store() -> MongoDBStore:
    """为 /memories/ 提供官方 MongoDB Store，不启用向量检索或自动过期。"""
    collection = get_mongo_client()[settings.MONGODB_DATABASE][settings.MONGODB_MEMORY_COLLECTION]
    return MongoDBStore(collection)

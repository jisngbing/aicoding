"""Copy legacy SQLite memories/checkpoints to MongoDB without deleting source data."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.sqlite import SqliteSaver
from pymongo import UpdateOne

from agent.core.persistence import make_checkpointer, make_langgraph_store
from agent.core.settings import CHECKPOINT_DB_PATH, LANGGRAPH_STORE_DB_PATH


def snapshot(path: Path) -> sqlite3.Connection:
    """Read a consistent SQLite backup; never create or alter the source database."""
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        source.backup(copy)
    finally:
        source.close()
    return copy


def migrate_memories(path: Path, target) -> dict[str, int]:
    counts = {"copied": 0, "skipped": 0}
    if not path.is_file():
        return counts
    connection = snapshot(path)
    try:
        for prefix, key, value in connection.execute("SELECT prefix, key, value FROM store"):
            namespace = tuple(prefix.split("."))
            if target.get(namespace, key) is not None:
                counts["skipped"] += 1
                continue
            parsed = json.loads(value)
            target.put(namespace, key, parsed)
            restored = target.get(namespace, key)
            if restored is None or restored.value != parsed:
                raise RuntimeError("MongoDB 记忆回读校验失败")
            counts["copied"] += 1
    finally:
        connection.close()
    return counts


def migrate_checkpoints(path: Path, target) -> dict[str, int]:
    counts = {"copied": 0, "skipped": 0, "writes": 0}
    if not path.is_file():
        return counts
    connection = snapshot(path)
    try:
        source = SqliteSaver(connection)
        source.setup()  # Setup applies only to the in-memory backup.
        for item in source.list(None):
            if target.get_tuple(item.config) is None:
                config = item.parent_config or {
                    "configurable": {
                        "thread_id": item.config["configurable"]["thread_id"],
                        "checkpoint_ns": item.config["configurable"].get("checkpoint_ns", ""),
                    }
                }
                target.put(config, item.checkpoint, item.metadata, item.checkpoint["channel_versions"])
                counts["copied"] += 1
            else:
                counts["skipped"] += 1
            # Retain intermediate writes as well as snapshots: message history uses both.
            values = item.config["configurable"]
            rows = connection.execute(
                "SELECT task_id, idx, channel, type, value FROM writes "
                "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (values["thread_id"], values.get("checkpoint_ns", ""), values["checkpoint_id"]),
            )
            operations = []
            for task_id, idx, channel, type_name, value in rows:
                # Preserve original indices, including negative special-channel indices.
                # Both official savers use the same LangGraph typed serializer.
                operations.append(UpdateOne(
                    {"thread_id": values["thread_id"],
                     "checkpoint_ns": values.get("checkpoint_ns", ""),
                     "checkpoint_id": values["checkpoint_id"],
                     "task_id": task_id, "task_path": "", "idx": idx},
                    {"$setOnInsert": {"channel": channel, "type": type_name, "value": value}},
                    upsert=True,
                ))
            if operations:
                target.writes_collection.bulk_write(operations)
                counts["writes"] += len(operations)
            restored = target.get_tuple(item.config)
            if restored is None or restored.checkpoint["id"] != item.checkpoint["id"]:
                raise RuntimeError("MongoDB checkpoint 回读校验失败")
            if counts["copied"] and counts["copied"] % 100 == 0:
                print(f"已迁移 checkpoint：{counts['copied']}", flush=True)
    finally:
        connection.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-source", type=Path, default=CHECKPOINT_DB_PATH)
    parser.add_argument("--memory-source", type=Path, default=LANGGRAPH_STORE_DB_PATH)
    args = parser.parse_args()
    print("迁移前请停止旧服务；目标已有记忆不会覆盖，原 SQLite 文件保留。", flush=True)
    memories = migrate_memories(args.memory_source, make_langgraph_store())
    print(f"长期记忆：{memories}", flush=True)
    checkpoints = migrate_checkpoints(args.checkpoint_source, make_checkpointer())
    print(f"checkpoint：{checkpoints}", flush=True)


if __name__ == "__main__":
    main()

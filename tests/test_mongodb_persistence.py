from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, Mock, patch

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.memory import InMemoryStore
from pymongo.errors import ConnectionFailure

from agent.core import persistence
from scripts.migrate_memory_to_mongodb import migrate_checkpoints, migrate_memories


def test_connection_failure_closes_client_and_hides_credentials():
    client = Mock()
    client.admin.command.side_effect = ConnectionFailure("secret connection details")
    persistence._connected_client.cache_clear()
    with patch.object(persistence, "MongoClient", return_value=client), \
         pytest.raises(RuntimeError, match="MongoDB 连接失败") as exc:
        persistence.get_mongo_client()
    assert "secret" not in str(exc.value)
    client.close.assert_called_once()


def test_factories_use_configured_collections():
    client = MagicMock()
    with patch.object(persistence, "get_mongo_client", return_value=client), \
         patch.object(persistence, "MongoDBSaver") as saver, \
         patch.object(persistence, "MongoDBStore") as store:
        persistence.make_checkpointer()
        persistence.make_langgraph_store()
    assert saver.call_args.kwargs["db_name"] == persistence.settings.MONGODB_DATABASE
    assert saver.call_args.kwargs["writes_collection_name"] == persistence.settings.MONGODB_WRITES_COLLECTION
    store.assert_called_once_with(
        client[persistence.settings.MONGODB_DATABASE][persistence.settings.MONGODB_MEMORY_COLLECTION]
    )


def test_memory_migration_preserves_value_and_does_not_overwrite(tmp_path):
    source = tmp_path / "memories.sqlite"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE store (prefix TEXT, key TEXT, value TEXT)")
    value = {"content": ["历史记忆"], "created_at": "original"}
    connection.execute("INSERT INTO store VALUES (?, ?, ?)",
                       ("lx-aicoding.repo-memory.owner.repo", "/owner/repo.md", json.dumps(value)))
    connection.commit()
    connection.close()
    original = source.read_bytes()
    store = InMemoryStore()
    namespace = ("lx-aicoding", "repo-memory", "owner", "repo")
    assert migrate_memories(source, store) == {"copied": 1, "skipped": 0}
    assert store.get(namespace, "/owner/repo.md").value == value
    store.put(namespace, "/owner/repo.md", {"content": ["新版记忆"]})
    assert migrate_memories(source, store) == {"copied": 0, "skipped": 1}
    assert store.get(namespace, "/owner/repo.md").value == {"content": ["新版记忆"]}
    assert source.read_bytes() == original


def test_checkpoint_migration_retains_parent_and_exact_write_indices(tmp_path):
    source = tmp_path / "checkpoints.sqlite"
    connection = sqlite3.connect(source)
    saver = SqliteSaver(connection)
    config = {"configurable": {"thread_id": "migration-test", "checkpoint_ns": ""}}
    first = empty_checkpoint()
    saved = saver.put(config, first, {"source": "input", "step": 0}, {})
    saver.put_writes(saved, [("messages", {"text": "方案"})], "task-1")
    saver.put_writes(saved, [("__error__", "failed")], "task-1")
    second = empty_checkpoint()
    saver.put(saved, second, {"source": "loop", "step": 1}, {})
    connection.close()
    original = source.read_bytes()

    target = Mock()
    existing = {}
    def get_tuple(config):
        return existing.get(config["configurable"]["checkpoint_id"])
    def put(config, checkpoint, metadata, versions):
        existing[checkpoint["id"]] = Mock(checkpoint=checkpoint)
    target.get_tuple.side_effect = get_tuple
    target.put.side_effect = put
    counts = migrate_checkpoints(source, target)
    assert counts["copied"] == 2
    assert counts["writes"] == 2
    child_call = next(call for call in target.put.call_args_list if call.args[1]["id"] == second["id"])
    assert child_call.args[0]["configurable"]["checkpoint_id"] == first["id"]
    operations = target.writes_collection.bulk_write.call_args.args[0]
    assert {op._filter["idx"] for op in operations} == {-1, 0}
    assert {op._doc["$setOnInsert"]["channel"] for op in operations} == {"__error__", "messages"}
    assert source.read_bytes() == original


def test_missing_source_does_not_create_database(tmp_path):
    missing = tmp_path / "missing.sqlite"
    assert migrate_memories(missing, Mock())["copied"] == 0
    assert migrate_checkpoints(missing, Mock())["copied"] == 0
    assert not missing.exists()

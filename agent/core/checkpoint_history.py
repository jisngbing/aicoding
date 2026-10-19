from __future__ import annotations

import logging
import re
from hashlib import sha1
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pymongo.errors import PyMongoError

from agent.core.graph import get_checkpointer
from agent.core.tool_display import display_text, get_display_secrets, tool_result

logger = logging.getLogger("agent.checkpoint_history")


class CheckpointHistoryError(RuntimeError):
    """区分存储读取失败与真正的空会话，供HTTP层提供可重试错误。"""


def _content_to_text(content: Any) -> str:
    """把 LangChain message.content 转成普通文本。

    千问 / OpenAI 兼容模型在 LangChain 1.x 中可能返回字符串，也可能返回
    content block 列表，例如 `[{"type": "text", "text": "..."}]`。这里统一抽取
    text 字段，避免前端看到 Python 对象 repr。
    """

    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return str(content).strip()


def _delta_messages_from_checkpoint(thread_id: str) -> list[Any]:
    """读取某个 thread 的 messages 通道历史。

    checkpointer 的最新快照不一定直接包含 `channel_values["messages"]`。
    DeepAgents / LangGraph 新版本会把通道历史保存在 delta channel 里，所以这里使用
    `get_delta_channel_history(config, channels=["messages"])` 读取 seed + writes。

    注意：seed 是一个基础快照，writes 是后续增量。不同版本可能重复返回同一条消息，
    后续会按 role/content/message_id 去重。
    """

    config = {"configurable": {"thread_id": thread_id}}
    try:
        checkpointer = get_checkpointer()
        history = checkpointer.get_delta_channel_history(config=config, channels=["messages"])
    except (RuntimeError, OSError, ValueError, TypeError, PyMongoError) as exc:
        logger.warning("读取checkpoint历史失败：thread_id=%s error_type=%s", thread_id, type(exc).__name__)
        raise CheckpointHistoryError(
            "历史会话暂时无法读取，请检查MongoDB连接；若使用SSH隧道，请确认本地隧道正在运行，然后重试加载。"
        ) from None

    channel_history = history.get("messages") if isinstance(history, dict) else None
    if not isinstance(channel_history, dict):
        return []

    messages: list[Any] = []
    seed = channel_history.get("seed")
    seed_value = getattr(seed, "value", None)
    if isinstance(seed_value, list):
        messages.extend(seed_value)

    writes = channel_history.get("writes")
    if isinstance(writes, list):
        for write in writes:
            if not isinstance(write, tuple) or len(write) < 3:
                continue
            value = write[2]
            if isinstance(value, list):
                messages.extend(value)
            elif value is not None:
                messages.append(value)

    return messages


def _extract_user_prompt(text: str) -> str:
    """从 runtime 包装后的 HumanMessage 中还原用户原始输入。

    后端真正发给 Agent 的用户内容会包含仓库地址、任务类型和执行规则，例如：
    `Gitee 仓库地址... 用户任务：... 这是只读任务...`。
    前端历史不应该展示这些内部包装文本，只展示用户在输入框里写的原始需求。
    """

    normalized = text.strip()
    if not normalized:
        return ""

    # 当前项目的前端历史只从 checkpoint 恢复。对于“确认实施”这类 follow-up
    # 指令，runtime 会把真正执行目标改写成上一轮技术方案和内部规则；如果只从
    # “用户任务：”提取，网页历史就会丢失用户刚刚输入的“确认实施”。因此 runtime
    # 会额外写入“用户可见输入”，这里必须优先读取它。
    if "用户可见输入：" in normalized:
        tail = normalized.split("用户可见输入：", 1)[1].strip()
        stop_patterns = [
            "\n\n内部执行上下文：",
            "\n\nGitee 仓库地址：",
            "\n\n任务类型：",
            "\n\n用户任务：",
        ]
        for stop in stop_patterns:
            if stop in tail:
                tail = tail.split(stop, 1)[0].strip()
        return tail

    # 先处理“用户新的修改要求”，因为修订方案的 HumanMessage 往往同时包含
    # “原始用户需求”“上一版技术方案”“用户新的修改要求”。如果先匹配“用户需求”，
    # 会误把后面的大段内部包装内容也当成用户输入展示到网页历史里。
    if "用户新的修改要求：" in normalized:
        tail = normalized.rsplit("用户新的修改要求：", 1)[1].strip()
        # “用户新的修改要求”后面还会拼接专门给方案 Agent 的内部规则。
        # 这些规则只用于约束模型，不属于用户在输入框里的原始问题，不能展示到网页。
        stop_patterns = [
            "\n\n请基于上一版方案",
            "\n\n不要只输出差异说明",
            "\n\n请只生成技术方案",
            "\n\n方案必须使用中文 Markdown",
            "\n\n最后必须单独输出一句",
        ]
        for stop in stop_patterns:
            if stop in tail:
                tail = tail.split(stop, 1)[0].strip()
        return tail

    markers = ["用户任务：", "原始用户需求：", "用户需求："]
    for marker in markers:
        if marker not in normalized:
            continue
        tail = normalized.split(marker, 1)[1].strip()
        stop_patterns = [
            "\n\n这是只读任务",
            "\n\n这是开发实现任务",
            "\n\n请只生成技术方案",
            "\n\n用户已经确认以下技术方案",
            "\n\n任务类型：",
        ]
        for stop in stop_patterns:
            if stop in tail:
                tail = tail.split(stop, 1)[0].strip()
        return tail

    return normalized


def _has_visible_markdown_value(text: str) -> bool:
    """判断 assistant 消息是否值得作为历史正文展示。

    DeepAgents 会产生很多很短的过程消息，例如“现在读取文件”。这些已经通过
    V3 event streaming 和 run_events 展示过，不应该在历史正文里堆积。
    """

    if len(text) >= 200:
        return True
    value_markers = [
        "# ",
        "## ",
        "技术方案",
        "代码审查报告",
        "审查报告",
        "完成总结",
        "任务完成总结",
        "内容如下",
        "整体架构",
    ]
    return any(marker in text for marker in value_markers)


def _message_role(message: Any) -> str | None:
    """识别 LangChain 消息角色。"""

    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "agent"
    if isinstance(message, BaseMessage):
        message_type = str(message.type or "").lower()
        if message_type in {"human", "user"}:
            return "user"
        if message_type in {"ai", "assistant"}:
            return "agent"
    return None


def _dedupe_key(author: str, content: str) -> tuple[str, str]:
    """按内容生成去重键。

    checkpoint 的 seed + writes 里，同一条正文可能带不同 message id。
    因此前端可见历史必须按 author + content 去重，而不是按消息对象 id 去重。
    """

    return author, _compact_content(content)


def _compact_content(content: str) -> str:
    """把正文压缩成稳定去重文本。

    checkpoint 的 seed 和 writes 可能重复返回同一条可见消息，也可能因为换行、
    多个空格等格式差异导致文本看起来不同。这里把空白统一压缩后再参与去重和
    稳定 id 计算，避免前端历史出现重复气泡。
    """

    return re.sub(r"\s+", " ", (content or "").strip())


def stable_history_message_id(thread_id: str, author: str, content: str) -> str:
    """为前端历史消息生成稳定 id。

    旧实现用列表 index 作为 id，checkpoint 新增消息后 index 会变化，前端容易把旧消息
    当成新消息或被当前过程块覆盖。这里改成 thread_id + author + content hash，
    同一条历史正文在多次 SSE 快照中保持同一个 id。
    """

    digest = sha1(f"{author}\n{_compact_content(content)}".encode("utf-8")).hexdigest()[:16]
    return f"{thread_id}-history-{author}-{digest}"


def visible_checkpoint_messages(thread_id: str, *, include_tools: bool = False,
                                answer_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """读取并整理某个 thread 的用户可见历史消息。

    返回值只包含前端真正需要展示的 user/agent 正文：
    - user：尽量还原输入框里的原始文本。
    - agent：保留技术方案、审查报告、总结、问答等有价值正文。
    - 默认过滤 tool/system/短过程消息；include_tools 时恢复工具调用和结果卡片。

    如果 checkpoint 读取失败或没有可用消息，返回空列表。前端历史不再回退读取 Store，
    Store 只保存任务摘要、运行状态、review findings 等业务数据。
    """

    visible: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    tool_cards: dict[str, dict[str, Any]] = {}
    display_secrets = get_display_secrets() if include_tools else ()

    for message in _delta_messages_from_checkpoint(thread_id):
        # 页面可选恢复工具卡片；业务读取方案时仍只读取正文。
        if include_tools and isinstance(message, AIMessage):
            for call in message.tool_calls:
                call_id = call.get("id")
                if not call_id or call.get("name") == "write_todos" or call_id in tool_cards:
                    continue
                card = {
                    "tool_call_id": call_id,
                    "tool_name": call.get("name") or "unknown_tool",
                    "input": display_text(call.get("args", {}), secrets=display_secrets),
                    "output": "未记录到工具执行结果。",
                    "status": "unknown",
                }
                tool_cards[call_id] = card
                visible.append({
                    "message_id": f"{thread_id}-history-tool-{call_id}",
                    "author": "agent", "tool": card, "source": "checkpoint",
                })
        if include_tools and getattr(message, "type", None) == "tool":
            card = tool_cards.get(getattr(message, "tool_call_id", None))
            if card is not None:
                status, output = tool_result(message, secrets=display_secrets)
                card.update(status=status, output=output)
        role = _message_role(message)
        if role is None:
            continue
        text = _content_to_text(getattr(message, "content", None))
        if not text:
            continue

        if role == "user":
            text = _extract_user_prompt(text)
        elif not _has_visible_markdown_value(text) and getattr(message, "id", None) not in (answer_ids or set()):
            continue

        text = text.strip()
        if not text:
            continue
        key = ("id", str(message.id)) if getattr(message, "id", None) else _dedupe_key(role, text)
        if key in seen:
            continue
        seen.add(key)
        visible.append(
            {
                "message_id": getattr(message, "id", None) or stable_history_message_id(thread_id, role, text),
                "author": role,
                "content": text,
                "source": "checkpoint",
            }
        )

    return visible

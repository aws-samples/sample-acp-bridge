"""ACP session/update → SSE event transformation."""


def transform_notification(notification: dict) -> dict | None:
    params = notification.get("params", {})
    update = params.get("update", {})
    session_update = update.get("sessionUpdate")

    if not session_update:
        return None

    if session_update == "agent_message_chunk":
        return {"type": "message.part", "content": update.get("content", {}).get("text", "")}

    if session_update == "agent_thought_chunk":
        return {"type": "message.thinking", "content": update.get("content", {}).get("text", "")}

    if session_update == "user_message_chunk":
        return None

    if session_update == "tool_call":
        return {
            "type": "tool.start",
            "toolCallId": update.get("toolCallId", ""),
            "title": update.get("title", ""),
            "status": update.get("status", "pending"),
        }

    if session_update == "tool_call_update":
        status = update.get("status", "")
        return {
            "type": "tool.done" if status in ("completed", "failed") else "tool.start",
            "toolCallId": update.get("toolCallId", ""),
            "title": update.get("title", ""),
            "status": status,
        }

    if session_update == "plan":
        entries = update.get("entries", [])
        text = "; ".join(e.get("content", "") for e in entries)
        return {"type": "status", "text": text}

    return None

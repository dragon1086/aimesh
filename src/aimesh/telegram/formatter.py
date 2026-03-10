"""Message formatting for Telegram display with persona prefixes."""

from aimesh.core.message import MeshMessage, MessageType

# Persona prefix mapping
PERSONA_ICONS = {
    "pm": "[PM]",
    "human": "[Human]",
}


def get_persona_prefix(agent_id: str) -> str:
    """Get the display persona prefix for an agent."""
    if agent_id in PERSONA_ICONS:
        return PERSONA_ICONS[agent_id]
    # Auto-generate: coder-1 -> [Coder-1], researcher-1 -> [Researcher-1]
    parts = agent_id.split("-")
    if len(parts) >= 2:
        name = parts[0].capitalize()
        suffix = "-".join(parts[1:])
        return f"[{name}-{suffix}]"
    return f"[{agent_id.capitalize()}]"


def format_message(message: MeshMessage) -> str:
    """Format a MeshMessage for Telegram display."""
    prefix = get_persona_prefix(message.sender)

    # Format based on message type
    if message.msg_type == MessageType.TASK_ASSIGN:
        return f"{prefix} *Task assigned*\n{_escape_md(message.content[:500])}"

    elif message.msg_type == MessageType.TASK_ACCEPT:
        return f"{prefix} {_escape_md(message.content)}"

    elif message.msg_type == MessageType.STATUS_UPDATE:
        return f"{prefix} {_escape_md(message.content[:300])}"

    elif message.msg_type == MessageType.RESULT:
        preview = message.content[:500]
        branch = message.metadata.get("branch", "")
        cost = message.metadata.get("cost_usd", 0)
        result_text = f"{prefix} *Result*\n{_escape_md(preview)}"
        if branch:
            result_text += f"\n`Branch: {branch}`"
        if cost:
            result_text += f"\n_Cost: ${cost:.4f}_"
        return result_text

    elif message.msg_type == MessageType.REVIEW_REQUEST:
        return f"{prefix} *Review needed*\n{_escape_md(message.content[:300])}"

    elif message.msg_type == MessageType.REVIEW_RESPONSE:
        return f"{prefix} {_escape_md(message.content)}"

    elif message.msg_type == MessageType.QUESTION:
        return f"{prefix} *Question*\n{_escape_md(message.content)}"

    elif message.msg_type == MessageType.SYSTEM:
        return f"{prefix} _{_escape_md(message.content)}_"

    return f"{prefix} {_escape_md(message.content[:300])}"


def _escape_md(text: str) -> str:
    """Escape special Markdown V2 characters for Telegram."""
    # Characters that need escaping in MarkdownV2
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text


def format_status_summary(tasks: list[dict]) -> str:
    """Format a task status summary for /status command."""
    if not tasks:
        return "No active tasks\\."

    lines = ["*Active Tasks:*\n"]
    for t in tasks:
        state_icon = {
            "created": "new",
            "decomposing": "thinking",
            "assigned": "assigned",
            "in_progress": "working",
            "review": "review",
            "done": "done",
            "failed": "failed",
            "rework": "rework",
            "cancelled": "cancelled",
        }.get(t.get("state", ""), "unknown")
        title = _escape_md(t.get("title", "Untitled"))
        assigned = t.get("assigned_to", "unassigned")
        lines.append(f"\\[{state_icon}\\] {title} \\({assigned}\\)")

    return "\n".join(lines)


def format_agents_list(agents: list[dict]) -> str:
    """Format agent list for /agents command."""
    if not agents:
        return "No agents registered\\."

    lines = ["*Registered Agents:*\n"]
    for a in agents:
        prefix = get_persona_prefix(a.get("agent_id", ""))
        status = a.get("status", "unknown")
        agent_type = a.get("agent_type", "unknown")
        lines.append(f"{prefix} type={agent_type}, status={status}")

    return "\n".join(lines)

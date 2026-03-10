"""Task decomposition logic for the PM agent."""

import json

import structlog

from aimesh.agents.executor import BaseExecutor, ExecutionResult
from aimesh.core.task import Task

logger = structlog.get_logger("decomposer")

DECOMPOSE_PROMPT = """You are a PM agent in an AI development team. Break down the following task into 2-5 concrete subtasks.

TASK: {task_description}

AVAILABLE AGENT TYPES:
{agent_types}

CONTEXT (what we already know about this project):
{context}

Respond in JSON format ONLY:
{{
  "subtasks": [
    {{
      "title": "Short title",
      "description": "Detailed description of what to do",
      "agent_type": "one of the agent types listed above",
      "priority": 1-5 (1=highest)
    }}
  ]
}}
"""

_DEFAULT_AGENT_TYPES = (
    "- coder: Can write code, create files, run commands, work with git\n"
    "- researcher: Can analyze codebases, research libraries, produce technical analysis"
)


def _build_agent_types_from_registry(registry) -> str:
    """Build AVAILABLE AGENT TYPES section from registry entries."""
    types_seen = {}
    for entry in registry.all_agents():
        if entry.agent_type not in types_seen:
            caps = ", ".join(entry.capabilities) if entry.capabilities else entry.agent_type
            types_seen[entry.agent_type] = caps

    if not types_seen:
        return "- coder: Can write code, create files, run tests\n- researcher: Can analyze and research"

    lines = []
    for agent_type, caps in types_seen.items():
        lines.append(f"- {agent_type}: Capabilities: {caps}")
    return "\n".join(lines)


async def decompose_task(
    task: Task,
    executor: BaseExecutor,
    context: str = "No prior context.",
    registry=None,
) -> list[dict]:
    """Use LLM to decompose a high-level task into subtasks.

    Returns a list of subtask dicts with title, description, agent_type, priority.
    """
    # Build dynamic agent types section
    if registry is not None:
        agent_types_section = _build_agent_types_from_registry(registry)
    else:
        agent_types_section = _DEFAULT_AGENT_TYPES

    prompt = DECOMPOSE_PROMPT.format(
        task_description=f"{task.title}: {task.description}",
        context=context,
        agent_types=agent_types_section,
    )

    result = await executor.execute(prompt, max_tokens=2048)

    if not result.success:
        logger.error("decomposition_failed", task_id=task.id, error=result.error)
        return []

    try:
        # Parse JSON from response (handle markdown code blocks)
        content = result.content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content)
        subtasks = data.get("subtasks", [])
        logger.info("decomposition_done", task_id=task.id, subtask_count=len(subtasks))
        return subtasks
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("decomposition_parse_error", task_id=task.id, error=str(e),
                      raw_content=result.content[:200])
        return []

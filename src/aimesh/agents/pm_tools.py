"""PM orchestration tools for ToolUsingExecutor.

These tools are dispatched by ToolUsingExecutor when the PM's LLM
makes tool_use requests. Each tool maps to a system operation.
"""

from typing import Any

import structlog

logger = structlog.get_logger("pm_tools")


# Tool schemas (Anthropic tool format)
PM_TOOL_SCHEMAS = [
    {
        "name": "spawn_agent",
        "description": "Spawn a new dynamic worker agent at runtime.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_type": {"type": "string", "description": "Type of agent (e.g., 'coder', 'designer', 'devops')"},
                "soul_prompt": {"type": "string", "description": "System prompt defining the agent's personality and behavior"},
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of capabilities (e.g., ['code', 'test', 'refactor'])",
                },
            },
            "required": ["agent_type", "soul_prompt", "capabilities"],
        },
    },
    {
        "name": "teardown_agent",
        "description": "Stop and remove a dynamically spawned agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "ID of the agent to remove"},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "list_agents",
        "description": "List all currently registered agents with their types and capabilities.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "assign_task",
        "description": "Assign a task to a specific agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "ID of the agent to assign to"},
                "task_description": {"type": "string", "description": "Description of the task"},
            },
            "required": ["agent_id", "task_description"],
        },
    },
    {
        "name": "check_task_status",
        "description": "Check the current status of a task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID of the task to check"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "request_completion_check",
        "description": "Request all agents assigned to a task's subtasks to confirm completion.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Parent task ID to check completion for"},
            },
            "required": ["task_id"],
        },
    },
]


class PMToolHandlers:
    """Handlers for PM orchestration tools.

    Initialized with references to system components.
    Actual factory/registry/tracker/bus references are set later
    via set_dependencies() after main.py wires everything up.
    """

    def __init__(self) -> None:
        self.factory = None  # AgentFactory, set later
        self.registry = None  # AgentRegistry, set later
        self.tracker = None  # TaskTracker, set later
        self.bus = None  # AbstractMessageBus, set later

    def set_dependencies(self, factory: Any, registry: Any, tracker: Any, bus: Any) -> None:
        """Set references to system components after initialization."""
        self.factory = factory
        self.registry = registry
        self.tracker = tracker
        self.bus = bus

    async def spawn_agent(self, agent_type: str, soul_prompt: str, capabilities: list[str]) -> dict:
        """Spawn a new dynamic worker agent."""
        if not self.factory:
            return {"error": "AgentFactory not initialized"}
        worker = await self.factory.spawn_worker(
            agent_type=agent_type,
            soul_prompt=soul_prompt,
            capabilities=capabilities,
        )
        logger.info("pm_spawned_agent", agent_id=worker.agent_id, agent_type=agent_type)
        return {"agent_id": worker.agent_id, "status": "spawned"}

    async def teardown_agent(self, agent_id: str) -> dict:
        """Teardown a dynamic worker agent."""
        if not self.factory:
            return {"error": "AgentFactory not initialized"}
        success = await self.factory.teardown_worker(agent_id)
        return {"agent_id": agent_id, "removed": success}

    async def list_agents(self) -> dict:
        """List all registered agents."""
        if not self.registry:
            return {"error": "AgentRegistry not initialized"}
        agents = []
        for entry in self.registry.all():
            agents.append({
                "agent_id": entry.agent_id,
                "agent_type": entry.agent_type,
                "capabilities": entry.capabilities,
                "status": entry.status,
            })
        return {"agents": agents, "count": len(agents)}

    async def assign_task(self, agent_id: str, task_description: str) -> dict:
        """Assign a task to a specific agent via the message bus."""
        if not self.bus:
            return {"error": "MessageBus not initialized"}
        from aimesh.core.message import MeshMessage, MessageType
        from aimesh.core.task import Task

        task = Task(title=task_description[:100], description=task_description)
        if self.tracker:
            self.tracker.add(task)

        msg = MeshMessage(
            sender="pm",
            recipient=agent_id,
            msg_type=MessageType.TASK_ASSIGN,
            content=task_description,
            task_id=task.id,
        )
        await self.bus.publish(msg)
        return {"task_id": task.id, "assigned_to": agent_id}

    async def check_task_status(self, task_id: str) -> dict:
        """Check task status."""
        if not self.tracker:
            return {"error": "TaskTracker not initialized"}
        task = self.tracker.get(task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}
        return {
            "task_id": task.id,
            "title": task.title,
            "state": task.state.value,
            "assigned_to": task.assigned_to or "unassigned",
        }

    async def request_completion_check(self, task_id: str) -> dict:
        """Request completion confirmation from all agents on a task."""
        if not self.tracker or not self.bus:
            return {"error": "Dependencies not initialized"}
        task = self.tracker.get(task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        from aimesh.core.message import MeshMessage, MessageType

        # Find all subtasks and their assigned agents
        subtasks = self.tracker.get_subtasks(task_id)
        agents_to_check = [st.assigned_to for st in subtasks if st.assigned_to]

        for agent_id in agents_to_check:
            msg = MeshMessage(
                sender="pm",
                recipient=agent_id,
                msg_type=MessageType.COMPLETION_CHECK,
                content=f"Please confirm completion of your work on task {task_id}",
                task_id=task_id,
            )
            await self.bus.publish(msg)

        return {"task_id": task_id, "agents_checked": agents_to_check}


def create_pm_dispatcher(handlers: PMToolHandlers) -> Any:
    """Create a ToolDispatcher wired to PMToolHandlers."""
    from aimesh.agents.executor import ToolDispatcher

    dispatcher = ToolDispatcher()
    dispatcher.register("spawn_agent", handlers.spawn_agent)
    dispatcher.register("teardown_agent", handlers.teardown_agent)
    dispatcher.register("list_agents", handlers.list_agents)
    dispatcher.register("assign_task", handlers.assign_task)
    dispatcher.register("check_task_status", handlers.check_task_status)
    dispatcher.register("request_completion_check", handlers.request_completion_check)
    return dispatcher

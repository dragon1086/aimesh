# Adding New Agent Types

## Overview

AI Mesh agents are created by subclassing `BaseAgent` and implementing `handle_message()`.

## Steps

### 1. Create the agent file

```python
# src/aimesh/agents/my_agent.py
from aimesh.agents.base import BaseAgent
from aimesh.core.message import MeshMessage, MessageType

class MyAgent(BaseAgent):
    def __init__(self, agent_id, bus, executor, tracker):
        super().__init__(
            agent_id=agent_id,
            agent_type="my_type",
            bus=bus,
            capabilities=["my_capability"],
        )
        self.executor = executor
        self.tracker = tracker

    async def handle_message(self, message: MeshMessage) -> None:
        if message.msg_type == MessageType.TASK_ASSIGN:
            result = await self.executor.execute(message.content)
            await self.send(
                recipient="pm",
                msg_type=MessageType.RESULT,
                content=result.content,
                task_id=message.task_id,
            )
```

### 2. Register in main.py

```python
from aimesh.agents.my_agent import MyAgent

my_agent = MyAgent("my-agent-1", bus=bus, executor=executor, tracker=tracker)
registry.register("my-agent-1", "my_type", ["my_capability"])
await my_agent.start()
```

### 3. Add to shutdown sequence

```python
await my_agent.stop()
```

## Key Interfaces

- `BaseAgent.handle_message(msg)` — process incoming messages
- `BaseAgent.send(recipient, msg_type, content, ...)` — send messages via bus
- `BaseAgent.heartbeat()` — return health status
- `BaseExecutor.execute(prompt, max_tokens)` — call LLM

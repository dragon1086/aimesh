"""LLM execution wrappers for AI Mesh agents.

Two executor patterns (from Phase 0 spike):
- ClaudeCodeExecutor: async subprocess for worker agents (full Claude Code tools)
- AnthropicExecutor: async API for PM agent (cheaper, native async)

Both use safe argument-list process creation (no shell interpolation).
"""

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger("executor")


@dataclass
class ExecutionResult:
    """Result from an LLM execution."""

    content: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    success: bool = True
    error: str | None = None


class BaseExecutor(ABC):
    """Abstract executor interface."""

    @abstractmethod
    async def execute(self, prompt: str, max_tokens: int = 4096, tools: list | None = None) -> ExecutionResult:
        """Run a prompt and return the result. Must not block the event loop."""
        ...


class ClaudeCodeExecutor(BaseExecutor):
    """Executor using Claude CLI async subprocess for full Claude Code capabilities.

    Used by: CoderAgent, ResearcherAgent
    Uses asyncio.create_subprocess with argument list (safe, no shell injection).
    """

    def __init__(self, model: str = "sonnet", max_turns: int = 10,
                 max_budget_usd: float = 5.0, working_dir: str | None = None) -> None:
        self.model = model
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.working_dir = working_dir

    async def execute(self, prompt: str, max_tokens: int = 4096, tools: list | None = None) -> ExecutionResult:
        """Run claude CLI as async subprocess with safe argument list."""
        args = [
            "claude",
            "--print",
            "--output-format", "json",
            "--model", self.model,
            "--max-turns", str(self.max_turns),
            "--max-budget-usd", str(self.max_budget_usd),
            "--dangerously-skip-permissions",
            "-p", prompt,
        ]

        logger.info("claude_code_start", model=self.model, prompt_length=len(prompt))

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                try:
                    data = json.loads(stdout.decode())
                    result = ExecutionResult(
                        content=data.get("result", stdout.decode()),
                        cost_usd=data.get("cost_usd", 0.0),
                        input_tokens=data.get("input_tokens", 0),
                        output_tokens=data.get("output_tokens", 0),
                    )
                except json.JSONDecodeError:
                    result = ExecutionResult(content=stdout.decode())

                logger.info("claude_code_done", cost_usd=result.cost_usd)
                return result
            else:
                error_msg = stderr.decode()[:500]
                logger.error("claude_code_failed", error=error_msg)
                return ExecutionResult(content="", success=False, error=error_msg)

        except Exception as e:
            logger.exception("claude_code_error")
            return ExecutionResult(content="", success=False, error=str(e))


class AnthropicExecutor(BaseExecutor):
    """Executor using Anthropic AsyncAnthropic for direct API calls.

    Used by: PMAgent
    Pattern: native async httpx (non-blocking).
    """

    def __init__(self, model: str = "claude-sonnet-4-20250514",
                 api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key
        self._client = None

    async def _get_client(self):
        if self._client is None:
            import anthropic
            kwargs = {}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    async def execute(self, prompt: str, max_tokens: int = 4096, tools: list | None = None) -> ExecutionResult:
        """Call Anthropic API asynchronously."""
        logger.info("anthropic_start", model=self.model, prompt_length=len(prompt))

        try:
            client = await self._get_client()
            kwargs: dict = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if tools is not None:
                kwargs["tools"] = tools
            response = await client.messages.create(**kwargs)

            content = response.content[0].text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000

            result = ExecutionResult(
                content=content,
                cost_usd=cost,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            logger.info("anthropic_done", cost_usd=cost,
                        input_tokens=input_tokens, output_tokens=output_tokens)
            return result

        except Exception as e:
            logger.exception("anthropic_error")
            return ExecutionResult(content="", success=False, error=str(e))

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.close()
            self._client = None


class ClaudeAgentSDKExecutor(BaseExecutor):
    """Execute tasks via claude-agent-sdk (async-first, wraps CLI internally)."""

    def __init__(
        self,
        model: str = "sonnet",
        system_prompt: str = "",
        allowed_tools: list[str] | None = None,
        permission_mode: str = "acceptEdits",
        cwd: str | None = None,
        max_turns: int = 25,
        max_tokens: int = 100_000,
    ) -> None:
        # Validate permission_mode
        valid_modes = {"acceptEdits", "bypassPermissions", "default", "plan"}
        if permission_mode not in valid_modes:
            raise ValueError(f"Invalid permission_mode '{permission_mode}'. Must be one of {valid_modes}")

        self.model = model
        self.system_prompt = system_prompt
        self.allowed_tools = allowed_tools or ["Read", "Edit", "Write", "Bash", "Glob", "Grep"]
        self.permission_mode = permission_mode
        self.cwd = cwd
        self.max_turns = max_turns
        self.default_max_tokens = max_tokens

    async def execute(self, prompt: str, max_tokens: int = 4096, tools: list | None = None) -> ExecutionResult:
        """Execute a prompt via claude-agent-sdk."""
        try:
            from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage

            options = ClaudeAgentOptions(
                system_prompt=self.system_prompt,
                allowed_tools=self.allowed_tools,
                permission_mode=self.permission_mode,
                cwd=self.cwd,
                max_turns=self.max_turns,
            )

            content_parts = []
            total_tokens = 0
            cost_usd = 0.0

            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            content_parts.append(block.text)
                elif isinstance(message, ResultMessage):
                    # ResultMessage may have usage info
                    if hasattr(message, 'usage'):
                        total_tokens = getattr(message.usage, 'total_tokens', 0)
                    if hasattr(message, 'cost'):
                        cost_usd = getattr(message, 'cost', 0.0)

            content = "\n".join(content_parts)
            return ExecutionResult(
                content=content or "No response from agent",
                success=bool(content),
                input_tokens=total_tokens,
                cost_usd=cost_usd,
            )
        except ImportError:
            return ExecutionResult(
                content="",
                success=False,
                error="claude-agent-sdk not installed. Run: pip install claude-agent-sdk>=0.1.48",
            )
        except Exception as e:
            return ExecutionResult(
                content="",
                success=False,
                error=str(e),
            )

    async def close(self) -> None:
        """No persistent client to close."""
        pass


class ToolDispatcher:
    """Maps tool names to async handler functions."""

    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}

    def register(self, name: str, handler: Any) -> None:
        self._handlers[name] = handler

    async def dispatch(self, name: str, input_data: dict) -> str:
        """Execute a tool and return the result as a string."""
        handler = self._handlers.get(name)
        if handler is None:
            return f"Error: Unknown tool '{name}'. Available tools: {list(self._handlers.keys())}"
        try:
            result = await handler(**input_data)
            return json.dumps(result) if not isinstance(result, str) else result
        except Exception as e:
            return f"Error executing tool '{name}': {e}"


class ToolUsingExecutor(BaseExecutor):
    """Wrapper that adds tool conversation loops to any BaseExecutor.

    AnthropicExecutor stays stateless — this wrapper handles the loop.
    """

    def __init__(
        self,
        inner: BaseExecutor,
        tools: list[dict],
        tool_dispatcher: ToolDispatcher,
        max_iterations: int = 10,
    ) -> None:
        self.inner = inner
        self.tools = tools
        self.tool_dispatcher = tool_dispatcher
        self.max_iterations = max_iterations

    async def execute(self, prompt: str, max_tokens: int = 4096, tools: list | None = None) -> ExecutionResult:
        """Execute with tool conversation loop.

        Calls inner executor, handles tool_use responses by dispatching
        and re-calling until a final text response is produced.
        """
        effective_tools = tools or self.tools

        # For the conversation loop, we need to work with the Anthropic API directly
        # since tool_use requires multi-turn conversation management
        messages = [{"role": "user", "content": prompt}]

        total_input_tokens = 0
        total_output_tokens = 0
        total_cost = 0.0
        final_content = ""

        for iteration in range(self.max_iterations):
            result = await self.inner.execute(
                prompt=json.dumps(messages) if iteration > 0 else prompt,
                max_tokens=max_tokens,
                tools=effective_tools,
            )

            total_input_tokens += result.input_tokens
            total_output_tokens += result.output_tokens
            total_cost += result.cost_usd

            if not result.success:
                return ExecutionResult(
                    content=result.content,
                    success=False,
                    error=result.error,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cost_usd=total_cost,
                )

            # Check if response contains tool_use blocks
            tool_calls = self._extract_tool_calls(result.content)

            if not tool_calls:
                # Final text response — done
                return ExecutionResult(
                    content=result.content,
                    success=True,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cost_usd=total_cost,
                )

            # Dispatch each tool call
            tool_results = []
            for tool_call in tool_calls:
                tool_result = await self.tool_dispatcher.dispatch(
                    tool_call["name"], tool_call.get("input", {})
                )
                tool_results.append({
                    "tool_use_id": tool_call.get("id", ""),
                    "name": tool_call["name"],
                    "result": tool_result,
                })

            # Append assistant response and tool results to conversation
            messages.append({"role": "assistant", "content": result.content})
            messages.append({
                "role": "user",
                "content": json.dumps({"tool_results": tool_results}),
            })

        # Max iterations reached
        return ExecutionResult(
            content=final_content or "Max tool iterations reached without final response",
            success=False,
            error=f"Tool loop exceeded {self.max_iterations} iterations",
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cost_usd=total_cost,
        )

    def _extract_tool_calls(self, content: str) -> list[dict]:
        """Extract tool_use blocks from response content.

        Handles both JSON-formatted and plain text responses.
        """
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return [b for b in data if isinstance(b, dict) and b.get("type") == "tool_use"]
            if isinstance(data, dict) and data.get("type") == "tool_use":
                return [data]
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    async def close(self) -> None:
        """Close the inner executor."""
        await self.inner.close()

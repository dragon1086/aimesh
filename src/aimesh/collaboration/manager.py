"""CollaborationManager — multi-PM orchestration with claim routing and collaboration.

When multiple PMs (orgs) are active, the CollaborationManager:
1. Routes user messages to the best PM based on domain keywords
2. Handles inter-PM collaboration requests and responses
3. Tracks active collaborations to completion
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from uuid import uuid4

import structlog

from aimesh.config import OrgConfig
from aimesh.core.bus import AbstractMessageBus
from aimesh.core.message import MeshMessage, MessageType

logger = structlog.get_logger("collaboration")


@dataclass
class PMInstance:
    """A running PM with its identity and domain info."""

    org_id: str
    org_config: OrgConfig
    pm: object  # PMAgent or TmuxPMOrchestrator
    agent_id: str  # bus subscription ID, e.g. "pm_dev"
    domain: str = ""
    domain_keywords: list[str] = field(default_factory=list)
    identity_text: str = ""  # soul.md content for richer matching
    active_task_count: int = 0


@dataclass
class ActiveCollab:
    """Tracks an in-flight collaboration between two PMs."""

    collab_id: str
    requester_pm: str  # agent_id of requesting PM
    helper_pm: str  # agent_id of volunteering PM
    request: str  # what was requested
    context: str  # shared context
    original_task_id: str = ""
    future: asyncio.Future | None = None


class CollaborationManager:
    """Orchestrates multi-PM claim routing and inter-PM collaboration."""

    def __init__(self, bus: AbstractMessageBus) -> None:
        self.bus = bus
        self.pms: dict[str, PMInstance] = {}  # agent_id -> PMInstance
        self._active_collabs: dict[str, ActiveCollab] = {}
        self._pending_chat_futures: dict[str, asyncio.Future] = {}

    def register_pm(self, instance: PMInstance) -> None:
        """Register a PM instance for routing."""
        self.pms[instance.agent_id] = instance
        logger.info(
            "pm_registered",
            agent_id=instance.agent_id,
            org_id=instance.org_id,
            domain=instance.domain,
            keywords=instance.domain_keywords,
        )

    def unregister_pm(self, agent_id: str) -> None:
        """Remove a PM instance."""
        self.pms.pop(agent_id, None)

    @property
    def is_multi_pm(self) -> bool:
        """True if multiple PMs are registered."""
        return len(self.pms) > 1

    # ------------------------------------------------------------------
    # Claim routing: which PM handles a user message?
    # ------------------------------------------------------------------

    def select_pm(self, text: str) -> str | None:
        """Select the best PM for a user message based on domain keywords.

        Scoring:
        1. Explicit @mention of org_id or domain -> direct route
        2. Keyword overlap score (domain_keywords + identity_text)
        3. Fallback: least-busy PM

        Returns agent_id of selected PM, or None if no PMs.
        """
        if not self.pms:
            return None

        if len(self.pms) == 1:
            return next(iter(self.pms))

        text_lower = text.lower()

        # 1. Check @mention (e.g., "@dev", "@marketing", "@pm_dev")
        mention_match = re.search(r"@(\S+)", text)
        if mention_match:
            mention = mention_match.group(1).lower()
            for agent_id, inst in self.pms.items():
                if (
                    mention == inst.org_id.lower()
                    or mention == inst.domain.lower()
                    or mention == agent_id.lower()
                    or mention == inst.org_config.org_name.lower()
                ):
                    logger.info("route_by_mention", pm=agent_id, mention=mention)
                    return agent_id

        # 2. Keyword scoring
        scores: dict[str, float] = {}
        for agent_id, inst in self.pms.items():
            score = 0.0

            # Domain keyword matches (high weight)
            for kw in inst.domain_keywords:
                if kw.lower() in text_lower:
                    score += 3.0

            # Domain name match
            if inst.domain and inst.domain.lower() in text_lower:
                score += 5.0

            # Org name match
            if inst.org_config.org_name.lower() in text_lower:
                score += 4.0

            # Identity text keyword overlap (lower weight)
            if inst.identity_text:
                identity_words = set(inst.identity_text.lower().split())
                text_words = set(text_lower.split())
                overlap = identity_words & text_words
                # Filter out common short words
                meaningful = {w for w in overlap if len(w) > 2}
                score += len(meaningful) * 0.5

            scores[agent_id] = score

        # Pick highest score (if any PM scored > 0)
        if scores:
            best = max(scores, key=lambda k: scores[k])
            if scores[best] > 0:
                logger.info(
                    "route_by_keywords",
                    pm=best,
                    score=scores[best],
                    scores={k: v for k, v in scores.items() if v > 0},
                )
                return best

        # 3. Fallback: least busy
        least_busy = min(
            self.pms.values(), key=lambda p: p.active_task_count
        )
        logger.info("route_by_load_balance", pm=least_busy.agent_id)
        return least_busy.agent_id

    # ------------------------------------------------------------------
    # Chat routing: forward user message to selected PM
    # ------------------------------------------------------------------

    async def route_chat(
        self,
        text: str,
        chat_id: int,
        user_id: int,
        user_name: str,
        conversation_context: str = "",
    ) -> str:
        """Route a user chat message to the best PM and wait for response.

        Returns the PM's response text.
        """
        target_pm = self.select_pm(text)
        if not target_pm:
            return "No PM available to handle this message."

        inst = self.pms[target_pm]

        # Publish HAND_RAISE to show which PM claimed it
        await self.bus.publish(MeshMessage(
            sender=target_pm,
            recipient="broadcast",
            msg_type=MessageType.HAND_RAISE,
            content=f"✋ {inst.org_config.org_name} 시작!",
            metadata={
                "org_id": inst.org_id,
                "domain": inst.domain,
                "user_message_preview": text[:100],
            },
        ))

        # Forward as CHAT to the selected PM
        msg = MeshMessage(
            sender="human",
            recipient=target_pm,
            msg_type=MessageType.CHAT,
            content=text,
            metadata={
                "conversation_context": conversation_context,
                "user_id": user_id,
                "user_name": user_name,
                "chat_id": chat_id,
                "routed_by": "collaboration_manager",
            },
        )

        # Set up response future
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending_chat_futures[msg.id] = future

        await self.bus.publish(msg)

        try:
            response = await asyncio.wait_for(future, timeout=60.0)
            return response
        except asyncio.TimeoutError:
            logger.warning("collab_chat_timeout", pm=target_pm, msg_id=msg.id)
            return "처리 중 시간이 초과되었습니다. 다시 시도해주세요."
        finally:
            self._pending_chat_futures.pop(msg.id, None)

    async def handle_pm_response(self, message: MeshMessage) -> None:
        """Handle a PM's CHAT response — resolve pending future."""
        if message.parent_id and message.parent_id in self._pending_chat_futures:
            future = self._pending_chat_futures[message.parent_id]
            if not future.done():
                future.set_result(message.content)

    # ------------------------------------------------------------------
    # Collaboration protocol
    # ------------------------------------------------------------------

    async def handle_collab_request(self, message: MeshMessage) -> None:
        """Handle COLLAB_REQUEST: find best PM to help and forward request.

        Expected metadata:
            request: str — what the PM needs
            context: str — relevant context for the helper PM
            required_domain: str — preferred domain (optional)
            original_task_id: str — task this relates to (optional)
        """
        requester = message.sender
        request_text = message.metadata.get("request", message.content)
        context = message.metadata.get("context", "")
        required_domain = message.metadata.get("required_domain", "")
        original_task_id = message.metadata.get("original_task_id", "")

        # Find best helper PM (excluding the requester)
        helper_pm = self._find_helper_pm(request_text, required_domain, requester)
        if not helper_pm:
            # No suitable PM found — notify requester
            await self.bus.publish(MeshMessage(
                sender="collab_manager",
                recipient=requester,
                msg_type=MessageType.COLLAB_RESULT,
                content="협업 가능한 조직을 찾지 못했습니다.",
                parent_id=message.id,
                metadata={"status": "no_helper_found"},
            ))
            return

        helper_inst = self.pms[helper_pm]
        requester_inst = self.pms.get(requester)
        requester_name = requester_inst.org_config.org_name if requester_inst else requester

        # Create collaboration tracking
        collab_id = str(uuid4())[:8]
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        collab = ActiveCollab(
            collab_id=collab_id,
            requester_pm=requester,
            helper_pm=helper_pm,
            request=request_text,
            context=context,
            original_task_id=original_task_id,
            future=future,
        )
        self._active_collabs[collab_id] = collab

        # Broadcast: looking for help
        await self.bus.publish(MeshMessage(
            sender=requester,
            recipient="broadcast",
            msg_type=MessageType.COLLAB_REQUEST,
            content=f"🙋 도와줄 조직 찾아요!\n요청: {request_text}",
            metadata={
                "collab_id": collab_id,
                "required_domain": required_domain,
            },
        ))

        # Send COLLAB_ACCEPT from helper (auto-volunteer based on domain match)
        await self.bus.publish(MeshMessage(
            sender=helper_pm,
            recipient="broadcast",
            msg_type=MessageType.COLLAB_ACCEPT,
            content=f"🤝 제가 맡을게요! ({helper_inst.org_config.org_name})",
            metadata={"collab_id": collab_id},
        ))

        # Forward the actual work to the helper PM as a TASK_ASSIGN
        collab_prompt = (
            f"[협업 요청 from {requester_name}]\n\n"
            f"요청: {request_text}\n\n"
        )
        if context:
            collab_prompt += f"맥락:\n{context}\n\n"
        collab_prompt += (
            "이 요청을 처리하고 결과를 알려주세요. "
            "완료되면 결과를 상세히 작성해주세요."
        )

        await self.bus.publish(MeshMessage(
            sender="collab_manager",
            recipient=helper_pm,
            msg_type=MessageType.TASK_ASSIGN,
            content=collab_prompt,
            metadata={
                "collab_id": collab_id,
                "collab_requester": requester,
                "is_collaboration": True,
            },
        ))

        logger.info(
            "collab_started",
            collab_id=collab_id,
            requester=requester,
            helper=helper_pm,
            request=request_text[:100],
        )

        # Wait for result with extended timeout (collaboration can take time)
        try:
            result = await asyncio.wait_for(future, timeout=300.0)
            # Forward result to requester
            await self.bus.publish(MeshMessage(
                sender=helper_pm,
                recipient=requester,
                msg_type=MessageType.COLLAB_RESULT,
                content=result,
                metadata={
                    "collab_id": collab_id,
                    "helper_org": helper_inst.org_id,
                    "original_task_id": original_task_id,
                },
            ))

            # Broadcast completion
            await self.bus.publish(MeshMessage(
                sender=helper_pm,
                recipient="broadcast",
                msg_type=MessageType.COLLAB_RESULT,
                content=f"✅ 협업 완료: [{helper_inst.org_config.org_name}]\n{result[:300]}",
                metadata={"collab_id": collab_id},
            ))

        except asyncio.TimeoutError:
            logger.warning("collab_timeout", collab_id=collab_id)
            await self.bus.publish(MeshMessage(
                sender="collab_manager",
                recipient=requester,
                msg_type=MessageType.COLLAB_RESULT,
                content="협업 시간이 초과되었습니다.",
                parent_id=message.id,
                metadata={"collab_id": collab_id, "status": "timeout"},
            ))
        finally:
            self._active_collabs.pop(collab_id, None)

    async def handle_collab_result_from_helper(self, message: MeshMessage) -> None:
        """Handle a helper PM's completed result for a collaboration.

        Called when a helper PM completes its collaboration subtask.
        Resolves the waiting future so handle_collab_request can forward the result.
        """
        collab_id = message.metadata.get("collab_id", "")
        collab = self._active_collabs.get(collab_id)
        if not collab or not collab.future or collab.future.done():
            return

        collab.future.set_result(message.content)
        logger.info("collab_result_received", collab_id=collab_id, helper=message.sender)

    def _find_helper_pm(
        self, request_text: str, required_domain: str, exclude: str
    ) -> str | None:
        """Find the best PM to handle a collaboration request."""
        candidates = {
            aid: inst for aid, inst in self.pms.items() if aid != exclude
        }
        if not candidates:
            return None

        if len(candidates) == 1:
            return next(iter(candidates))

        text_lower = request_text.lower()

        # If required_domain specified, try exact match first
        if required_domain:
            domain_lower = required_domain.lower()
            for aid, inst in candidates.items():
                if inst.domain.lower() == domain_lower:
                    return aid

        # Keyword scoring (same as select_pm but among candidates)
        scores: dict[str, float] = {}
        for aid, inst in candidates.items():
            score = 0.0
            for kw in inst.domain_keywords:
                if kw.lower() in text_lower:
                    score += 3.0
            if inst.domain and inst.domain.lower() in text_lower:
                score += 5.0
            scores[aid] = score

        best = max(scores, key=lambda k: scores[k])
        if scores[best] > 0:
            return best

        # Fallback: least busy
        return min(candidates, key=lambda k: candidates[k].active_task_count)

    # ------------------------------------------------------------------
    # Bus message handler
    # ------------------------------------------------------------------

    async def on_bus_message(self, message: MeshMessage) -> None:
        """Handle messages addressed to collab_manager."""
        if message.msg_type == MessageType.COLLAB_REQUEST:
            await self.handle_collab_request(message)
        elif message.msg_type == MessageType.CHAT:
            await self.handle_pm_response(message)

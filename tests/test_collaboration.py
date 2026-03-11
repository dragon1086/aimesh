"""Tests for inter-PM collaboration manager."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from aimesh.collaboration.manager import CollaborationManager, PMInstance
from aimesh.config import OrgConfig, TelegramOrgConfig
from aimesh.core.bus import AsyncioMessageBus
from aimesh.core.message import MeshMessage, MessageType


def _make_org_config(org_id: str, org_name: str = "", domain: str = "", keywords: list = None):
    """Create a minimal OrgConfig."""
    return OrgConfig(
        org_id=org_id,
        org_name=org_name or org_id,
        domain=domain,
        domain_keywords=keywords or [],
        telegram=TelegramOrgConfig(group_chat_id=-100, admin_user_ids=[111]),
    )


def _make_pm_instance(org_id: str, domain: str = "", keywords: list = None, identity: str = ""):
    """Create a PMInstance for testing."""
    config = _make_org_config(org_id, org_name=org_id, domain=domain, keywords=keywords)
    return PMInstance(
        org_id=org_id,
        org_config=config,
        pm=MagicMock(),
        agent_id=f"pm_{org_id}",
        domain=domain,
        domain_keywords=keywords or [],
        identity_text=identity,
    )


# --- select_pm tests ---

def test_select_pm_single_pm():
    """Single PM always gets selected."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", domain="development"))
    assert mgr.select_pm("anything") == "pm_dev"


def test_select_pm_no_pms():
    """Returns None when no PMs registered."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    assert mgr.select_pm("hello") is None


def test_select_pm_by_mention():
    """@mention routes to correct PM."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", domain="development"))
    mgr.register_pm(_make_pm_instance("marketing", domain="marketing"))

    assert mgr.select_pm("@marketing 출시 홍보 해줘") == "pm_marketing"
    assert mgr.select_pm("@dev 로그인 기능 만들어") == "pm_dev"


def test_select_pm_by_domain_keyword():
    """Domain keywords route correctly."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", domain="개발", keywords=["코드", "로그인", "API", "기능"]))
    mgr.register_pm(_make_pm_instance("marketing", domain="마케팅", keywords=["홍보", "카피", "광고", "마케팅"]))

    assert mgr.select_pm("로그인 기능 만들어줘") == "pm_dev"
    assert mgr.select_pm("출시 홍보 카피 써줘") == "pm_marketing"


def test_select_pm_by_domain_name():
    """Domain name match scores highly."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", domain="development"))
    mgr.register_pm(_make_pm_instance("marketing", domain="marketing"))

    assert mgr.select_pm("marketing campaign plan") == "pm_marketing"
    assert mgr.select_pm("development task") == "pm_dev"


def test_select_pm_by_org_name():
    """Org name match routes correctly."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", domain=""))
    mgr.register_pm(_make_pm_instance("marketing", domain=""))

    assert mgr.select_pm("marketing 팀 도와줘") == "pm_marketing"


def test_select_pm_fallback_least_busy():
    """Ambiguous message goes to least busy PM."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)

    dev = _make_pm_instance("dev")
    dev.active_task_count = 3
    mgr.register_pm(dev)

    mkt = _make_pm_instance("marketing")
    mkt.active_task_count = 1
    mgr.register_pm(mkt)

    # Message has no keywords matching either PM
    assert mgr.select_pm("안녕하세요") == "pm_marketing"


def test_select_pm_identity_text_matching():
    """Identity text (soul.md) contributes to scoring."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance(
        "dev", identity="코드 리뷰 프론트엔드 백엔드 데이터베이스",
    ))
    mgr.register_pm(_make_pm_instance(
        "marketing", identity="브랜딩 광고 소셜미디어 캠페인",
    ))

    assert mgr.select_pm("소셜미디어 캠페인 기획") == "pm_marketing"
    assert mgr.select_pm("데이터베이스 스키마 설계") == "pm_dev"


# --- is_multi_pm tests ---

def test_is_multi_pm():
    """is_multi_pm returns True when 2+ PMs registered."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    assert not mgr.is_multi_pm

    mgr.register_pm(_make_pm_instance("dev"))
    assert not mgr.is_multi_pm

    mgr.register_pm(_make_pm_instance("marketing"))
    assert mgr.is_multi_pm


def test_unregister_pm():
    """Unregistered PM is removed."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev"))
    mgr.register_pm(_make_pm_instance("marketing"))
    assert mgr.is_multi_pm

    mgr.unregister_pm("pm_marketing")
    assert not mgr.is_multi_pm
    assert "pm_marketing" not in mgr.pms


# --- _find_helper_pm tests ---

def test_find_helper_excludes_requester():
    """Helper search excludes the requesting PM."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", keywords=["코드"]))
    mgr.register_pm(_make_pm_instance("marketing", keywords=["홍보"]))

    helper = mgr._find_helper_pm("코드 리뷰 해줘", "", "pm_dev")
    assert helper == "pm_marketing"


def test_find_helper_by_required_domain():
    """required_domain routes to matching PM."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", domain="development"))
    mgr.register_pm(_make_pm_instance("marketing", domain="marketing"))
    mgr.register_pm(_make_pm_instance("design", domain="design"))

    helper = mgr._find_helper_pm("make a logo", "design", "pm_dev")
    assert helper == "pm_design"


def test_find_helper_by_keywords():
    """Keyword matching finds the best helper."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", keywords=["코드", "API"]))
    mgr.register_pm(_make_pm_instance("marketing", keywords=["홍보", "카피"]))
    mgr.register_pm(_make_pm_instance("design", keywords=["디자인", "UI"]))

    helper = mgr._find_helper_pm("홍보 카피 작성", "", "pm_dev")
    assert helper == "pm_marketing"


def test_find_helper_no_candidates():
    """Returns None when only one PM exists (the requester)."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev"))

    helper = mgr._find_helper_pm("help me", "", "pm_dev")
    assert helper is None


# --- route_chat tests ---

@pytest.mark.asyncio
async def test_route_chat_publishes_hand_raise():
    """route_chat publishes HAND_RAISE before forwarding."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", domain="development", keywords=["로그인"]))

    published = []
    original_publish = bus.publish

    async def capture_publish(msg):
        published.append(msg)
        await original_publish(msg)

    bus.publish = capture_publish

    # Subscribe a mock PM to receive messages
    pm_received = []
    await bus.subscribe("pm_dev", lambda m: pm_received.append(m))

    # Run route_chat with a quick timeout (PM won't respond)
    try:
        await asyncio.wait_for(
            mgr.route_chat("로그인 만들어", -100, 111, "User"),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        pass

    # Check HAND_RAISE was published
    hand_raises = [m for m in published if m.msg_type == MessageType.HAND_RAISE]
    assert len(hand_raises) >= 1
    assert "pm_dev" in hand_raises[0].sender or "dev" in hand_raises[0].content


@pytest.mark.asyncio
async def test_route_chat_returns_pm_response():
    """route_chat returns the PM's response."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", keywords=["task"]))

    # Subscribe PM and auto-respond
    async def auto_respond(msg):
        if msg.msg_type == MessageType.CHAT:
            response = MeshMessage(
                sender="pm_dev",
                recipient="human",
                msg_type=MessageType.CHAT,
                content="Task accepted!",
                parent_id=msg.id,
            )
            await bus.publish(response)

    await bus.subscribe("pm_dev", auto_respond)
    await bus.subscribe("human", lambda m: asyncio.ensure_future(mgr.handle_pm_response(m)))

    result = await mgr.route_chat("do this task", -100, 111, "User")
    assert result == "Task accepted!"


# --- on_bus_message tests ---

@pytest.mark.asyncio
async def test_on_bus_message_collab_request():
    """COLLAB_REQUEST messages are handled."""
    bus = AsyncioMessageBus()
    mgr = CollaborationManager(bus=bus)
    mgr.register_pm(_make_pm_instance("dev", keywords=["코드"]))
    mgr.register_pm(_make_pm_instance("marketing", keywords=["홍보"]))

    # Subscribe PMs
    dev_msgs = []
    mkt_msgs = []
    await bus.subscribe("pm_dev", lambda m: dev_msgs.append(m))
    await bus.subscribe("pm_marketing", lambda m: mkt_msgs.append(m))

    msg = MeshMessage(
        sender="pm_dev",
        recipient="collab_manager",
        msg_type=MessageType.COLLAB_REQUEST,
        content="홍보 카피 필요",
        metadata={
            "request": "출시 홍보 카피 3개 작성",
            "context": "로그인 v1.0 개발 완료",
            "required_domain": "marketing",
        },
    )

    # Run in background (handle_collab_request waits for helper result)
    task = asyncio.create_task(mgr.on_bus_message(msg))

    # Wait for messages to propagate
    await asyncio.sleep(0.5)

    # Marketing PM should have received a TASK_ASSIGN
    task_assigns = [m for m in mkt_msgs if m.msg_type == MessageType.TASK_ASSIGN]
    assert len(task_assigns) >= 1
    assert "출시 홍보 카피" in task_assigns[0].content

    # Cancel the waiting task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# --- MessageType tests ---

def test_collab_message_types_exist():
    """All collaboration message types are defined."""
    assert MessageType.HAND_RAISE.value == "hand_raise"
    assert MessageType.COLLAB_REQUEST.value == "collab_request"
    assert MessageType.COLLAB_ACCEPT.value == "collab_accept"
    assert MessageType.COLLAB_RESULT.value == "collab_result"


def test_collab_message_serialization():
    """Collaboration messages serialize/deserialize correctly."""
    msg = MeshMessage(
        sender="pm_dev",
        recipient="collab_manager",
        msg_type=MessageType.COLLAB_REQUEST,
        content="Need help with marketing",
        metadata={"required_domain": "marketing"},
    )
    d = msg.to_dict()
    restored = MeshMessage.from_dict(d)

    assert restored.msg_type == MessageType.COLLAB_REQUEST
    assert restored.metadata["required_domain"] == "marketing"
    assert restored.sender == "pm_dev"

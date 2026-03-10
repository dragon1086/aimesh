"""Tests for MeshMessage protocol."""

from datetime import datetime, timezone

from aimesh.core.message import MeshMessage, MessageStatus, MessageType


def test_message_creation():
    msg = MeshMessage(
        sender="pm",
        recipient="coder-1",
        msg_type=MessageType.TASK_ASSIGN,
        content="Implement the user model",
        task_id="task-123",
    )
    assert msg.sender == "pm"
    assert msg.recipient == "coder-1"
    assert msg.msg_type == MessageType.TASK_ASSIGN
    assert msg.task_id == "task-123"
    assert msg.status == MessageStatus.PENDING
    assert msg.id  # UUID auto-generated
    assert msg.timestamp  # auto-generated


def test_message_serialization():
    msg = MeshMessage(
        sender="coder-1",
        recipient="pm",
        msg_type=MessageType.RESULT,
        content="Code complete",
        task_id="task-456",
        metadata={"branch": "task/task-456"},
    )
    data = msg.to_dict()
    assert data["sender"] == "coder-1"
    assert data["msg_type"] == "result"
    assert data["metadata"]["branch"] == "task/task-456"

    restored = MeshMessage.from_dict(data)
    assert restored.sender == msg.sender
    assert restored.msg_type == msg.msg_type
    assert restored.metadata == msg.metadata


def test_message_type_enum():
    assert MessageType.TASK_ASSIGN.value == "task_assign"
    assert MessageType.STATUS_UPDATE.value == "status_update"
    assert MessageType.RESULT.value == "result"
    assert MessageType.REVIEW_REQUEST.value == "review_request"
    assert MessageType.REVIEW_RESPONSE.value == "review_response"
    assert MessageType.QUESTION.value == "question"
    assert MessageType.SYSTEM.value == "system"
    assert MessageType.TASK_ACCEPT.value == "task_accept"


def test_message_status_enum():
    assert MessageStatus.PENDING.value == "pending"
    assert MessageStatus.DELIVERED.value == "delivered"
    assert MessageStatus.READ.value == "read"
    assert MessageStatus.FAILED.value == "failed"


def test_message_defaults():
    msg = MeshMessage(
        sender="human", recipient="pm", msg_type=MessageType.SYSTEM, content="Hello"
    )
    assert msg.task_id is None
    assert msg.parent_id is None
    assert msg.metadata == {}
    assert isinstance(msg.timestamp, datetime)

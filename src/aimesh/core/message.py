"""Message protocol for AI Mesh inter-agent communication."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class MessageType(Enum):
    """Types of messages in the mesh."""

    TASK_ASSIGN = "task_assign"
    TASK_ACCEPT = "task_accept"
    STATUS_UPDATE = "status_update"
    RESULT = "result"
    REVIEW_REQUEST = "review_request"
    REVIEW_RESPONSE = "review_response"
    QUESTION = "question"
    SYSTEM = "system"
    COMPLETION_CHECK = "completion_check"
    COMPLETION_CONFIRM = "completion_confirm"
    CHAT = "chat"  # Natural language message forwarded to PM for classification

    # Inter-PM collaboration
    HAND_RAISE = "hand_raise"          # PM claims a user task
    COLLAB_REQUEST = "collab_request"  # PM requests help from another PM
    COLLAB_ACCEPT = "collab_accept"    # PM volunteers to help
    COLLAB_RESULT = "collab_result"    # PM returns collaboration result


class MessageStatus(Enum):
    """Delivery status of a message."""

    PENDING = "pending"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


@dataclass
class MeshMessage:
    """A message in the AI Mesh communication protocol."""

    sender: str
    recipient: str
    msg_type: MessageType
    content: str
    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    task_id: str | None = None
    metadata: dict = field(default_factory=dict)
    parent_id: str | None = None
    status: MessageStatus = field(default=MessageStatus.PENDING)

    def to_dict(self) -> dict:
        """Serialize message to a dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "sender": self.sender,
            "recipient": self.recipient,
            "task_id": self.task_id,
            "msg_type": self.msg_type.value,
            "content": self.content,
            "metadata": self.metadata,
            "parent_id": self.parent_id,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MeshMessage":
        """Deserialize message from a dictionary."""
        return cls(
            id=data["id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            sender=data["sender"],
            recipient=data["recipient"],
            task_id=data.get("task_id"),
            msg_type=MessageType(data["msg_type"]),
            content=data["content"],
            metadata=data.get("metadata", {}),
            parent_id=data.get("parent_id"),
            status=MessageStatus(data.get("status", "pending")),
        )

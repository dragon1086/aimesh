"""Pydantic settings for AI Mesh configuration."""

import yaml
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class MeshSettings(BaseSettings):
    """AI Mesh configuration loaded from environment variables."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Telegram
    telegram_bot_token: str = Field(description="Telegram bot token from @BotFather")
    telegram_group_chat_id: int = Field(description="Telegram group chat ID for agent display")
    admin_user_ids: str = Field(
        default="", description="Comma-separated admin user IDs"
    )

    # Anthropic
    anthropic_api_key: str = Field(default="", description="Anthropic API key for PM agent")

    # Model settings
    pm_model: str = Field(
        default="claude-sonnet-4-20250514", description="Model for PM agent"
    )
    worker_model: str = Field(
        default="claude-sonnet-4-20250514", description="Model for worker agents"
    )

    # Cost controls
    max_tokens_per_subtask: int = Field(
        default=100_000, description="Max tokens per subtask"
    )
    max_retries_per_subtask: int = Field(
        default=3, description="Max retries before marking FAILED"
    )
    agent_idle_timeout_minutes: int = Field(
        default=30, description="Shut down idle agents after N minutes"
    )
    review_timeout_minutes: int = Field(
        default=30, description="Send reminder if no human response to review within N minutes"
    )
    max_budget_per_task_usd: float = Field(
        default=5.0, description="Max USD spend per task"
    )

    # Workspace
    workspace_path: str = Field(
        default="./workspace", description="Path for coder agent git operations"
    )

    @property
    def admin_ids(self) -> list[int]:
        """Parse admin user IDs from comma-separated string."""
        if not self.admin_user_ids:
            return []
        return [int(uid.strip()) for uid in self.admin_user_ids.split(",") if uid.strip()]


class AgentEntry(BaseModel):
    """Agent definition in org config."""
    id: str
    type: str
    soul_file: str = ""
    capabilities: list[str] = []
    model: str = "sonnet"
    max_budget_usd: float = 5.0


class PMConfig(BaseModel):
    """PM configuration."""
    model: str = "claude-sonnet-4-20250514"
    tools_enabled: bool = True


class TelegramOrgConfig(BaseModel):
    """Per-org Telegram settings."""
    group_chat_id: int = 0
    admin_user_ids: list[int] = []


class MultiTeamConfig(BaseModel):
    """Top-level config for multi-team mode."""

    teams: list[str] = []  # List of org_ids to start
    shared_chat_ids: list[int] = []  # Chat IDs shared between teams
    dedup_db_path: str = "data/message_dedup.db"


class OrgConfig(BaseModel):
    """Organization configuration loaded from YAML."""
    org_id: str
    org_name: str = "My Org"
    telegram: TelegramOrgConfig = TelegramOrgConfig()
    agents: list[AgentEntry] = []
    pm: PMConfig = PMConfig()
    workspace_path: str = "./workspace"

    def load_soul_prompt(self, agent_entry: AgentEntry, org_dir: Path) -> str:
        """Load soul prompt for an agent. Falls back to org-level soul.md."""
        if agent_entry.soul_file:
            soul_path = org_dir / agent_entry.soul_file
            if soul_path.exists():
                return soul_path.read_text(encoding="utf-8")
        # Fallback to org-level soul.md
        org_soul = org_dir / "soul.md"
        if org_soul.exists():
            return org_soul.read_text(encoding="utf-8")
        return ""


ORGS_DIR = Path("orgs")


def load_org_config(org_id: str) -> OrgConfig:
    """Load org config from orgs/{org_id}/config.yaml."""
    org_dir = ORGS_DIR / org_id
    config_path = org_dir / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Org config not found: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return OrgConfig(**data)


def save_org_config(config: OrgConfig) -> None:
    """Save org config to orgs/{org_id}/config.yaml."""
    org_dir = ORGS_DIR / config.org_id
    org_dir.mkdir(parents=True, exist_ok=True)

    config_path = org_dir / "config.yaml"
    data = config.model_dump()

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

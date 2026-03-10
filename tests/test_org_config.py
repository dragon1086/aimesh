"""Tests for org config loading and saving."""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from aimesh.config import OrgConfig, AgentEntry, load_org_config, save_org_config, ORGS_DIR


def test_load_default_org_config():
    """load_org_config('_default') returns valid OrgConfig."""
    config = load_org_config("_default")
    assert config.org_id == "default"
    assert len(config.agents) == 2
    assert config.agents[0].type == "coder"
    assert config.agents[1].type == "researcher"
    assert config.pm.tools_enabled is True


def test_org_config_rejects_missing_org_id():
    """OrgConfig without org_id raises validation error."""
    with pytest.raises(Exception):
        OrgConfig(agents=[])


def test_save_and_reload_roundtrip(tmp_path):
    """save_org_config produces a file that load_org_config can read back."""
    config = OrgConfig(
        org_id="test-org",
        org_name="Test Org",
        agents=[
            AgentEntry(id="dev-1", type="developer", capabilities=["code"]),
        ],
        workspace_path="/tmp/workspace",
    )

    import aimesh.config as config_module
    original_orgs_dir = config_module.ORGS_DIR
    config_module.ORGS_DIR = tmp_path
    try:
        save_org_config(config)
        loaded = load_org_config("test-org")
        assert loaded.org_id == config.org_id
        assert loaded.org_name == config.org_name
        assert len(loaded.agents) == 1
        assert loaded.agents[0].type == "developer"
    finally:
        config_module.ORGS_DIR = original_orgs_dir


def test_soul_file_resolution():
    """Agent with soul_file loads per-agent soul; without falls back to org soul.md."""
    config = load_org_config("_default")
    org_dir = ORGS_DIR / "_default"

    # Agent with soul_file
    coder = config.agents[0]
    soul = config.load_soul_prompt(coder, org_dir)
    assert "Coder Agent" in soul or "coding" in soul.lower()

    # Agent without soul_file falls back to org soul.md
    agent_no_soul = AgentEntry(id="test-1", type="test")
    soul_fallback = config.load_soul_prompt(agent_no_soul, org_dir)
    assert "AI Mesh Agent" in soul_fallback or "agent" in soul_fallback.lower()


def test_config_yaml_has_no_bot_token():
    """config.yaml does NOT contain bot_token field."""
    config = load_org_config("_default")
    # OrgConfig should not have a bot_token field
    assert not hasattr(config, 'bot_token')
    data = config.model_dump()
    assert 'bot_token' not in data

"""Tests for the tmux outbox protocol module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aimesh.tmux.protocol import build_outbox_instructions, poll_outbox, write_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_RESPONSE = {
    "id": "resp-001",
    "reply_to": "msg-abc",
    "type": "chat_response",
    "content": "Hello from worker",
    "structured_data": {},
    "timestamp": "2026-03-11T00:00:00Z",
}


# ---------------------------------------------------------------------------
# write_response tests
# ---------------------------------------------------------------------------


def test_write_response_creates_file(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    result = write_response(outbox, SAMPLE_RESPONSE)
    assert result.exists()
    assert result.suffix == ".json"
    assert result.parent == outbox


def test_write_response_atomic(tmp_path: Path) -> None:
    """No .tmp files should remain after a successful write."""
    outbox = tmp_path / "outbox"
    write_response(outbox, SAMPLE_RESPONSE)
    tmp_files = list(outbox.glob("*.tmp"))
    assert tmp_files == []


def test_write_response_valid_json(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    result = write_response(outbox, SAMPLE_RESPONSE)
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data == SAMPLE_RESPONSE


def test_write_response_creates_dir(tmp_path: Path) -> None:
    """write_response must create the outbox directory if it doesn't exist."""
    outbox = tmp_path / "deep" / "nested" / "outbox"
    assert not outbox.exists()
    write_response(outbox, SAMPLE_RESPONSE)
    assert outbox.is_dir()


# ---------------------------------------------------------------------------
# poll_outbox tests
# ---------------------------------------------------------------------------


def test_poll_outbox_reads_and_moves(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    write_response(outbox, {"id": "1", "content": "first"})
    write_response(outbox, {"id": "2", "content": "second"})

    results = poll_outbox(outbox)

    assert len(results) == 2
    ids = {r["id"] for r in results}
    assert ids == {"1", "2"}

    # Files must have been moved to processed/
    assert list(outbox.glob("*.json")) == []
    processed = list((outbox / "processed").glob("*.json"))
    assert len(processed) == 2


def test_poll_outbox_skips_malformed(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    outbox.mkdir(parents=True)
    bad_file = outbox / "bad.json"
    bad_file.write_text("not valid json {{{{", encoding="utf-8")

    results = poll_outbox(outbox)

    assert results == []
    # Malformed file must still be moved to processed/ to avoid retrying
    assert not bad_file.exists()
    assert (outbox / "processed" / "bad.json").exists()


def test_poll_outbox_empty_dir(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    outbox.mkdir(parents=True)
    results = poll_outbox(outbox)
    assert results == []


def test_poll_outbox_nonexistent_dir(tmp_path: Path) -> None:
    """Polling a directory that doesn't exist should return an empty list."""
    results = poll_outbox(tmp_path / "does_not_exist")
    assert results == []


# ---------------------------------------------------------------------------
# build_outbox_instructions tests
# ---------------------------------------------------------------------------


def test_build_outbox_instructions(tmp_path: Path) -> None:
    outbox_path = str(tmp_path / "outbox")
    instructions = build_outbox_instructions(outbox_path)

    assert outbox_path in instructions
    assert "chat_response" in instructions
    assert "reply_to" in instructions
    assert "structured_data" in instructions
    assert "ISO8601" in instructions

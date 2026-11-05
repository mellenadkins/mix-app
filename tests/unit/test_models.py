"""Unit tests for Pydantic data models."""

import math
from datetime import datetime, timezone

import pytest

from mix_revision_agent.models import EditPlan, RevisionEmail, RevisionRequest
from mix_revision_agent.models import RevisionStatus


def test_revision_email_creation():
    email = RevisionEmail(
        message_id="<abc123@example.com>",
        thread_id="<thread456@example.com>",
        sender="artist@example.com",
        subject="Mix revision needed",
        body_text="Please turn up the vocals by 3 dB",
        received_at=datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc),
    )
    assert email.message_id == "<abc123@example.com>"


def test_revision_request_valid():
    req = RevisionRequest(track_name="VOCALS", volume_db=3.0)
    assert req.track_name == "VOCALS"
    assert req.volume_db == 3.0


def test_revision_request_rejects_nan():
    with pytest.raises(ValueError):
        RevisionRequest(track_name="VOCALS", volume_db=float("nan"))


def test_revision_request_rejects_inf():
    with pytest.raises(ValueError):
        RevisionRequest(track_name="VOCALS", volume_db=float("inf"))


def test_revision_status_enum():
    assert RevisionStatus.PENDING == "pending"
    assert RevisionStatus.FAILED == "failed"


def test_edit_plan_creation(tmp_path):
    baseline = tmp_path / "session.als"
    output = tmp_path / "session_rev1.als"
    plan = EditPlan(
        baseline_path=baseline,
        track_name="VOCALS",
        track_index=2,
        old_linear_gain=1.0,
        new_linear_gain=0.5,
        output_path=output,
        volume_db_delta=-6.0,
        volume_db_target=-6.0,
    )
    assert plan.track_name == "VOCALS"
    assert plan.volume_db_target == -6.0

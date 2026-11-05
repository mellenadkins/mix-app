"""Pydantic data models for the Mix Revision Agent.

All models are validated at construction time. LLM output is always coerced
into RevisionRequest before any execution path is reached.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, validator


class RevisionStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    APPLIED = "applied"
    EXPORTED = "exported"
    REPLIED = "replied"
    FAILED = "failed"
    SKIPPED_DUPLICATE = "skipped_duplicate"


class RevisionEmail(BaseModel):
    """An incoming email that may contain a mix-revision request."""

    message_id: str = Field(..., description="Gmail Message-ID for idempotency")
    thread_id: str = Field(..., description="Gmail thread ID for reply grouping")
    sender: str = Field(..., description="Sender email address")
    subject: str = Field(..., description="Email subject line")
    body_text: str = Field(..., description="Plain-text email body")
    received_at: datetime = Field(..., description="When the email was received")


class RevisionRequest(BaseModel):
    """Structured output from the LLM interpretation step.

    This is the only boundary between untrusted email text and execution.
    Never construct this from raw user input except via the LLM interpreter.
    """

    track_name: str = Field(..., description="Instrument name from the email")
    volume_db: float = Field(..., description="Requested volume change in dB")

    @validator("volume_db")
    def volume_must_be_finite(cls, v: float) -> float:
        import math

        if not math.isfinite(v):
            raise ValueError("volume_db must be a finite number")
        return v


class EditPlan(BaseModel):
    """A validated, safe edit to apply to an Ableton session."""

    baseline_path: Path = Field(..., description="Original .als file path")
    track_name: str = Field(..., description="Matched track name")
    track_index: int = Field(..., description="Position of track in <Tracks>")
    old_linear_gain: float = Field(..., description="Current linear gain value")
    new_linear_gain: float = Field(..., description="Target linear gain value")
    output_path: Path = Field(..., description="New versioned .als output path")
    volume_db_delta: float = Field(..., description="Requested dB delta")
    volume_db_target: float = Field(..., description="Resulting absolute dB")


class RevisionResult(BaseModel):
    """Outcome of processing a single revision email."""

    status: RevisionStatus = RevisionStatus.PENDING
    message_id: str = Field(..., description="Source email Message-ID")
    wav_path: Optional[Path] = None
    als_path: Optional[Path] = None
    error: Optional[str] = None
    track_name: Optional[str] = None
    volume_db: Optional[float] = None

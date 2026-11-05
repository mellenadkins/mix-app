"""Mix Revision Agent — Streamlit UI.

A local desktop application that:
1. Accepts mix-revision instructions via a text input field.
2. Lets the user select an Ableton Live Set (.als) file via a file picker.
3. Applies the track-volume change to the .als file in the background.
4. Provides a download button for the modified .als file.

Run with: streamlit run src/mix_revision_agent/ui/app.py
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import streamlit as st

# Add the src directory to the path so we can import our package
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from mix_revision_agent.ableton.session import (
    apply_volume_edit,
    clamp_db,
    linear_to_db,
    SessionError,
    TrackNotFoundError,
    AmbiguousTrackError,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RevisionRequest:
    """Parsed user revision request."""
    track_name: str
    volume_db: float


@dataclass
class RevisionResult:
    """Result of applying a revision."""
    success: bool
    output_path: Optional[Path] = None
    track_name: Optional[str] = None
    old_db: Optional[float] = None
    new_db: Optional[float] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Text parsing
# ---------------------------------------------------------------------------

def parse_revision_text(text: str) -> RevisionRequest:
    """Parse a plain-text revision request into a structured request.

    Expected format: "Turn up the VOCALS by 3 dB" or similar.
    Extracts track name and dB delta from free text.

    This is intentionally simple — no LLM involved. Uses regex patterns
    to extract the instrument name and numerical dB value.
    """
    text = text.strip()

    # Pattern: [verb] [track name] by [number] dB
    # Examples: "Turn up the VOCALS by 3 dB", "Lower DRUMS by -2 dB",
    #           "Set BASS to 1 dB", "GUITAR +3 dB"
    patterns = [
        # "increase/decrease/turn up/turn down/lower/raise X by Y dB"
        r'(?:turn\s+up|increase|raise|boost)\s+(?:the\s+)?(.+?)\s+by\s+([+-]?\d+(?:\.\d+)?)\s*dB',
        # "decrease/lower X by Y dB" (negative)
        r'(?:turn\s+down|decrease|lower|cut)\s+(?:the\s+)?(.+?)\s+by\s+([+-]?\d+(?:\.\d+)?)\s*dB',
        # "set X to Y dB"
        r'set\s+(?:the\s+)?(.+?)\s+to\s+([+-]?\d+(?:\.\d+)?)\s*dB',
        # "X +Y dB" or "X -Y dB" or "X Y dB"
        r'^(.+?)\s+([+-]?\d+(?:\.\d+)?)\s*dB$',
    ]

    track_name = None
    volume_db = None

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            track_name = match.group(1).strip()
            volume_db = float(match.group(2))
            break

    if track_name is None or volume_db is None:
        raise ValueError(
            "Could not parse revision request. "
            "Expected format like: 'Turn up VOCALS by 3 dB' or 'GUITAR -2 dB'"
        )

    return RevisionRequest(track_name=track_name, volume_db=volume_db)


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

class RevisionProcessor:
    """Processes a revision request against an .als file in a background thread."""

    def __init__(self):
        self.result: Optional[RevisionResult] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        als_path: str,
        track_name: str,
        volume_db_delta: float,
    ) -> None:
        """Start processing in a background thread."""
        if self.is_running():
            return

        self.result = None
        self._thread = threading.Thread(
            target=self._run,
            args=(als_path, track_name, volume_db_delta),
            daemon=True,
        )
        self._thread.start()

    def _run(
        self,
        als_path: str,
        track_name: str,
        volume_db_delta: float,
    ) -> None:
        try:
            plan = apply_volume_edit(
                baseline_path=Path(als_path),
                track_name=track_name,
                volume_db_delta=volume_db_delta,
            )

            with self._lock:
                self.result = RevisionResult(
                    success=True,
                    output_path=plan.output_path,
                    track_name=plan.track_name,
                    old_db=linear_to_db(plan.old_linear_gain),
                    new_db=plan.volume_db_target,
                )
        except (SessionError, TrackNotFoundError, AmbiguousTrackError, ValueError) as exc:
            logger.exception("Revision processing failed")
            with self._lock:
                self.result = RevisionResult(
                    success=False,
                    error=str(exc),
                )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Unexpected error during revision processing")
            with self._lock:
                self.result = RevisionResult(
                    success=False,
                    error=f"Unexpected error: {exc}",
                )

    def get_result(self) -> Optional[RevisionResult]:
        """Get the result if processing is complete, else None."""
        with self._lock:
            return self.result


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Mix Revision Agent",
    page_icon="🎛️",
    layout="centered",
)

st.title("🎛️ Mix Revision Agent")
st.markdown(
    "Enter a mix-revision instruction, select an Ableton Live Set (.als) file, "
    "and download the modified file."
)

# Initialize session state
if "processor" not in st.session_state:
    st.session_state.processor = RevisionProcessor()
if "result_ready" not in st.session_state:
    st.session_state.result_ready = False
if "processing" not in st.session_state:
    st.session_state.processing = False

# ---------------------------------------------------------------------------
# Input form
# ---------------------------------------------------------------------------

st.subheader("1. Enter revision instruction")
revision_text = st.text_area(
    "Mix revision instruction",
    placeholder="e.g. 'Turn up the VOCALS by 3 dB' or 'GUITAR -2 dB'",
    height=100,
    help="Specify the instrument name and the dB change. "
    "Example: 'Turn up the VOCALS by 3 dB'",
)

st.subheader("2. Select .als file")
uploaded_file = st.file_uploader(
    "Choose an Ableton Live Set (.als) file",
    type=["als"],
    help="Select the .als file to modify. The original file is never overwritten.",
)

# ---------------------------------------------------------------------------
# Process button
# ---------------------------------------------------------------------------

process_clicked = st.button("Apply Revision", type="primary", disabled=st.session_state.processing)

if process_clicked:
    if not revision_text.strip():
        st.error("Please enter a revision instruction.")
    elif uploaded_file is None:
        st.error("Please select a .als file.")
    else:
        # Parse the revision text
        try:
            request = parse_revision_text(revision_text)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()

        # Clamp the volume to safe range
        clamped_db = clamp_db(request.volume_db)
        if abs(clamped_db - request.volume_db) > 0.001:
            st.warning(
                f"Volume change clamped from {request.volume_db} dB to {clamped_db} dB "
                f"(safe range: -70 to +6.02 dB)."
            )

        # Save uploaded file to a temp location
        tmp_dir = tempfile.mkdtemp(prefix="mix_revision_")
        tmp_als_path = os.path.join(tmp_dir, uploaded_file.name)
        with open(tmp_als_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        # Start background processing
        st.session_state.processing = True
        st.session_state.result_ready = False
        st.session_state.processor.start(
            als_path=tmp_als_path,
            track_name=request.track_name,
            volume_db_delta=clamped_db,
        )
        st.rerun()

# ---------------------------------------------------------------------------
# Processing status / loading indicator
# ---------------------------------------------------------------------------

if st.session_state.processing:
    st.subheader("3. Processing...")
    with st.spinner("Modifying .als file in the background..."):
        # Check if processing is complete
        result = st.session_state.processor.get_result()
        if result is not None:
            st.session_state.processing = False
            st.session_state.result_ready = True
            st.session_state.last_result = result
            st.rerun()
        else:
            # Still running — rerun to keep the spinner alive
            st.rerun()

# ---------------------------------------------------------------------------
# Result / download
# ---------------------------------------------------------------------------

if st.session_state.result_ready:
    result: RevisionResult = st.session_state.last_result

    st.subheader("3. Result")

    if result.success:
        st.success(
            f"✅ Track '{result.track_name}' volume changed "
            f"from {result.old_db:.2f} dB to {result.new_db:.2f} dB."
        )

        # Read the output file for download
        if result.output_path and result.output_path.exists():
            with open(result.output_path, "rb") as f:
                file_bytes = f.read()

            output_name = result.output_path.name
            st.download_button(
                label="⬇️ Download modified .als file",
                data=file_bytes,
                file_name=output_name,
                mime="application/octet-stream",
                type="primary",
            )
            st.info(
                "The original .als file is unchanged. "
                "The modified file is a new versioned file."
            )
        else:
            st.error("Output file not found.")
    else:
        st.error(f"❌ {result.error}")

    # Reset button
    if st.button("Start over"):
        st.session_state.processing = False
        st.session_state.result_ready = False
        st.session_state.last_result = None
        st.session_state.processor = RevisionProcessor()
        st.rerun()

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.markdown(
    "**Safety notes:**\n"
    "- Original .als files are never overwritten.\n"
    "- Volume changes are clamped to the safe range [-70 dB, +6.02 dB].\n"
    "- Unknown or ambiguous track names are rejected.\n"
    "- All processing happens locally — no files are uploaded to external services."
)

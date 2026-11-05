"""Mix Revision Agent — Streamlit UI (revised MVP).

A local desktop application that:
1. Lets the user upload an Ableton Live Set (.als) file first.
2. Extracts all track names from the .als session file.
3. Displays dropdown menus to construct a volume edit request.
4. Applies the track-volume change to the .als file synchronously.
5. Provides a download button for the modified .als file.

Run with: streamlit run src/mix_revision_agent/ui/app.py
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from mix_revision_agent.ableton.session import (
    apply_volume_edit,
    clamp_db,
    linear_to_db,
    load_session,
    SessionError,
    TrackNotFoundError,
    AmbiguousTrackError,
)

logger = logging.getLogger(__name__)


@dataclass
class RevisionResult:
    success: bool
    output_path: Optional[Path] = None
    track_name: Optional[str] = None
    old_db: Optional[float] = None
    new_db: Optional[float] = None
    error: Optional[str] = None


def extract_track_names(als_path: Path) -> list[str]:
    root = load_session(als_path)
    names: set[str] = set()
    for track in root.findall(".//Tracks/*"):
        name_el = track.find("Name")
        if name_el is None:
            continue
        user_name_el = name_el.find("UserName")
        if user_name_el is not None:
            value = user_name_el.get("Value", "")
            if value:
                names.add(value)
                continue
        effective_name_el = name_el.find("EffectiveName")
        if effective_name_el is not None:
            value = effective_name_el.get("Value", "")
            if value:
                names.add(value)
    return sorted(names)


st.set_page_config(page_title="Mix Revision Agent", page_icon="🎛️", layout="centered")
st.title("🎛️ Mix Revision Agent")

if "track_names" not in st.session_state:
    st.session_state.track_names = []
if "als_tmp_path" not in st.session_state:
    st.session_state.als_tmp_path = None
if "last_result" not in st.session_state:
    st.session_state.last_result = None

# Initialize dropdown defaults in session state
if "dd_track_name" not in st.session_state:
    st.session_state.dd_track_name = None
if "dd_property_name" not in st.session_state:
    st.session_state.dd_property_name = "volume"
if "dd_property_direction" not in st.session_state:
    st.session_state.dd_property_direction = "increase"
if "dd_property_value" not in st.session_state:
    st.session_state.dd_property_value = 1.0

# ---------------------------------------------------------------------------
# Step 1: Upload .als file
# ---------------------------------------------------------------------------

st.subheader("1. Upload .als file")
uploaded_file = st.file_uploader(
    "Choose an Ableton Live Set (.als) file",
    type=["als"],
    help="Select the .als file to modify. The original file is never overwritten.",
)

if uploaded_file is not None:
    tmp_dir = tempfile.mkdtemp(prefix="mix_revision_")
    tmp_als_path = os.path.join(tmp_dir, uploaded_file.name)
    with open(tmp_als_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    if st.session_state.als_tmp_path != tmp_als_path:
        st.session_state.als_tmp_path = tmp_als_path
        st.session_state.last_result = None
        try:
            track_names = extract_track_names(Path(tmp_als_path))
            st.session_state.track_names = track_names
            # Reset track selection to first available track
            if track_names:
                st.session_state.dd_track_name = track_names[0]
        except Exception as exc:
            st.error(f"Failed to read .als file: {exc}")
            st.session_state.track_names = []

# ---------------------------------------------------------------------------
# Step 2: Build your revision request
# ---------------------------------------------------------------------------

if st.session_state.track_names:
    st.subheader("2. Build your revision request")

    # Compute current values from session state (with fallbacks)
    current_track = st.session_state.dd_track_name or st.session_state.track_names[0]
    current_property = st.session_state.dd_property_name or "volume"
    current_direction = st.session_state.dd_property_direction or "increase"
    current_value = st.session_state.dd_property_value or 1.0

    db_delta = current_value if current_direction == "increase" else -current_value

    # Build the structured sentence with colored dynamic words
    sentence_html = (
        "<div style=\"font-size: 26px; line-height: 1.6; margin: 12px 0;\">"
        "<span style=\"color: #222222;\">I want to modify the </span>"
        f"<span style=\"color: #1f77b4; font-weight: bold;\">{current_track}</span>"
        "<span style=\"color: #222222;\"> track. The </span>"
        f"<span style=\"color: #2ca02c; font-weight: bold;\">{current_property}</span>"
        "<span style=\"color: #222222;\"> should </span>"
        f"<span style=\"color: #ff7f0e; font-weight: bold;\">{current_direction}</span>"
        "<span style=\"color: #222222;\"> by </span>"
        f"<span style=\"color: #d62728; font-weight: bold;\">{current_value}</span>"
        "<span style=\"color: #222222;\"> dB.</span>"
        "</div>"
    )
    st.markdown(sentence_html, unsafe_allow_html=True)

    st.divider()

    # Dropdowns below the sentence
    track_name = st.selectbox(
        "Track name",
        options=st.session_state.track_names,
        index=st.session_state.track_names.index(current_track)
        if current_track in st.session_state.track_names
        else 0,
        help="Select the track you want to modify.",
        key="sb_track_name",
    )
    if track_name != st.session_state.dd_track_name:
        st.session_state.dd_track_name = track_name

    property_name = st.selectbox(
        "Property",
        options=["volume"],
        index=0 if current_property == "volume" else 0,
        help="Which track property to modify. Currently only volume is supported.",
        key="sb_property_name",
    )
    if property_name != st.session_state.dd_property_name:
        st.session_state.dd_property_name = property_name

    property_direction = st.selectbox(
        "Direction",
        options=["increase", "decrease"],
        index=0 if current_direction == "increase" else 1,
        help="Whether to increase or decrease the property value.",
        key="sb_property_direction",
    )
    if property_direction != st.session_state.dd_property_direction:
        st.session_state.dd_property_direction = property_direction

    property_values = [round(1.0 + i * 0.1, 1) for i in range(int((6.0 - 1.0) / 0.1) + 1)]
    value_index = property_values.index(current_value) if current_value in property_values else 0
    property_value = st.selectbox(
        "Value (dB)",
        options=property_values,
        index=value_index,
        help=f"Amount in dB ({min(property_values)} to {max(property_values)}).",
        key="sb_property_value",
    )
    if property_value != st.session_state.dd_property_value:
        st.session_state.dd_property_value = property_value

    # Recompute db_delta from final widget values (after any changes)
    db_delta = property_value if property_direction == "increase" else -property_value

    st.subheader("3. Apply revision")
    process_clicked = st.button("Apply Revision", type="primary")

    if process_clicked:
        als_path = st.session_state.als_tmp_path
        if not als_path:
            st.error("No .als file loaded. Please upload a file first.")
        else:
            with st.spinner(f"Applying volume change to '{track_name}'..."):
                try:
                    plan = apply_volume_edit(
                        baseline_path=Path(als_path),
                        track_name=track_name,
                        volume_db_delta=clamp_db(db_delta),
                    )
                    result = RevisionResult(
                        success=True,
                        output_path=plan.output_path,
                        track_name=plan.track_name,
                        old_db=linear_to_db(plan.old_linear_gain),
                        new_db=plan.volume_db_target,
                    )
                    st.session_state.last_result = result
                except (SessionError, TrackNotFoundError, AmbiguousTrackError, ValueError) as exc:
                    logger.exception("Revision processing failed")
                    result = RevisionResult(success=False, error=str(exc))
                    st.session_state.last_result = result

if st.session_state.last_result is not None:
    result: RevisionResult = st.session_state.last_result
    st.subheader("Result")

    if result.success:
        st.success(
            f"✅ Volume change applied to '{result.track_name}': "
            f"{result.old_db:.2f} dB → {result.new_db:.2f} dB."
        )
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
                "The original .als file is unchanged.\n"
                "The modified file is a new versioned file."
            )
        else:
            st.error("Output file not found.")
    else:
        st.error(f"❌ {result.error}")

    if st.button("Start over"):
        st.session_state.last_result = None
        st.session_state.track_names = []
        st.session_state.als_tmp_path = None
        st.session_state.dd_track_name = None
        st.session_state.dd_property_name = "volume"
        st.session_state.dd_property_direction = "increase"
        st.session_state.dd_property_value = 1.0
        st.rerun()

st.divider()
st.markdown(
    "**Safety notes:**\n"
    "- Original .als files are never overwritten.\n"
    "- Volume changes are clamped to the safe range [-70 dB, +6.02 dB].\n"
    "- All processing happens locally — no files are uploaded to external services."
)

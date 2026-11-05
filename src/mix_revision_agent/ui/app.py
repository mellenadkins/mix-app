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
        except Exception as exc:
            st.error(f"Failed to read .als file: {exc}")
            st.session_state.track_names = []

if st.session_state.track_names:
    st.subheader("2. Build your revision request")

    track_name = st.selectbox(
        "Track name",
        options=st.session_state.track_names,
        help="Select the track you want to modify.",
    )

    property_name = st.selectbox(
        "Property",
        options=["volume"],
        help="Which track property to modify. Currently only volume is supported.",
    )

    property_direction = st.selectbox(
        "Direction",
        options=["increase", "decrease"],
        help="Whether to increase or decrease the property value.",
    )

    property_values = [round(1.0 + i * 0.1, 1) for i in range(int((6.0 - 1.0) / 0.1) + 1)]
    property_value = st.selectbox(
        "Value (dB)",
        options=property_values,
        help=f"Amount in dB ({min(property_values)} to {max(property_values)}).",
    )

    db_delta = property_value if property_direction == "increase" else -property_value

    request_string = (
        f"I want to modify the {track_name} track. "
        f"The {property_name} should {property_direction} by {property_value} dB."
    )

    st.markdown("**Your request:**")
    st.info(request_string)

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
        st.rerun()

st.divider()
st.markdown(
    "**Safety notes:**\n"
    "- Original .als files are never overwritten.\n"
    "- Volume changes are clamped to the safe range [-70 dB, +6.02 dB].\n"
    "- All processing happens locally — no files are uploaded to external services."
)

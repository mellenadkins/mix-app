"""Mix Revision Agent — Streamlit UI (revised MVP).

A local desktop application that:
1. Lets the user upload an Ableton Live Set (.als) file first.
2. Extracts all track names from the .als session file.
3. Displays dropdown menus to construct one or more volume edit requests.
4. Applies all track-volume changes to the .als file synchronously in one pass.
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
    apply_volume_edits,
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
    edits: list[tuple[str, float, float, float]] = None  # (track, direction, value, delta)
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
if "revision_count" not in st.session_state:
    st.session_state.revision_count = 1


def _key(idx: int, suffix: str) -> str:
    return f"rev_{idx}_{suffix}"


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
        st.session_state.revision_count = 1
        try:
            track_names = extract_track_names(Path(tmp_als_path))
            st.session_state.track_names = track_names
        except Exception as exc:
            st.error(f"Failed to read .als file: {exc}")
            st.session_state.track_names = []

# ---------------------------------------------------------------------------
# Step 2: Build revision request(s)
# ---------------------------------------------------------------------------

if st.session_state.track_names:
    st.subheader("2. Build your revision request(s)")

    for i in range(st.session_state.revision_count):
        st.markdown(f"### Revision {i + 1}")

        # Read current values from widget keys (instant updates)
        current_track = st.session_state.get(_key(i, "track")) or st.session_state.track_names[0]
        current_property = st.session_state.get(_key(i, "property")) or "volume"
        current_direction = st.session_state.get(_key(i, "direction")) or "increase"
        current_value = st.session_state.get(_key(i, "value")) or 1.0

        # Build the structured sentence with colored dynamic words
        direction_word = current_direction.capitalize()
        sentence_html = (
            "<div style=\"font-size: 26px; line-height: 1.6; margin: 12px 0;\">"
            f"<span style=\"color: #ff7f0e; font-weight: bold;\">{direction_word}</span>"
            "<span style=\"color: #ffffff;\"> the </span>"
            f"<span style=\"color: #2ca02c; font-weight: bold;\">{current_property}</span>"
            "<span style=\"color: #ffffff;\"> of the </span>"
            f"<span style=\"color: #1f77b4; font-weight: bold;\">{current_track}</span>"
            "<span style=\"color: #ffffff;\"> track by </span>"
            f"<span style=\"color: #d62728; font-weight: bold;\">{current_value}</span>"
            "<span style=\"color: #ffffff;\"> dB.</span>"
            "</div>"
        )
        st.markdown(sentence_html, unsafe_allow_html=True)

        st.divider()

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            track_name = st.selectbox(
                "Track name",
                options=st.session_state.track_names,
                index=st.session_state.track_names.index(current_track)
                if current_track in st.session_state.track_names
                else 0,
                key=_key(i, "track"),
            )

        with col2:
            property_name = st.selectbox(
                "Property",
                options=["volume"],
                index=0 if current_property == "volume" else 0,
                key=_key(i, "property"),
            )

        with col3:
            property_direction = st.selectbox(
                "Direction",
                options=["increase", "decrease"],
                index=0 if current_direction == "increase" else 1,
                key=_key(i, "direction"),
            )

        with col4:
            property_values = [round(1.0 + i2 * 0.1, 1) for i2 in range(int((6.0 - 1.0) / 0.1) + 1)]
            value_index = property_values.index(current_value) if current_value in property_values else 0
            property_value = st.selectbox(
                "Value (dB)",
                options=property_values,
                index=value_index,
                key=_key(i, "value"),
            )

        # Add button row: + and remove
        col_add, col_remove = st.columns(2)
        with col_add:
            if st.button("+ Add another revision", key=f"add_{i}"):
                st.session_state.revision_count += 1
                st.rerun()

        with col_remove:
            if st.session_state.revision_count > 1:
                if st.button("Remove this revision", key=f"remove_{i}"):
                    st.session_state.revision_count -= 1
                    st.rerun()

        st.divider()

    st.subheader("3. Apply all revisions")
    process_clicked = st.button("Apply all revisions", type="primary")

    if process_clicked:
        als_path = st.session_state.als_tmp_path
        if not als_path:
            st.error("No .als file loaded. Please upload a file first.")
        else:
            # Collect all edits from widget keys
            edits = []
            old_new_pairs = []
            for i in range(st.session_state.revision_count):
                track_name = st.session_state.get(_key(i, "track")) or st.session_state.track_names[0]
                direction = st.session_state.get(_key(i, "direction")) or "increase"
                value = st.session_state.get(_key(i, "value")) or 1.0
                delta = value if direction == "increase" else -value
                edits.append((track_name, delta))
                old_new_pairs.append((track_name, direction, value, delta))

            with st.spinner(f"Applying {len(edits)} revision(s)..."):
                try:
                    plans = apply_volume_edits(
                        baseline_path=Path(als_path),
                        edits=edits,
                    )
                    result = RevisionResult(
                        success=True,
                        output_path=plans[0].output_path,
                        edits=old_new_pairs,
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
        st.success(f"✅ Applied {len(result.edits)} revision(s).")

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
        st.session_state.revision_count = 1
        st.rerun()

st.divider()
st.markdown(
    "**Safety notes:**\n"
    "- Original .als files are never overwritten.\n"
    "- Volume changes are clamped to the safe range [-70 dB, +6.02 dB].\n"
    "- All revisions are applied in a single pass to one versioned file.\n"
    "- All processing happens locally — no files are uploaded to external services."
)

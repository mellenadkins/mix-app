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
if "revision_editable" not in st.session_state:
    st.session_state.revision_editable = [True]  # first revision is editable


def _key(idx: int, suffix: str) -> str:
    return f"rev_{idx}_{suffix}"

# Track which dropdown is most recently modified
if "last_modified_dropdown" not in st.session_state:
    st.session_state.last_modified_dropdown = None  # None means no dropdown has been modified yet

# Add CSS for highlighted track
st.markdown(
    """
    <style>
    .highlighted-track {
        border: 2px solid #ff7f0e;
        border-radius: 4px;
        padding: 2px 4px;
        background-color: rgba(255, 127, 14, 0.1);
        font-weight: bold;
        font-size: 1.1em;
    }
    </style>
    """,
    unsafe_allow_html=True
)


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
        st.session_state.revision_editable = [True]  # Reset to single editable revision
        # Clear all widget states to avoid stale data
        for key in list(st.session_state.keys()):
            if key.startswith("rev_"):
                del st.session_state[key]
        # Reset last_modified_dropdown to avoid stale highlighting
        st.session_state.last_modified_dropdown = None
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
        # Determine revision title and editability
        is_editable = st.session_state.revision_editable[i]
        revision_label = f"Revision {i + 1}" + (" [Active]" if is_editable else " [Locked]")
        st.markdown(f"### {revision_label}")

        # Read current values from widget keys (instant updates)
        current_track = st.session_state.get(_key(i, "track")) or st.session_state.track_names[0]
        current_property = st.session_state.get(_key(i, "property")) or "volume"
        current_direction = st.session_state.get(_key(i, "direction")) or "increase"
        current_value = st.session_state.get(_key(i, "value")) or 1.0

        # Build the structured sentence with colored dynamic words
        # Highlight the most recently modified dropdown
        highlight_direction = st.session_state.last_modified_dropdown == _key(i, "direction")
        highlight_property = st.session_state.last_modified_dropdown == _key(i, "property")
        highlight_track = st.session_state.last_modified_dropdown == _key(i, "track")
        highlight_value = st.session_state.last_modified_dropdown == _key(i, "value")

        # Set colors based on which dropdown was last modified
        direction_color = "#ff7f0e" if highlight_direction else "#ffffff"
        direction_weight = "bold" if highlight_direction else "normal"

        property_color = "#2ca02c" if highlight_property else "#ffffff"
        property_weight = "bold" if highlight_property else "normal"

        track_color = "#1f77b4" if highlight_track else "#ffffff"
        track_weight = "bold" if highlight_track else "normal"

        value_color = "#d62728" if highlight_value else "#ffffff"
        value_weight = "bold" if highlight_value else "normal"

        # Add highlight class to track name for special highlighting
        track_class = "highlighted-track" if highlight_track else ""

        sentence_html = (
            '<div style="font-size: 26px; line-height: 1.6; margin: 12px 0; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 6px;">'
            f'<span style="color: {direction_color}; font-weight: {direction_weight}; font-size: 32px;">{current_direction.capitalize()}</span>'
            '<span style="color: #ffffff; font-size: 28px; margin: 0 8px;"> the </span>'
            f'<span style="color: {property_color}; font-weight: {property_weight}; font-size: 32px;">{current_property}</span>'
            '<span style="color: #ffffff; font-size: 28px; margin: 0 8px;"> of the </span>'
            f'<span class="{track_class}" style="color: {track_color}; font-weight: {track_weight}; font-size: 36px;">{current_track}</span>'
            '<span style="color: #ffffff; font-size: 28px; margin: 0 8px;"> track by </span>'
            f'<span style="color: {value_color}; font-weight: {value_weight}; font-size: 38px;">{current_value}</span>'
            '<span style="color: #ffffff; font-size: 28px;"> dB.</span>'
            '</div>'
        )
        st.markdown(sentence_html, unsafe_allow_html=True)

        st.divider()

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            if is_editable:
                track_name = st.selectbox(
                    "Track name",
                    options=st.session_state.track_names,
                    index=st.session_state.track_names.index(current_track)
                    if current_track in st.session_state.track_names
                    else 0,
                    key=_key(i, "track"),
                    on_change=lambda: st.session_state.__setattr__(
                        "last_modified_dropdown", _key(i, "track")
                    ) if st.session_state.track_names else None,
                )
            else:
                st.text_input("Track name", value=current_track, disabled=True)
                track_name = current_track

        with col2:
            if is_editable:
                property_name = st.selectbox(
                    "Property",
                    options=["volume"],
                    index=0 if current_property == "volume" else 0,
                    key=_key(i, "property"),
                    on_change=lambda: st.session_state.__setattr__(
                        "last_modified_dropdown", _key(i, "property")
                    ),
                )
            else:
                st.text_input("Property", value=current_property, disabled=True)
                property_name = current_property

        with col3:
            if is_editable:
                property_direction = st.selectbox(
                    "Direction",
                    options=["increase", "decrease"],
                    index=0 if current_direction == "increase" else 1,
                    key=_key(i, "direction"),
                    on_change=lambda: st.session_state.__setattr__(
                        "last_modified_dropdown", _key(i, "direction")
                    ),
                )
            else:
                st.text_input("Direction", value=current_direction, disabled=True)
                property_direction = current_direction

        with col4:
            if is_editable:
                property_values = [round(1.0 + i2 * 0.1, 1) for i2 in range(int((6.0 - 1.0) / 0.1) + 1)]
                value_index = property_values.index(current_value) if current_value in property_values else 0
                property_value = st.selectbox(
                    "Value (dB)",
                    options=property_values,
                    index=value_index,
                    key=_key(i, "value"),
                    on_change=lambda: st.session_state.__setattr__(
                        "last_modified_dropdown", _key(i, "value")
                    ),
                )
            else:
                st.text_input("Value (dB)", value=f"{current_value}", disabled=True)
                property_value = current_value

        # Delete button for locked revisions
        if not is_editable and st.session_state.revision_count > 1:
            if st.button("Remove this revision", key=f"remove_{i}"):
                # Remove from both lists
                st.session_state.revision_count -= 1
                st.session_state.revision_editable.pop(i)
                st.rerun()

        st.divider()

        st.divider()

    # Add a single "Add another revision" button after all revision blocks
    # This allows users to add unlimited revisions
    if st.session_state.revision_editable and st.session_state.revision_editable[-1]:
        if st.button("+ Add another revision"):
            # Add new revision to both lists
            st.session_state.revision_count += 1
            st.session_state.revision_editable.append(True)
            # Reset the new revision to default values
            # The new revision gets index revision_count-1 (since it was just added)
            new_idx = st.session_state.revision_count - 1
            # Set default widget keys for the new revision
            # These will be used when rendering the new revision
            st.session_state[_key(new_idx, "track")] = st.session_state.track_names[0]
            st.session_state[_key(new_idx, "property")] = "volume"
            st.session_state[_key(new_idx, "direction")] = "increase"
            st.session_state[_key(new_idx, "value")] = 1.0
            # Lock all previous revisions except the new one
            for i in range(new_idx):
                st.session_state.revision_editable[i] = False
            # Reset last_modified_dropdown to avoid highlighting stale revisions
            st.session_state.last_modified_dropdown = None
            st.rerun()

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
        st.session_state.revision_editable = [True]  # Reset to single editable revision
        # Clear all widget states to avoid stale data
        for key in list(st.session_state.keys()):
            if key.startswith("rev_"):
                del st.session_state[key]
        # Clear last_modified_dropdown to avoid stale highlighting
        st.session_state.last_modified_dropdown = None
        st.rerun()

st.divider()
st.markdown(
    "**Safety notes:**\n"
    "- Original .als files are never overwritten.\n"
    "- Volume changes are clamped to the safe range [-70 dB, +6.02 dB].\n"
    "- All revisions are applied in a single pass to one versioned file.\n"
    "- All processing happens locally — no files are uploaded to external services."
)

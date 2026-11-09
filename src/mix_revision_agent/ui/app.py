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
if "revisions" not in st.session_state:
    st.session_state.revisions = []  # list of dicts: {track_name, direction, value, timestamp}
if "song_length_seconds" not in st.session_state:
    st.session_state.song_length_seconds = 300  # Default 5 minutes

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
        except Exception as exc:
            st.error(f"Failed to read .als file: {exc}")
            st.session_state.track_names = []

        # Automatically create Revision 1 when a new file is uploaded
        if not st.session_state.revisions and st.session_state.track_names:
            st.session_state.revisions.append({
                "track": st.session_state.track_names[0],
                "property": "volume",
                "direction": "increase",
                "value": 1.0,
                "timestamp": "00:00:00",
            })

# ---------------------------------------------------------------------------
# Step 2: Build revision request(s)
# ---------------------------------------------------------------------------

if st.session_state.track_names:
    st.subheader("2. Build your revision request(s)")

    # Render each revision request
    for idx in range(len(st.session_state.revisions)):
        rev = st.session_state.revisions[idx]
        st.markdown(f"### Revision {idx + 1}")

        # Individual revision inputs
        col_trk, col_prop, col_dir, col_val, col_ts = st.columns(5)

        with col_trk:
            track_name = st.selectbox(
                "Track",
                options=st.session_state.track_names,
                index=st.session_state.track_names.index(rev["track"])
                if rev["track"] in st.session_state.track_names
                else 0,
                key=f"rev_{idx}_track",
            )

        with col_prop:
            property_name = st.selectbox(
                "Property",
                options=["volume"],
                index=0,
                key=f"rev_{idx}_property",
            )

        with col_dir:
            property_direction = st.selectbox(
                "Direction",
                options=["increase", "decrease"],
                index=0 if rev["direction"] == "increase" else 1,
                key=f"rev_{idx}_direction",
            )

        with col_val:
            property_values = [round(1.0 + i * 0.1, 1) for i in range(int((6.0 - 1.0) / 0.1) + 1)]
            value_index = property_values.index(rev["value"]) if rev["value"] in property_values else 0
            property_value = st.selectbox(
                "Value (dB)",
                options=property_values,
                index=value_index,
                key=f"rev_{idx}_value",
            )

        with col_ts:
            # Build timestamp dropdown options: 00:00:00 to song_length_seconds in 1-second increments
            timestamp_options = []
            for sec in range(st.session_state.song_length_seconds + 1):
                mins = sec // 60
                secs = sec % 60
                timestamp_options.append(f"{mins:02d}:{secs:02d}:00")

            current_ts = rev.get("timestamp", "00:00:00")
            try:
                ts_index = timestamp_options.index(current_ts)
            except ValueError:
                ts_index = 0
            property_timestamp = st.selectbox(
                "Timestamp",
                options=timestamp_options,
                index=ts_index,
                key=f"rev_{idx}_timestamp",
            )

        # Update session state with current widget values
        rev["track"] = track_name
        rev["property"] = property_name
        rev["direction"] = property_direction
        rev["value"] = property_value
        rev["timestamp"] = property_timestamp

        # Build colored sentence for this revision
        direction_color = "#ff7f0e" if rev["direction"] == "increase" else "#2ca02c"
        property_color = "#2ca02c" if rev["property"] == "volume" else "#ffffff"
        track_color = "#1f77b4"
        value_color = "#d62728"
        direction_word = rev["direction"].capitalize()

        sentence_html = (
            '<div style="font-size: 20px; line-height: 1.6; margin: 8px 0; '
            'padding: 8px; background: rgba(255,255,255,0.05); border-radius: 6px;">'
            f'<span style="color: #ffffff;">I want to modify the </span>'
            f'<span style="color: {track_color}; font-weight: bold;">{rev["track"]}</span>'
            f'<span style="color: #ffffff;"> track. The </span>'
            f'<span style="color: {property_color}; font-weight: bold;">{rev["property"]}</span>'
            f'<span style="color: #ffffff;"> should </span>'
            f'<span style="color: {direction_color}; font-weight: bold;">{direction_word}</span>'
            f'<span style="color: #ffffff;"> by </span>'
            f'<span style="color: {value_color}; font-weight: bold;">{rev["value"]}</span>'
            f'<span style="color: #ffffff;"> dB at </span>'
            f'<span style="color: #ff7f0e; font-weight: bold;">{rev["timestamp"]}</span>'
            "</div>"
        )
        st.markdown(sentence_html, unsafe_allow_html=True)
        st.divider()

    # "+ Add Revision" button (below all revisions)
    if st.button("+ Add Revision", key="add_revision"):
        st.session_state.revisions.append({
            "track": st.session_state.track_names[0] if st.session_state.track_names else "",
            "property": "volume",
            "direction": "increase",
            "value": 1.0,
            "timestamp": "00:00:00",
        })
        st.rerun()

    # "Remove Last Revision" button (only if more than 1 revision)
    if len(st.session_state.revisions) > 1:
        if st.button("− Remove Last Revision", key="remove_revision"):
            st.session_state.revisions.pop()
            st.rerun()

    # Compute all edits for processing
    edits = []
    for idx in range(len(st.session_state.revisions)):
        rev = st.session_state.revisions[idx]
        delta = rev["value"] if rev["direction"] == "increase" else -rev["value"]
        edits.append((
            rev["track"],
            rev["direction"],
            rev["value"],
            rev.get("timestamp", "00:00:00"),  # backward compatible for older revisions
        ))

    st.subheader("3. Apply all revisions")
    process_clicked = st.button("Apply All Revisions", type="primary")

    if process_clicked:
        # Print requested changes to console
        print(f"\n{'='*60}")
        print("REVISION REQUESTS TO APPLY:")
        print(f"{'='*60}")
        for idx, rev in enumerate(st.session_state.revisions, 1):
            direction_word = rev["direction"].capitalize()
            print(f"  {idx}. Track: {rev['track']}, {direction_word} {rev['value']} dB at {rev['timestamp']}")
        print(f"{'='*60}\n")

        als_path = st.session_state.als_tmp_path
        if not als_path:
            st.error("No .als file loaded. Please upload a file first.")
        elif not edits:
            st.error("No revisions defined. Add at least one revision.")
        else:
            with st.spinner(f"Applying {len(edits)} revision(s)..."):
                try:
                    plans = apply_volume_edits(
                        baseline_path=Path(als_path),
                        edits=edits,
                    )
                    results = []
                    for plan in plans:
                        old_db = linear_to_db(plan.old_linear_gain)
                        new_db = plan.volume_db_target
                        results.append(RevisionResult(
                            success=True,
                            output_path=plan.output_path,
                            track_name=plan.track_name,
                            old_db=old_db,
                            new_db=new_db,
                        ))
                    st.session_state.last_result = results
                except (SessionError, TrackNotFoundError, AmbiguousTrackError, ValueError) as exc:
                    logger.exception("Revision processing failed")
                    st.session_state.last_result = None
                    st.error(f"Revision processing failed: {exc}")

# Display results
if st.session_state.last_result:
    results = st.session_state.last_result
    if isinstance(results, list):
        st.subheader("Results")

        all_success = all(r.success for r in results)
        if all_success:
            st.success(f"✅ Applied {len(results)} revision(s).")
            for r in results:
                st.write(
                    f"• {r.track_name}: {r.old_db:.2f} dB → {r.new_db:.2f} dB"
                )
        else:
            st.error("Some revisions failed:")
            for r in results:
                if r.success:
                    st.success(f"✅ {r.track_name}: {r.old_db:.2f} dB → {r.new_db:.2f} dB.")
                else:
                    st.error(f"❌ {r.track_name}: {r.error}")

        if results and results[0].output_path and results[0].output_path.exists():
            output_path = results[0].output_path
            with open(output_path, "rb") as f:
                file_bytes = f.read()
            output_name = output_path.name
            st.download_button(
                label="⬇️ Download modified .als file",
                data=file_bytes,
                file_name=output_name,
                mime="application/octet-stream",
                type="primary",
            )
            st.info(
                "The original .als file is unchanged.\n"
                "The modified file is a new versioned file with all revisions applied."
            )
    else:
        # Legacy single result (backward compatibility)
        result = results
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
            st.error(f"❌ Revision failed: {result.error}")

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

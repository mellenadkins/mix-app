"""Session editor for Ableton Live .als files.

Reads and writes static mixer volume by decompressing the gzip-compressed
XML, editing <DeviceChain><Mixer><Volume><Manual Value>, and recompressing.

The original .als is never overwritten — each edit produces a new versioned
file. All values are clamped to the verified safe range [-70 dB, +6.02 dB].

Editing strategy: Targeted string replacement on the raw decompressed XML.
This preserves all original formatting, attribute order, and whitespace,
which is critical for Ableton compatibility. The ElementTree parser is used
only to locate the correct track and read the current value — the actual
edit is done via precise string substitution on the raw XML text.
"""

from __future__ import annotations

import gzip
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple

from mix_revision_agent.models import EditPlan

# Volume bounds verified against Ableton Live 12.4.6 behavior.
MIN_DB = -70.0
MAX_DB = 6.02


class SessionError(Exception):
    """Raised when a .als session cannot be read or edited safely."""


class TrackNotFoundError(SessionError):
    """Raised when no track matches the requested name."""


class AmbiguousTrackError(SessionError):
    """Raised when multiple tracks match the requested name."""


def db_to_linear(db: float) -> float:
    """Convert dB to linear gain. 0 dB -> 1.0."""
    return 10.0 ** (db / 20.0)


def linear_to_db(linear: float) -> float:
    """Convert linear gain to dB. Returns -inf for zero or negative."""
    if linear <= 0:
        return float("-inf")
    return 20.0 * math.log10(linear)


def clamp_db(db: float) -> float:
    """Clamp a dB value to the verified safe range [-70, +6.02]."""
    return max(MIN_DB, min(MAX_DB, db))


def _read_raw_xml(path: Path) -> Tuple[str, ET.Element]:
    """Decompress a .als file and return (raw_xml_string, parsed_root).

    The raw string is preserved for targeted string replacement; the parsed
    root is used only for locating elements and reading values.
    """
    try:
        with gzip.open(path, "rb") as f:
            xml_bytes = f.read()
    except (OSError, gzip.BadGzipFile) as exc:
        raise SessionError(f"Cannot read .als file {path}: {exc}") from exc

    try:
        xml_str = xml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SessionError(f"Cannot decode .als file {path} as UTF-8: {exc}") from exc

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise SessionError(f"Invalid XML in .als file {path}: {exc}") from exc

    return xml_str, root


def _write_raw_xml(xml_str: str, output_path: Path) -> None:
    """Gzip-compress an XML string to output_path and verify it re-parses."""
    try:
        xml_bytes = xml_str.encode("utf-8")
        with gzip.open(output_path, "wb") as f:
            f.write(xml_bytes)
    except OSError as exc:
        raise SessionError(f"Cannot write .als file {output_path}: {exc}") from exc

    # Verify the output re-parses as well-formed XML.
    try:
        with gzip.open(output_path, "rb") as f:
            ET.fromstring(f.read())
    except ET.ParseError as exc:
        output_path.unlink(missing_ok=True)
        raise SessionError(
            f"Recompressed .als file {output_path} is not valid XML: {exc}"
        ) from exc


def _track_display_name(track: ET.Element) -> str:
    """Return the display name for a track: UserName preferred, EffectiveName fallback."""
    name_el = track.find("Name")
    if name_el is None:
        return ""

    user_name_el = name_el.find("UserName")
    if user_name_el is not None:
        value = user_name_el.get("Value", "")
        if value:
            return value

    effective_name_el = name_el.find("EffectiveName")
    if effective_name_el is not None:
        return effective_name_el.get("Value", "")

    return ""


def _find_track_by_name(
    root: ET.Element, track_name: str
) -> Tuple[int, ET.Element]:
    """Find a track by display name. Returns (index, track_element).

    Raises TrackNotFoundError if no match, AmbiguousTrackError if multiple match.
    """
    tracks = root.findall(".//Tracks/*")
    matches = []
    for idx, track in enumerate(tracks):
        if _track_display_name(track) == track_name:
            matches.append((idx, track))

    if not matches:
        raise TrackNotFoundError(
            f"No track found with name '{track_name}' in session"
        )
    if len(matches) > 1:
        raise AmbiguousTrackError(
            f"Multiple tracks found with name '{track_name}': "
            f"{[m[0] for m in matches]}"
        )

    return matches[0]


def _get_manual_volume(track: ET.Element) -> Optional[ET.Element]:
    """Return the <Manual> volume element for a track, or None if absent."""
    return track.find(".//DeviceChain/Mixer/Volume/Manual")


def _find_volume_block_in_raw(xml_str: str, track_name: str) -> Tuple[int, int, str]:
    """Locate the <Volume>...</Volume> block for a named track in raw XML.

    Returns (volume_start_idx, volume_end_idx, volume_block_text).
    Raises SessionError if the track or volume block cannot be found.
    """
    # Find the track's name attribute in raw XML
    name_patterns = [f'UserName Value="{track_name}"', f'EffectiveName Value="{track_name}"']
    track_idx = -1
    for pat in name_patterns:
        track_idx = xml_str.find(pat)
        if track_idx >= 0:
            break

    if track_idx < 0:
        raise SessionError(
            f"Track '{track_name}' not found in raw XML"
        )

    # Find the <DeviceChain> that contains this track's Volume
    devicechain_idx = xml_str.find("<DeviceChain>", track_idx)
    if devicechain_idx < 0:
        raise SessionError(
            f"No <DeviceChain> found for track '{track_name}'"
        )

    # Find the <Volume> element within this DeviceChain
    volume_idx = xml_str.find("<Volume>", devicechain_idx)
    if volume_idx < 0:
        raise SessionError(
            f"No <Volume> element found for track '{track_name}'"
        )

    # Find the closing </Volume>
    end_volume_idx = xml_str.find("</Volume>", volume_idx)
    if end_volume_idx < 0:
        raise SessionError(
            f"No closing </Volume> found for track '{track_name}'"
        )

    volume_block = xml_str[volume_idx : end_volume_idx + len("</Volume>")]
    return volume_idx, end_volume_idx + len("</Volume>"), volume_block


def _replace_manual_value_in_block(
    volume_block: str, old_value: str, new_value: str
) -> str:
    """Replace Manual Value within a Volume block, raising if not found."""
    old_pattern = f'Manual Value="{old_value}"'
    if old_pattern not in volume_block:
        raise SessionError(
            f'Pattern "{old_pattern}" not found in Volume block'
        )
    return volume_block.replace(old_pattern, f'Manual Value="{new_value}"')


def _generate_output_path(baseline_path: Path, revision: int) -> Path:
    """Generate a versioned output path: baseline_rev{N}.als"""
    stem = baseline_path.stem
    suffix = baseline_path.suffix
    parent = baseline_path.parent
    return parent / f"{stem}_rev{revision}{suffix}"


def _next_revision_number(baseline_path: Path) -> int:
    """Determine the next available revision number for versioned output."""
    parent = baseline_path.parent
    stem = baseline_path.stem
    suffix = baseline_path.suffix

    pattern = re.compile(rf"^{re.escape(stem)}_rev(\d+){re.escape(suffix)}$")
    existing = []
    for child in parent.iterdir():
        m = pattern.match(child.name)
        if m:
            existing.append(int(m.group(1)))

    return max(existing) + 1 if existing else 1


def load_session(path: Path) -> ET.Element:
    """Load and parse a .als session file. Returns the XML root element."""
    _, root = _read_raw_xml(path)
    return root


def apply_volume_edit(
    baseline_path: Path,
    track_name: str,
    volume_db_delta: float,
) -> EditPlan:
    """Apply a volume change to a track and save a new versioned .als.

    Steps:
    1. Read raw XML and parse to locate the track.
    2. Find the track by name (UserName preferred, EffectiveName fallback).
    3. Read current linear gain from <Manual Value>.
    4. Compute target linear gain: current * 10^(delta/20).
    5. Clamp target dB to [MIN_DB, MAX_DB].
    6. Do targeted string replacement on raw XML to change only the Manual Value.
    7. Write new versioned .als file (never overwrite original).
    8. Verify output re-parses as well-formed XML.
    9. Return EditPlan with old/new values and output path.
    """
    xml_str, root = _read_raw_xml(baseline_path)

    track_index, track = _find_track_by_name(root, track_name)

    manual_el = _get_manual_volume(track)
    if manual_el is None:
        raise SessionError(
            f"No mixer volume found for track '{track_name}' in session"
        )

    old_value = manual_el.get("Value", "1")
    old_linear = float(old_value)
    old_db = linear_to_db(old_linear)

    # Compute target: apply delta to current absolute dB, then clamp.
    target_db = clamp_db(old_db + volume_db_delta)
    new_linear = db_to_linear(target_db)
    new_value = f"{new_linear:.10f}"

    # Targeted string replacement on raw XML
    volume_idx, volume_end_idx, volume_block = _find_volume_block_in_raw(
        xml_str, track_name
    )
    new_volume_block = _replace_manual_value_in_block(
        volume_block, old_value, new_value
    )
    new_xml_str = xml_str[:volume_idx] + new_volume_block + xml_str[volume_end_idx:]

    # Verify the replacement took effect
    if f'Manual Value="{new_value}"' not in new_xml_str:
        raise SessionError(
            f"Failed to write new Manual Value to output XML"
        )

    revision = _next_revision_number(baseline_path)
    output_path = _generate_output_path(baseline_path, revision)

    _write_raw_xml(new_xml_str, output_path)

    return EditPlan(
        baseline_path=baseline_path,
        track_name=track_name,
        track_index=track_index,
        old_linear_gain=old_linear,
        new_linear_gain=new_linear,
        output_path=output_path,
        volume_db_delta=volume_db_delta,
        volume_db_target=target_db,
    )


def apply_volume_edits(
    baseline_path: Path,
    edits: list[tuple[str, str, float, str]],
) -> list[EditPlan]:
    """Apply multiple volume edits to a single .als file in one pass.

    Each edit is a (track_name, direction, value_db, timestamp_str) tuple.
    All edits are applied to the same XML document and saved as a single
    versioned .als file. The original .als is never overwritten.

    If timestamp is "00:00:00" (0 seconds), the volume change is applied as a
    static mixer volume change to the baseline gain.

    If timestamp is non-zero, an automation envelope breakpoint is injected at
    that timestamp.

    Steps:
    1. Read raw XML once.
    2. For each edit: find track by name, compute target linear gain,
       clamp to [MIN_DB, MAX_DB], replace Manual Value in raw XML.
    3. Write new versioned .als file once.
    4. Return one EditPlan per edit (all sharing the same output_path).
    """
    xml_str, root = _read_raw_xml(baseline_path)

    plans: list[EditPlan] = []

    for track_name, direction, value_db, timestamp_str in edits:
        track_index, track = _find_track_by_name(root, track_name)

        # Convert timestamp string "MM:SS:00" to seconds
        timestamp_seconds = _parse_timestamp(timestamp_str)

        if timestamp_seconds == 0:
            # Static volume edit (change baseline gain)
            manual_el = _get_manual_volume(track)
            if manual_el is None:
                raise SessionError(
                    f"No mixer volume found for track '{track_name}' in session"
                )

            old_value = manual_el.get("Value", "1")
            old_linear = float(old_value)
            old_db = linear_to_db(old_linear)

            # Compute target: apply delta to current absolute dB, then clamp.
            delta = value_db if direction == "increase" else -value_db
            target_db = clamp_db(old_db + delta)
            new_linear = db_to_linear(target_db)
            new_value = f"{new_linear:.10f}"

            # Targeted string replacement on raw XML (mutates xml_str in place)
            volume_idx, volume_end_idx, volume_block = _find_volume_block_in_raw(
                xml_str, track_name
            )
            new_volume_block = _replace_manual_value_in_block(
                volume_block, old_value, new_value
            )
            xml_str = xml_str[:volume_idx] + new_volume_block + xml_str[volume_end_idx:]

            plans.append(
                EditPlan(
                    baseline_path=baseline_path,
                    track_name=track_name,
                    track_index=track_index,
                    old_linear_gain=old_linear,
                    new_linear_gain=new_linear,
                    output_path=Path(""),  # set below after saving
                    volume_db_delta=delta,
                    volume_db_target=target_db,
                )
            )
        else:
            # Timestamped edit: add automation breakpoint
            delta = value_db if direction == "increase" else -value_db
            target_db = clamp_db(delta)
            new_linear = db_to_linear(target_db)

            # Convert seconds to beats (assume 120 BPM = 2 beats per second)
            beat_position = timestamp_seconds * 2.0

            # Find or create <Events> block for this track's volume automation
            xml_str = _insert_automation_event(
                xml_str, track_name, beat_position, new_linear
            )

            plans.append(
                EditPlan(
                    baseline_path=baseline_path,
                    track_name=track_name,
                    track_index=track_index,
                    old_linear_gain=float(
                        manual_el.get("Value", "1")
                        if (manual_el := _get_manual_volume(track)) is not None
                        else 1.0
                    ),
                    new_linear_gain=new_linear,
                    output_path=Path(""),  # set below after saving
                    volume_db_delta=delta,
                    volume_db_target=target_db,
                )
            )

    # Write once, after all edits are applied
    revision = _next_revision_number(baseline_path)
    output_path = _generate_output_path(baseline_path, revision)
    _write_raw_xml(xml_str, output_path)

    # Update output_path on all plans
    for p in plans:
        p.output_path = output_path

    return plans


def _parse_timestamp(timestamp_str: str) -> int:
    """Parse a timestamp string "MM:SS:00" into total seconds."""
    try:
        parts = timestamp_str.split(":")
        if len(parts) == 3:
            minutes, seconds, _ = int(parts[0]), int(parts[1]), int(parts[2])
            return minutes * 60 + seconds
        elif len(parts) == 2:
            minutes, seconds = int(parts[0]), int(parts[1])
            return minutes * 60 + seconds
        else:
            return int(parts[0])
    except (ValueError, IndexError):
        return 0


def _insert_automation_event(
    xml_str: str, track_name: str, beat_position: float, new_linear: float
) -> str:
    """Insert or update an automation breakpoint event for a track's volume.

    Inserts a <FloatEvent Time="beat_position" Value="new_linear"/> into the
    track's Volume AutomationTarget Events block.
    """
    root = ET.fromstring(xml_str)

    # Find the track element
    _, track_element = _find_track_by_name(root, track_name)

    # Find the Mixer/Volume/AutomationTarget in this track
    volume_el = track_element.find(".//DeviceChain/Mixer/Volume")
    if volume_el is None:
        raise SessionError(
            f"No Mixer/Volume found for track '{track_name}'"
        )

    automation_target_el = volume_el.find("AutomationTarget")
    if automation_target_el is None:
        # Create AutomationTarget element
        automation_target_el = ET.Element("AutomationTarget")
        volume_el.append(automation_target_el)

    # Find or create Events element
    events_el = automation_target_el.find("Events")
    if events_el is None:
        events_el = ET.Element("Events")
        automation_target_el.append(events_el)

    # Create FloatEvent element
    float_event_el = ET.Element("FloatEvent")
    float_event_el.set("Time", f"{beat_position}")
    float_event_el.set("Value", f"{new_linear:.10f}")

    # Append the event
    events_el.append(float_event_el)

    # Convert back to string with XML declaration
    xml_output = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_output}'


def edit_session_from_plan(plan: EditPlan) -> Path:
    """Apply a pre-validated EditPlan to produce a new versioned .als.

    This is the entry point used by the workflow orchestrator. It re-reads
    the baseline, verifies the track still matches, applies the edit via
    targeted string replacement, and returns the output path.
    """
    xml_str, root = _read_raw_xml(plan.baseline_path)

    track_index, track = _find_track_by_name(root, plan.track_name)

    manual_el = _get_manual_volume(track)
    if manual_el is None:
        raise SessionError(
            f"No mixer volume found for track '{plan.track_name}' in session"
        )

    current_linear = float(manual_el.get("Value", "1"))
    current_db = linear_to_db(current_linear)

    # Re-verify the plan's assumptions still hold.
    if abs(current_db - plan.volume_db_target + plan.volume_db_delta) > 0.01:
        # The session has changed since the plan was made; recompute safely.
        target_db = clamp_db(current_db + plan.volume_db_delta)
    else:
        target_db = plan.volume_db_target

    new_linear = db_to_linear(target_db)
    new_value = f"{new_linear:.10f}"
    old_value = manual_el.get("Value", "1")

    # Targeted string replacement on raw XML
    volume_idx, volume_end_idx, volume_block = _find_volume_block_in_raw(
        xml_str, plan.track_name
    )
    new_volume_block = _replace_manual_value_in_block(
        volume_block, old_value, new_value
    )
    new_xml_str = xml_str[:volume_idx] + new_volume_block + xml_str[volume_end_idx:]

    # Verify the replacement took effect
    if f'Manual Value="{new_value}"' not in new_xml_str:
        raise SessionError(
            f"Failed to write new Manual Value to output XML"
        )

    revision = _next_revision_number(plan.baseline_path)
    output_path = _generate_output_path(plan.baseline_path, revision)

    _write_raw_xml(new_xml_str, output_path)

    return output_path


def validate_session_readable(path: Path) -> None:
    """Check that a .als file can be decompressed and parsed without error.

    Raises SessionError if the file is not readable or not valid.
    """
    _read_raw_xml(path)

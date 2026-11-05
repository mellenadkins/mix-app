"""Unit tests for the Ableton .als session editor."""

from __future__ import annotations

import gzip
import math
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mix_revision_agent.ableton.session import (
    apply_volume_edit,
    clamp_db,
    db_to_linear,
    linear_to_db,
    load_session,
    validate_session_readable,
)
from mix_revision_agent.ableton.session import (
    SessionError,
    TrackNotFoundError,
    AmbiguousTrackError,
)

FIXTURE_PATH = Path(
    "/Users/mellenadkins/Desktop/dev/mix-app/"
    "tests/fixtures/ableton/"
    "mix-notes-ableton-project Project/"
    "mix-notes-ableton-project.als"
)


def _build_minimal_als(track_name: str, linear_gain: str = "1") -> bytes:
    """Build a minimal valid .als XML document in memory."""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0">
  <LiveSet>
    <Tracks>
      <AudioTrack Id="1">
        <Name>
          <UserName Value="{track_name}" />
          <EffectiveName Value="{track_name}" />
        </Name>
        <DeviceChain>
          <Mixer>
            <Volume>
              <LomId Value="0" />
              <Manual Value="{linear_gain}" />
              <MidiControllerRange>
                <Min Value="0.0003162277571" />
                <Max Value="1.99526238" />
              </MidiControllerRange>
              <AutomationTarget Id="100">
                <LockEnvelope Value="0" />
              </AutomationTarget>
              <ModulationTarget Id="101">
                <LockEnvelope Value="0" />
              </ModulationTarget>
            </Volume>
          </Mixer>
        </DeviceChain>
      </AudioTrack>
    </Tracks>
  </LiveSet>
</Ableton>
""".encode("utf-8")
    return xml


def _write_temp_als(tmp_path: Path, name: str, content: bytes) -> Path:
    """Write content as gzip-compressed .als file (real .als format)."""
    p = tmp_path / name
    p.write_bytes(gzip.compress(content))
    return p


# ---- dB / linear conversion ----

def test_db_to_linear_unity():
    assert abs(db_to_linear(0.0) - 1.0) < 1e-12


def test_db_to_linear_minus6():
    assert abs(db_to_linear(-6.0) - 0.5011872336) < 1e-9


def test_db_to_linear_plus6():
    # 6 dB -> linear 1.995262315; 6.02 dB -> ~1.99986
    assert abs(db_to_linear(6.0) - 1.995262315) < 1e-6
    assert abs(db_to_linear(6.02) - 1.999861870) < 1e-6


def test_linear_to_db_unity():
    assert abs(linear_to_db(1.0) - 0.0) < 1e-12


def test_linear_to_db_minus6():
    assert abs(linear_to_db(0.5) - (-6.020599913)) < 1e-6


def test_linear_to_db_zero_is_neg_inf():
    assert linear_to_db(0.0) == float("-inf")


# ---- Clamping ----

def test_clamp_db_within_bounds():
    assert clamp_db(-10.0) == -10.0
    assert clamp_db(0.0) == 0.0
    assert clamp_db(6.0) == 6.0


def test_clamp_db_below_min():
    assert clamp_db(-80.0) == -70.0


def test_clamp_db_above_max():
    assert clamp_db(20.0) == 6.02


# ---- Session loading ----

def test_load_session_from_fixture():
    root = load_session(FIXTURE_PATH)
    assert root.tag == "Ableton"
    assert root.attrib["Creator"] == "Ableton Live 12.4.16"


def test_validate_session_readable():
    validate_session_readable(FIXTURE_PATH)


def test_validate_session_missing(tmp_path):
    missing = tmp_path / "does_not_exist.als"
    with pytest.raises(SessionError):
        validate_session_readable(missing)


def test_validate_session_not_gzip(tmp_path):
    bad = tmp_path / "not_als.als"
    bad.write_bytes(b"this is not gzip data")
    with pytest.raises(SessionError):
        validate_session_readable(bad)


# ---- Track name matching ----

def test_find_track_by_name_success(tmp_path):
    content = _build_minimal_als("VOCALS", "1")
    path = _write_temp_als(tmp_path, "test.als", content)
    root = load_session(path)

    from mix_revision_agent.ableton.session import _find_track_by_name
    idx, track = _find_track_by_name(root, "VOCALS")
    assert idx == 0
    assert track.tag == "AudioTrack"


def test_find_track_not_found(tmp_path):
    content = _build_minimal_als("VOCALS", "1")
    path = _write_temp_als(tmp_path, "test.als", content)
    root = load_session(path)

    from mix_revision_agent.ableton.session import _find_track_by_name
    with pytest.raises(TrackNotFoundError):
        _find_track_by_name(root, "BASS")


def test_find_track_ambiguous(tmp_path):
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0">
  <LiveSet>
    <Tracks>
      <AudioTrack Id="1">
        <Name><UserName Value="VOCALS" /><EffectiveName Value="VOCALS" /></Name>
        <DeviceChain><Mixer><Volume><Manual Value="1" /></Volume></Mixer></DeviceChain>
      </AudioTrack>
      <AudioTrack Id="2">
        <Name><UserName Value="VOCALS" /><EffectiveName Value="VOCALS" /></Name>
        <DeviceChain><Mixer><Volume><Manual Value="1" /></Volume></Mixer></DeviceChain>
      </AudioTrack>
    </Tracks>
  </LiveSet>
</Ableton>
""".encode("utf-8")
    path = _write_temp_als(tmp_path, "ambiguous.als", xml)
    root = load_session(path)

    from mix_revision_agent.ableton.session import _find_track_by_name
    with pytest.raises(AmbiguousTrackError):
        _find_track_by_name(root, "VOCALS")


def test_track_name_fallback_to_effective_name(tmp_path):
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0">
  <LiveSet>
    <Tracks>
      <AudioTrack Id="1">
        <Name><UserName Value="" /><EffectiveName Value="A-Reverb" /></Name>
        <DeviceChain><Mixer><Volume><Manual Value="1" /></Volume></Mixer></DeviceChain>
      </AudioTrack>
    </Tracks>
  </LiveSet>
</Ableton>
""".encode("utf-8")
    path = _write_temp_als(tmp_path, "test.als", xml)
    root = load_session(path)

    from mix_revision_agent.ableton.session import _find_track_by_name
    idx, track = _find_track_by_name(root, "A-Reverb")
    assert idx == 0


# ---- Volume edit ----

def test_apply_volume_edit_minus6_db(tmp_path):
    content = _build_minimal_als("VOCALS", "1")
    path = _write_temp_als(tmp_path, "session.als", content)

    plan = apply_volume_edit(path, "VOCALS", -6.0)

    assert plan.track_name == "VOCALS"
    assert plan.old_linear_gain == 1.0
    assert abs(plan.new_linear_gain - 0.5011872336) < 1e-9
    assert abs(plan.volume_db_target - (-6.0)) < 1e-9
    assert plan.output_path.parent == path.parent
    assert plan.output_path.stem == "session_rev1"
    assert plan.output_path.suffix == ".als"
    assert plan.output_path.exists()

    root = load_session(plan.output_path)
    manual = root.find(".//DeviceChain/Mixer/Volume/Manual")
    assert manual is not None
    val = float(manual.get("Value"))
    assert abs(20 * math.log10(val) - (-6.0)) < 0.01


def test_apply_volume_edit_clamps_to_max(tmp_path):
    content = _build_minimal_als("VOCALS", "1")
    path = _write_temp_als(tmp_path, "session.als", content)

    plan = apply_volume_edit(path, "VOCALS", 20.0)

    assert abs(plan.volume_db_target - 6.02) < 1e-9
    assert abs(plan.new_linear_gain - db_to_linear(6.02)) < 1e-9


def test_apply_volume_edit_clamps_to_min(tmp_path):
    content = _build_minimal_als("VOCALS", "1")
    path = _write_temp_als(tmp_path, "session.als", content)

    plan = apply_volume_edit(path, "VOCALS", -100.0)

    assert abs(plan.volume_db_target - (-70.0)) < 1e-9


def test_apply_volume_edit_creates_versioned_files(tmp_path):
    content = _build_minimal_als("VOCALS", "1")
    path = _write_temp_als(tmp_path, "session.als", content)

    apply_volume_edit(path, "VOCALS", -3.0)
    apply_volume_edit(path, "VOCALS", -3.0)
    apply_volume_edit(path, "VOCALS", -3.0)

    for n in (1, 2, 3):
        rev = tmp_path / f"session_rev{n}.als"
        assert rev.exists()
        root = load_session(rev)
        manual = root.find(".//DeviceChain/Mixer/Volume/Manual")
        val = float(manual.get("Value"))
        db = 20 * math.log10(val)
        assert abs(db - (-3.0)) < 0.01


def test_apply_volume_edit_does_not_overwrite_baseline(tmp_path):
    content = _build_minimal_als("VOCALS", "1")
    path = _write_temp_als(tmp_path, "session.als", content)

    apply_volume_edit(path, "VOCALS", -6.0)

    root = load_session(path)
    manual = root.find(".//DeviceChain/Mixer/Volume/Manual")
    assert float(manual.get("Value")) == 1.0


def test_apply_volume_edit_track_not_found(tmp_path):
    content = _build_minimal_als("VOCALS", "1")
    path = _write_temp_als(tmp_path, "session.als", content)

    with pytest.raises(TrackNotFoundError):
        apply_volume_edit(path, "BASS", -6.0)


# ---- Round-trip against real Ableton fixture ----

@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason="Real .als fixture not available",
)
def test_round_trip_against_real_fixture(tmp_path):
    import shutil

    working = tmp_path / "mix-notes-ableton-project.als"
    shutil.copy(FIXTURE_PATH, working)

    plan = apply_volume_edit(working, "GUITAR", 1.0)

    assert plan.output_path.exists()
    assert plan.track_name == "GUITAR"
    assert abs(plan.volume_db_target - 1.0) < 0.01

    root = load_session(plan.output_path)
    # Find GUITAR track's volume specifically
    guitar_manual = None
    for track in root.findall(".//Tracks/*"):
        name_el = track.find("Name")
        if name_el is None:
            continue
        un = name_el.find("UserName")
        en = name_el.find("EffectiveName")
        uname = un.attrib.get("Value", "") if un is not None else ""
        ename = en.attrib.get("Value", "") if en is not None else ""
        if uname == "GUITAR" or ename == "GUITAR":
            guitar_manual = track.find(".//DeviceChain/Mixer/Volume/Manual")
            break
    assert guitar_manual is not None
    val = float(guitar_manual.get("Value"))
    db = 20 * math.log10(val)
    assert abs(db - 1.0) < 0.01

    # Baseline unchanged — GUITAR still at 0 dB in the original
    root_orig = load_session(working)
    for track in root_orig.findall(".//Tracks/*"):
        name_el = track.find("Name")
        if name_el is None:
            continue
        un = name_el.find("UserName")
        en = name_el.find("EffectiveName")
        uname = un.attrib.get("Value", "") if un is not None else ""
        ename = en.attrib.get("Value", "") if en is not None else ""
        if uname == "GUITAR" or ename == "GUITAR":
            orig_manual = track.find(".//DeviceChain/Mixer/Volume/Manual")
            assert float(orig_manual.get("Value")) == 1.0
            break

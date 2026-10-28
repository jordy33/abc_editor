"""MIDI bridge tests. No browser, no third-party packages.

    python3 tests/test_midi.py
    python3 -m pytest tests/test_midi.py -q      (if pytest is installed)

The tests deliberately use a score with a 3/4 meter, duplicate section names,
empty voices, and an empty section, so the exporter cannot special-case the
example song.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.abc_score import AbcError
from app.midi import (
    PPQ, _chunk, _key_meta, _meter_meta, _smf, _tempo_meta, _text_meta, _vlq,
    build_section_midi, export_sections, import_sections, parse_midi,
)
from app.score import DEFAULT_SCORE, inspect_score


GENERIC = """X:1
T:
M:3/4
L:1/16
Q:1/4=90
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:Dm
% intro
V: Vocal
Z4|
V: Ins
d4e4f4|g4a4b4|c'4b4a4|g4f4e4|
% chorus
V: Vocal
d4e4f4|g4a4b4|
V: Ins
Z2|
% chorus
V: Vocal
a4g4f4|e4d4c4|
V: Ins
Z2|
% outro
V: Vocal
Z2|
V: Ins
Z2|
"""

CROSSING = """X:1
T:
M:4/4
L:1/16
Q:1/4=100
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
% one
V: Vocal
C16-|
V: Ins
Z|
% two
V: Vocal
C16|
V: Ins
Z|
"""


def exported(text):
    result = export_sections(text)
    return result, [{"name": item["name"], "data": item["bytes"]} for item in result["files"]]


def expected_error(text, files, needle):
    try:
        import_sections(text, files)
    except AbcError as error:
        assert needle in str(error), f"{needle!r} not in {error!s}"
        return str(error)
    raise AssertionError(f"expected an error containing {needle!r}")


def note_track(name, notes):
    """A plain track, used to exercise filename/marker fallbacks."""
    events = [(0, 0, _text_meta(0x03, name))]
    for start, end, pitch, channel in notes:
        events.append((start, 11, bytes([0x90 | channel, pitch, 96])))
        events.append((end, 10, bytes([0x80 | channel, pitch, 0])))
    return events


def test_export_names_and_files_are_general():
    result, files = exported(GENERIC)
    assert [item["name"] for item in result["files"]] == [
        "01_intro.mid", "02_chorus.mid", "03_chorus.mid", "04_outro.mid"]
    assert [item["notes"] for item in result["files"]] == [
        {"Vocal": 0, "Ins": 12},
        {"Vocal": 6, "Ins": 0},
        {"Vocal": 6, "Ins": 0},
        {"Vocal": 0, "Ins": 0},
    ]
    assert files[1]["data"][:4] == b"MThd"

    midi = parse_midi(files[1]["data"], files[1]["name"])
    assert midi.format == 1 and midi.ppq == PPQ
    assert len(midi.tracks) == 3
    assert midi.meter == (3, 4)
    assert midi.tempo == 90
    assert midi.key_signature == (-1, 1)  # Dm: one flat, minor
    assert midi.tracks[1].notes[0].start == 0  # section-relative origin
    assert midi.tracks[1].notes[0].pitch == 74
    assert "YUE2-SECTION 02 chorus bars=2 voices=Vocal,Ins" in midi.tracks[0].markers
    assert "YUE2-VOICE Vocal" in midi.tracks[1].texts
    assert "YUE2-VOICE Ins" in midi.tracks[2].texts


def test_round_trip_preserves_generic_score():
    _, files = exported(GENERIC)
    imported = import_sections(GENERIC, files)
    original = inspect_score(GENERIC)
    assert imported["roll"]["tracks"] == original["roll"]["tracks"]
    assert imported["abc"] == GENERIC
    assert imported["import"]["warnings"] == []
    assert len(imported["import"]["applied"]) == 4
    assert imported["import"]["unchanged"] == []


def test_round_trip_preserves_a_note_crossing_a_section():
    _, files = exported(CROSSING)
    imported = import_sections(CROSSING, files)
    original = inspect_score(CROSSING)
    assert imported["roll"]["tracks"] == original["roll"]["tracks"]
    assert imported["abc"] == CROSSING


def test_round_trip_the_example_song_if_present():
    path = Path(__file__).resolve().parent.parent / "al_otro_lado_del_mar.abc"
    if not path.exists():
        return
    text = path.read_text()
    _, files = exported(text)
    imported = import_sections(text, files)
    original = inspect_score(text)
    assert imported["roll"]["tracks"] == original["roll"]["tracks"]
    assert imported["abc"] == original["abc"]
    assert imported["import"]["warnings"] == []


def test_partial_import_changes_only_the_named_section():
    replacement = build_section_midi(
        2, "chorus", 2, 90, "3/4", "Dm", 768,
        [{"start": 0, "duration": 768, "pitch": 60}], [], width=2,
    )
    imported = import_sections(GENERIC, [{"name": "02_chorus.mid", "data": replacement}])
    start, end = 4 * 768, 6 * 768
    vocal = imported["roll"]["tracks"]["Vocal"]
    assert [note for note in vocal if start <= note["start"] < end] == [
        {"start": start, "duration": 768, "pitch": 60}]
    # The other chorus (section 3) and the intro/outro are untouched.
    assert len([note for note in vocal if 6 * 768 <= note["start"] < 8 * 768]) == 6
    assert [item["section"] for item in imported["import"]["unchanged"]] == [1, 3, 4]


def test_renamed_file_is_recovered_from_its_marker():
    _, files = exported(GENERIC)
    imported = import_sections(GENERIC, [{"name": "Untitled.mid", "data": files[0]["data"]}])
    assert imported["roll"]["tracks"] == inspect_score(GENERIC)["roll"]["tracks"]
    assert imported["import"]["applied"][0]["section"] == 1


def test_copy_suffix_in_filename_is_allowed_when_the_marker_matches():
    _, files = exported(GENERIC)
    imported = import_sections(GENERIC, [{"name": "02_chorus copy.mid", "data": files[1]["data"]}])
    assert imported["import"]["applied"][0]["section"] == 2
    assert imported["roll"]["tracks"] == inspect_score(GENERIC)["roll"]["tracks"]


def test_unmatched_file_is_rejected_with_expected_names():
    data = _smf([note_track("Piano", [(0, 3840, 60, 0)])])
    error = expected_error(GENERIC, [{"name": "Untitled.mid", "data": data}], "Cannot match")
    assert "01_intro.mid" in error and "04_outro.mid" in error


def test_filename_number_maps_without_embedded_metadata():
    data = _smf([note_track("Piano", [(0, 3840, 60, 0)])])
    imported = import_sections(DEFAULT_SCORE, [{"name": "02_outro.mid", "data": data}])
    start = 4 * 1024  # 4/4: one bar is 1024 roll ticks; the verse is 4 bars.
    assert [note for note in imported["roll"]["tracks"]["Vocal"] if note["start"] == start] == [
        {"start": start, "duration": 256, "pitch": 60}]
    assert imported["roll"]["tracks"]["Ins"] == inspect_score(DEFAULT_SCORE)["roll"]["tracks"]["Ins"]
    assert imported["import"]["applied"][0]["section"] == 2


def test_filename_without_a_slug_maps_by_number():
    data = _smf([note_track("Piano", [(0, 3840, 60, 0)])])
    imported = import_sections(DEFAULT_SCORE, [{"name": "02.mid", "data": data}])
    assert imported["import"]["applied"][0]["section"] == 2


def test_track_name_without_a_slug_still_uses_its_code():
    data = _smf([[
        (0, 0, _text_meta(0x03, "YUE2 02 - Vocal")),
        (0, 1, _text_meta(0x06, "YUE2-SECTION 02 chorus bars=2 voices=Vocal,Ins")),
        (0, 2, _meter_meta("3/4")),
        (0, 11, bytes([0x90, 60, 96])),
        (3840, 10, bytes([0x80, 60, 0])),
    ]])
    imported = import_sections(GENERIC, [{"name": "02_chorus.mid", "data": data}])
    assert imported["import"]["applied"][0]["section"] == 2
    start = 4 * 768
    assert [note for note in imported["roll"]["tracks"]["Vocal"] if note["start"] == start] == [
        {"start": start, "duration": 256, "pitch": 60}]


def test_unique_bare_name_maps_and_ambiguous_name_is_rejected():
    data = _smf([note_track("Piano", [(0, 3840, 60, 0)])])
    imported = import_sections(GENERIC, [{"name": "intro.mid", "data": data}])
    assert imported["import"]["applied"][0]["section"] == 1
    expected_error(GENERIC, [{"name": "chorus.mid", "data": data}], "matches more than one section")


def test_filename_slug_must_match_the_current_section():
    data = _smf([note_track("Piano", [(0, 3840, 60, 0)])])
    expected_error(DEFAULT_SCORE, [{"name": "02_verse.mid", "data": data}],
                   "the name suggests section 2 'verse'")


def test_conflicting_filename_and_marker_are_rejected():
    _, files = exported(GENERIC)
    expected_error(GENERIC, [{"name": "03_chorus.mid", "data": files[1]["data"]}],
                   "named for section 3")


def test_conflicting_marker_names_are_rejected():
    data = _smf([[
        (0, 0, _text_meta(0x03, "YUE2 02 chorus")),
        (0, 1, _text_meta(0x06, "YUE2-SECTION 02 chorus bars=2 voices=Vocal,Ins")),
        (0, 2, _text_meta(0x06, "YUE2-SECTION 02 verse bars=2 voices=Vocal,Ins")),
        (0, 3, _meter_meta("3/4")),
    ]])
    expected_error(GENERIC, [{"name": "02_chorus.mid", "data": data}],
                   "disagree about the name")


def test_overlapping_notes_are_rejected_with_section_and_bar():
    data = build_section_midi(
        2, "chorus", 2, 90, "3/4", "Dm", 768,
        [{"start": 0, "duration": 512, "pitch": 60},
         {"start": 256, "duration": 512, "pitch": 64}], [], width=2,
    )
    error = expected_error(GENERIC, [{"name": "02_chorus.mid", "data": data}], "overlap")
    assert "section 2 'chorus', bar 1" in error
    assert "60 and 64" in error


def test_note_outside_the_section_is_rejected():
    data = build_section_midi(
        2, "chorus", 2, 90, "3/4", "Dm", 768,
        [{"start": 1536, "duration": 256, "pitch": 60}], [], width=2,
    )
    expected_error(GENERIC, [{"name": "02_chorus.mid", "data": data}], "only 2 bar(s)")


def test_note_past_the_song_is_rejected():
    data = build_section_midi(
        4, "outro", 2, 90, "3/4", "Dm", 768,
        [{"start": 0, "duration": 100000, "pitch": 60}], [], width=2,
    )
    expected_error(GENERIC, [{"name": "04_outro.mid", "data": data}], "past the end of the song")


def test_empty_section_file_clears_notes():
    replacement = build_section_midi(2, "chorus", 2, 90, "3/4", "Dm", 768, [], [], width=2)
    imported = import_sections(GENERIC, [{"name": "02_chorus.mid", "data": replacement}])
    start, end = 4 * 768, 6 * 768
    assert [note for note in imported["roll"]["tracks"]["Vocal"]
            if start <= note["start"] < end] == []
    assert len([note for note in imported["roll"]["tracks"]["Vocal"]
                if 6 * 768 <= note["start"] < 8 * 768]) == 6


def test_missing_voice_list_leaves_the_other_voice_alone():
    replacement = build_section_midi(
        1, "intro", 4, 90, "3/4", "Dm", 768,
        [{"start": 0, "duration": 256, "pitch": 60}], [],
        width=2, declared_voices=None,
    )
    imported = import_sections(GENERIC, [{"name": "01_intro.mid", "data": replacement}])
    original = inspect_score(GENERIC)
    assert imported["roll"]["tracks"]["Ins"] == original["roll"]["tracks"]["Ins"]
    assert [note for note in imported["roll"]["tracks"]["Vocal"]
            if 0 <= note["start"] < 4 * 768] == [
        {"start": 0, "duration": 256, "pitch": 60}]


def test_one_track_on_two_channels_is_split_by_voice():
    data = _smf([[
        (0, 0, _text_meta(0x03, "Combined")),
        (0, 1, _text_meta(0x06, "YUE2-SECTION 02 chorus bars=2 voices=Vocal,Ins")),
        (0, 2, _meter_meta("3/4")),
        (0, 3, _tempo_meta(90)),
        (0, 4, _key_meta("Dm")),
        (0, 11, bytes([0x90, 60, 96])),
        (0, 11, bytes([0x91, 48, 96])),
        (3840 * 3, 10, bytes([0x80, 60, 0])),
        (3840 * 3, 10, bytes([0x81, 48, 0])),
    ]])
    imported = import_sections(GENERIC, [{"name": "02_chorus.mid", "data": data}])
    start = 4 * 768
    assert [note for note in imported["roll"]["tracks"]["Vocal"] if note["start"] == start] == [
        {"start": start, "duration": 768, "pitch": 60}]
    assert [note for note in imported["roll"]["tracks"]["Ins"] if note["start"] == start] == [
        {"start": start, "duration": 768, "pitch": 48}]


def test_meter_mismatch_is_rejected():
    data = build_section_midi(
        2, "chorus", 2, 90, "4/4", "Dm", 768,
        [{"start": 0, "duration": 768, "pitch": 60}], [], width=2,
    )
    expected_error(GENERIC, [{"name": "02_chorus.mid", "data": data}],
                   "is 4/4, but the score is 3/4")


def test_tempo_and_key_mismatches_warn_but_still_import():
    data = build_section_midi(
        2, "chorus", 2, 120, "3/4", "C", 768,
        [{"start": 0, "duration": 768, "pitch": 60}], [], width=2,
    )
    imported = import_sections(GENERIC, [{"name": "02_chorus.mid", "data": data}])
    warnings = " ".join(imported["import"]["warnings"])
    assert "120 BPM" in warnings and "90 BPM" in warnings
    assert "key signature" in warnings
    start = 4 * 768
    assert [note for note in imported["roll"]["tracks"]["Vocal"] if note["start"] == start] == [
        {"start": start, "duration": 768, "pitch": 60}]


def test_duplicate_section_files_are_rejected():
    _, files = exported(GENERIC)
    expected_error(GENERIC, [files[1], {"name": "copy.mid", "data": files[1]["data"]}],
                   "both target section 2")


def test_invalid_and_empty_imports_are_rejected():
    expected_error(DEFAULT_SCORE, [], "at least one")
    expected_error(DEFAULT_SCORE, [{"name": "bad.mid", "data": b"not a midi file"}],
                   "not a Standard MIDI File")
    expected_error(DEFAULT_SCORE, [{"name": "bad.mid", "data": "not bytes"}],
                   "name and binary data")


def test_export_rejects_a_score_with_inline_key_changes():
    broken = DEFAULT_SCORE.replace('"Am7"z16', '[K:C]"Am7"z16', 1)
    broken = broken.replace("E2A2c4B2A2G4", "[K:C]E2A2c4B2A2G4", 1)
    try:
        export_sections(broken)
    except AbcError as error:
        assert "does not support" in str(error)
    else:
        raise AssertionError("expected export to reject key changes")


def test_reader_handles_running_status_and_velocity_zero():
    payload = (
        _vlq(0) + bytes([0x90, 60, 96]) +
        _vlq(3840) + bytes([60, 0]) +   # running status: note-on with velocity 0
        _vlq(0) + bytes([64, 96]) +     # running status: next note-on
        _vlq(3840) + bytes([64, 0])
    )
    data = b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ) + _chunk(b"MTrk", payload)
    midi = parse_midi(data, "running.mid")
    notes = sorted(midi.tracks[0].notes, key=lambda note: note.start)
    assert [(note.start, note.end, note.pitch) for note in notes] == [
        (0, 3840, 60), (3840, 7680, 64)]


def test_running_status_survives_a_meta_event():
    payload = (
        _vlq(0) + bytes([0x90, 60, 96]) +
        _vlq(0) + _text_meta(0x01, "between") +
        _vlq(0) + bytes([62, 96]) +      # running-status note-on
        _vlq(3840) + bytes([60, 0]) +    # running-status note-off
        _vlq(0) + bytes([62, 0])
    )
    data = b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ) + _chunk(b"MTrk", payload)
    midi = parse_midi(data, "running-meta.mid")
    notes = sorted(midi.tracks[0].notes, key=lambda note: (note.start, note.pitch))
    assert [(note.start, note.end, note.pitch) for note in notes] == [
        (0, 3840, 60), (0, 3840, 62)]


def test_invalid_voice_channel_is_rejected():
    data = _smf([[
        (0, 0, _text_meta(0x03, "YUE2 02 chorus - Strings")),
        (0, 1, _text_meta(0x06, "YUE2-SECTION 02 chorus bars=2 voices=Vocal,Ins")),
        (0, 2, _meter_meta("3/4")),
        (0, 11, bytes([0x95, 60, 96])),
        (3840, 10, bytes([0x85, 60, 0])),
    ]])
    expected_error(GENERIC, [{"name": "02_chorus.mid", "data": data}], "channel 6")


def test_drum_channel_is_rejected():
    data = _smf([[
        (0, 0, _text_meta(0x03, "YUE2 02 chorus - Vocal")),
        (0, 1, _text_meta(0x06, "YUE2-SECTION 02 chorus bars=2 voices=Vocal,Ins")),
        (0, 2, _meter_meta("3/4")),
        (0, 11, bytes([0x99, 36, 96])),
        (3840, 10, bytes([0x89, 36, 0])),
    ]])
    expected_error(GENERIC, [{"name": "02_chorus.mid", "data": data}], "drums on channel 10")


def _run():
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {name}: {type(error).__name__}: {error}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())

"""MusicXML bridge tests. No browser, no third-party packages.

    python3 tests/test_musicxml.py
    python3 -m pytest tests/test_musicxml.py -q      (if pytest is installed)

The fixtures deliberately use a 3/4 meter, duplicate section names, empty
voices, an empty section, a note that crosses a section boundary, and a chord
in the middle of a held note, so the bridge cannot special-case the example
song.
"""

import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.musicxml as musicxml
from app.abc_score import AbcError
from app.musicxml import export_song, import_song
from app.score import DEFAULT_SCORE, build_score, inspect_score


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

MID_CHORD = """X:1
T:
M:4/4
L:1/16
Q:1/4=100
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
% one
V: Vocal
C8-"G"C8|
V: Ins
Z|
"""

NONCANONICAL = """X:1
T:
M:4/4
L:1/32
Q:1/4=100
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
% one
V: Vocal
C32|
V: Ins
Z|
"""

LOW_PITCH = """X:1
T:
M:4/4
L:1/16
Q:1/4=100
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
% one
V: Vocal
C,,,,,16|
V: Ins
Z|
"""

SIX_EIGHT = """X:1
T:
M:6/8
L:1/16
Q:1/4=120
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:F
% one
V: Vocal
C12|D12|
V: Ins
Z2|
"""

CHORDS = """X:1
T:
M:4/4
L:1/16
Q:1/4=100
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
% one
V: Vocal
"Cm7b5"C16|"Cdim7"C16|"Cm(maj7)"C16|"C7sus4"C16|
V: Ins
Z4|
V: Vocal
"Csus2"C16|"Caug"C16|
V: Ins
Z2|
"""


# --- helpers ---------------------------------------------------------------


def local(element):
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


def elements(root, name):
    return [element for element in root.iter() if local(element) == name]


def child(element, name):
    return next((item for item in element if local(item) == name), None)


def parts(root):
    return [element for element in root if local(element) == "part"]


def measures(part):
    return [element for element in part if local(element) == "measure"]


def first_measure(part):
    return measures(part)[0]


def first_note(measure):
    return next(element for element in measure if local(element) == "note")


def first_pitch(part):
    for note in part.iter():
        if local(note) != "note":
            continue
        pitch = child(note, "pitch")
        if pitch is not None:
            return pitch
    raise AssertionError("part has no pitched note")


def exported(text, name="test"):
    result = export_song(text, name)
    assert len(result["files"]) == 1
    return result, result["files"][0]


def parsed(data):
    return ET.fromstring(data)


def mutate(data, change):
    root = parsed(data)
    change(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def expected_error(text, data, needle, name="edited.musicxml"):
    try:
        import_song(text, data, name)
    except AbcError as error:
        assert needle in str(error), f"{needle!r} not in {error!s}"
        return str(error)
    raise AssertionError(f"expected an error containing {needle!r}")


def export_error(text, needle):
    try:
        export_song(text)
    except AbcError as error:
        assert needle in str(error), f"{needle!r} not in {error!s}"
        return str(error)
    raise AssertionError(f"expected an export error containing {needle!r}")


def text_of(element):
    return (element.text or "").strip() if element is not None else ""


def strip_tie(note, kind):
    for item in list(note):
        if local(item) == "tie" and item.get("type") == kind:
            note.remove(item)
        elif local(item) == "notations":
            for tied in list(item):
                if local(tied) == "tied" and tied.get("type") == kind:
                    item.remove(tied)


def replace_with_rest(note):
    duration = int(text_of(child(note, "duration")))
    for item in list(note):
        note.remove(item)
    ET.SubElement(note, "rest")
    ET.SubElement(note, "duration").text = str(duration)
    ET.SubElement(note, "voice").text = "1"
    ET.SubElement(note, "type").text = "whole" if duration == 1024 else "quarter"


def rescale(root, divisions):
    factor = Fraction(divisions, 256)
    for element in elements(root, "divisions"):
        element.text = str(divisions)
    for element in elements(root, "duration"):
        value = factor * int(text_of(element))
        assert value.denominator == 1, f"test fixture duration {element.text} does not rescale"
        element.text = str(value.numerator)


def namespace_tags(root, uri="http://www.musicxml.org/ns/musicxml"):
    for element in root.iter():
        if isinstance(element.tag, str):
            element.tag = "{%s}%s" % (uri, element.tag)


def clear_part(root, index, bar_ticks):
    part = parts(root)[index]
    for measure in measures(part):
        for item in list(measure):
            if local(item) == "note":
                measure.remove(item)
        note = ET.SubElement(measure, "note")
        ET.SubElement(note, "rest", {"measure": "yes"})
        ET.SubElement(note, "duration").text = str(bar_ticks)
        ET.SubElement(note, "voice").text = "1"


def check_roundtrip(text):
    info = inspect_score(text)
    result, file = exported(text)
    imported = import_song(text, file["data"], file["name"])
    assert imported["import"]["unchanged"] is True, imported["import"]
    assert imported["abc"] == info["abc"]
    assert imported["roll"]["tracks"] == info["roll"]["tracks"]
    assert imported["roll"]["chords"] == info["roll"]["chords"]
    assert imported["import"]["warnings"] == []
    return info, result, file, imported


# --- export -----------------------------------------------------------------


def test_export_has_two_parts_and_every_measure():
    info, result, file, _ = check_roundtrip(GENERIC)
    root = parsed(file["data"])
    assert local(root) == "score-partwise"
    assert root.get("version") == "3.1"
    assert [text_of(child(score_part, "part-name")) for score_part in elements(root, "score-part")] == [
        "Vocal", "Ins"]
    assert [len(measures(part)) for part in parts(root)] == [info["bars"], info["bars"]] == [10, 10]
    assert file["bars"] == 10
    assert file["parts"] == ["Vocal", "Ins"]
    assert file["notes"] == {voice: len(info["roll"]["tracks"][voice]) for voice in ("Vocal", "Ins")}
    assert result["summary"]["divisions"] == 256
    for part in parts(root):
        attributes = child(first_measure(part), "attributes")
        assert text_of(child(attributes, "divisions")) == "256"
        key = child(attributes, "key")
        assert text_of(child(key, "fifths")) == "-1"
        assert text_of(child(key, "mode")) == "minor"
        time = child(attributes, "time")
        assert (text_of(child(time, "beats")), text_of(child(time, "beat-type"))) == ("3", "4")


def test_export_writes_explicit_rests_and_sections():
    info, _, file, _ = check_roundtrip(DEFAULT_SCORE)
    root = parsed(file["data"])
    vocal, ins = parts(root)
    measure_rests = [element for element in elements(vocal, "rest") if element.get("measure") == "yes"]
    assert len(measure_rests) == 8
    assert len([element for element in elements(ins, "rest") if element.get("measure") == "yes"]) == 0
    assert [text_of(element) for element in elements(root, "rehearsal")] == ["verse", "outro"]
    assert len(elements(root, "harmony")) == len(info["roll"]["chords"]) == 8


def test_export_splits_notes_across_barlines_with_ties():
    info = inspect_score(CROSSING)
    assert info["roll"]["tracks"]["Vocal"] == [{"start": 0, "duration": 2048, "pitch": 60}]
    _, _, file, imported = check_roundtrip(CROSSING)
    vocal = parts(parsed(file["data"]))[0]
    vocal_notes = [note for note in vocal.iter() if local(note) == "note"]
    assert len(vocal_notes) == 2
    assert child(vocal_notes[0], "tie").get("type") == "start"
    assert child(vocal_notes[1], "tie").get("type") == "stop"
    assert imported["roll"]["tracks"]["Vocal"] == [{"start": 0, "duration": 2048, "pitch": 60}]


def test_export_splits_held_notes_at_chord_changes():
    info = inspect_score(MID_CHORD)
    assert info["roll"]["tracks"]["Vocal"] == [{"start": 0, "duration": 1024, "pitch": 60}]
    assert info["roll"]["chords"] == [{"start": 512, "symbol": "G"}]
    _, _, file, imported = check_roundtrip(MID_CHORD)
    vocal = parts(parsed(file["data"]))[0]
    notes = [note for note in vocal.iter() if local(note) == "note"]
    assert len(notes) == 2
    assert child(notes[0], "tie").get("type") == "start"
    assert child(notes[1], "tie").get("type") == "stop"
    assert imported["roll"]["tracks"]["Vocal"] == [{"start": 0, "duration": 1024, "pitch": 60}]


def test_export_uses_standard_note_values_and_dots():
    text = DEFAULT_SCORE
    _, _, file, _ = check_roundtrip(text)
    types = {text_of(element) for element in elements(parsed(file["data"]), "type")}
    assert types <= {"whole", "half", "quarter", "eighth", "16th", "32nd", "64th",
                     "128th", "256th", "512th", "1024th"}
    assert {"eighth", "quarter", "half"} <= types


def test_export_writes_harmony_kinds_and_bass():
    _, _, file, _ = check_roundtrip(CHORDS)
    root = parsed(file["data"])
    kinds = elements(root, "kind")
    assert [text_of(element) for element in kinds] == [
        "half-diminished", "diminished-seventh", "major-minor",
        "suspended-fourth", "suspended-second", "augmented"]
    assert [element.get("text") for element in kinds] == [
        "m7b5", "dim7", "m(maj7)", "7sus4", "sus2", "aug"]

    bass_score = CHORDS.replace('"Cm7b5"C16', '"C/Eb"C16')
    _, _, bass_file, _ = check_roundtrip(bass_score)
    harmony = elements(parsed(bass_file["data"]), "harmony")[0]
    bass = child(harmony, "bass")
    assert text_of(child(bass, "bass-step")) == "E"
    assert text_of(child(bass, "bass-alter")) == "-1"


def test_export_escapes_section_names():
    name = "rock & roll <x>"
    text = DEFAULT_SCORE.replace("% verse", "% " + name, 1)
    info, _, file, imported = check_roundtrip(text)
    assert [section["name"] for section in info["sections"]][0] == name
    assert [text_of(element) for element in elements(parsed(file["data"]), "rehearsal")][0] == name
    assert imported["import"]["warnings"] == []


def test_export_name_is_sanitised():
    assert export_song(DEFAULT_SCORE, "100añños.abc")["files"][0]["name"] == "100añños.musicxml"
    assert export_song(DEFAULT_SCORE)["files"][0]["name"] == "yue2-score.musicxml"
    assert export_song(DEFAULT_SCORE, "../x/y.musicxml")["files"][0]["name"].endswith(".musicxml")


# --- round trips ------------------------------------------------------------


def test_roundtrip_is_exact_for_varied_scores():
    for text in (DEFAULT_SCORE, GENERIC, CROSSING, MID_CHORD, SIX_EIGHT, CHORDS):
        check_roundtrip(text)


def test_noncanonical_abc_is_returned_byte_for_byte():
    info = inspect_score(NONCANONICAL)
    assert build_score(info)["abc"] != info["abc"]
    _, _, file, imported = check_roundtrip(NONCANONICAL)
    assert imported["abc"] == info["abc"]


def test_idempotent_export_import_export():
    _, file = exported(GENERIC)
    first = import_song(GENERIC, file["data"], file["name"])
    second_result, second_file = exported(first["abc"])
    second = import_song(first["abc"], second_file["data"], second_file["name"])
    assert second["import"]["unchanged"] is True
    assert second["abc"] == first["abc"]
    assert second["roll"]["tracks"] == first["roll"]["tracks"]
    assert second_result["files"][0]["bars"] == 10


def test_changed_notes_replace_both_voices_and_canonicalise():
    info = inspect_score(DEFAULT_SCORE)

    def change(root):
        pitch = first_pitch(parts(root)[1])
        child(pitch, "step").text = "F"

    data = mutate(exported(DEFAULT_SCORE)[1]["data"], change)
    imported = import_song(DEFAULT_SCORE, data, "edited.musicxml")
    expected_notes = [dict(note) for note in info["roll"]["tracks"]["Ins"]]
    expected_notes[0]["pitch"] = 65
    expected = build_score({**info, "roll": {**info["roll"], "tracks": {
        "Vocal": info["roll"]["tracks"]["Vocal"], "Ins": expected_notes}}})
    assert imported["import"]["unchanged"] is False
    assert imported["roll"]["tracks"]["Ins"][0]["pitch"] == 65
    assert imported["abc"] == expected["abc"]
    assert imported["roll"]["chords"] == info["roll"]["chords"]


def test_import_clears_voice_with_rests():
    info = inspect_score(DEFAULT_SCORE)
    data = mutate(exported(DEFAULT_SCORE)[1]["data"], lambda root: clear_part(root, 1, 1024))
    imported = import_song(DEFAULT_SCORE, data, "edited.musicxml")
    assert imported["import"]["unchanged"] is False
    assert imported["roll"]["tracks"]["Ins"] == []
    assert imported["roll"]["tracks"]["Vocal"] == info["roll"]["tracks"]["Vocal"]


def test_import_accepts_forward_as_silence():
    _, _, file, _ = check_roundtrip(GENERIC)

    def replace(root):
        measure = first_measure(parts(root)[0])
        note = first_note(measure)
        duration = text_of(child(note, "duration"))
        measure.remove(note)
        forward = ET.SubElement(measure, "forward")
        ET.SubElement(forward, "duration").text = duration

    data = mutate(file["data"], replace)
    imported = import_song(GENERIC, data, "edited.musicxml")
    assert imported["import"]["unchanged"] is True


def test_import_accepts_other_divisions_exactly():
    _, _, file, _ = check_roundtrip(DEFAULT_SCORE)
    data = mutate(file["data"], lambda root: rescale(root, 512))
    imported = import_song(DEFAULT_SCORE, data, "edited.musicxml")
    assert imported["import"]["unchanged"] is True

    _, _, cross_file, _ = check_roundtrip(CROSSING)
    data = mutate(cross_file["data"], lambda root: rescale(root, 480))
    imported = import_song(CROSSING, data, "edited.musicxml")
    assert imported["import"]["unchanged"] is True
    assert imported["roll"]["tracks"]["Vocal"] == [{"start": 0, "duration": 2048, "pitch": 60}]


def test_import_accepts_namespaces_and_version_4():
    _, _, file, _ = check_roundtrip(DEFAULT_SCORE)

    def change(root):
        root.set("version", "4.0")
        namespace_tags(root)

    data = mutate(file["data"], change)
    imported = import_song(DEFAULT_SCORE, data, "logic.musicxml")
    assert imported["import"]["unchanged"] is True
    assert imported["import"]["source"] == "part names"


def test_import_recognises_parts_by_channel_and_by_order():
    _, _, file, _ = check_roundtrip(DEFAULT_SCORE)

    def rename(root):
        for index, score_part in enumerate(elements(root, "score-part"), 1):
            child(score_part, "part-name").text = f"Part {index}"
            child(score_part, "part-abbreviation").text = ""
            child(child(score_part, "score-instrument"), "instrument-name").text = f"Part {index}"

    data = mutate(file["data"], rename)
    imported = import_song(DEFAULT_SCORE, data, "renamed.musicxml")
    assert imported["import"]["unchanged"] is True
    assert imported["import"]["source"] == "MIDI channels"
    assert imported["import"]["warnings"] == []

    def strip_channels(root):
        rename(root)
        for score_part in elements(root, "score-part"):
            for item in list(score_part):
                if local(item) == "midi-instrument":
                    score_part.remove(item)

    data = mutate(file["data"], strip_channels)
    imported = import_song(DEFAULT_SCORE, data, "renamed.musicxml")
    assert imported["import"]["unchanged"] is True
    assert imported["import"]["source"] == "part order"
    assert any("part names were not recognized" in warning for warning in imported["import"]["warnings"])


# --- warnings ---------------------------------------------------------------


def test_import_warns_about_tempo_key_and_markers():
    _, _, file, _ = check_roundtrip(DEFAULT_SCORE)

    def change(root):
        for sound in elements(root, "sound"):
            sound.set("tempo", "99")
        for fifths in elements(root, "fifths"):
            fifths.text = "0"
        for mode in elements(root, "mode"):
            mode.text = "major"
        elements(root, "rehearsal")[0].text = "intro"

    data = mutate(file["data"], change)
    imported = import_song(DEFAULT_SCORE, data, "edited.musicxml")
    warnings = imported["import"]["warnings"]
    assert imported["import"]["unchanged"] is True
    assert any("99 BPM" in warning for warning in warnings)
    assert any("different key signature" in warning for warning in warnings)
    assert any("rehearsal marks" in warning for warning in warnings)


# --- rejections -------------------------------------------------------------


def test_import_rejects_measure_count_changes():
    _, _, file, _ = check_roundtrip(GENERIC)

    def remove(root):
        part = parts(root)[0]
        part.remove(measures(part)[0])

    data = mutate(file["data"], remove)
    expected_error(GENERIC, data, "has 9 measure(s)")

    def duplicate(root):
        part = parts(root)[1]
        part.append(ET.fromstring(ET.tostring(measures(part)[0])))

    data = mutate(file["data"], duplicate)
    expected_error(GENERIC, data, "has 11 measure(s)")


def test_import_rejects_meter_changes_and_pickups():
    _, _, file, _ = check_roundtrip(GENERIC)

    def meter(root):
        child(elements(root, "time")[0], "beats").text = "4"

    expected_error(GENERIC, mutate(file["data"], meter), "is in 4/4, but the score is 3/4")

    def implicit(root):
        first_measure(parts(root)[0]).set("implicit", "yes")

    expected_error(GENERIC, mutate(file["data"], implicit), "pickup")

    def short(root):
        note = first_note(first_measure(parts(root)[0]))
        for item in list(note):
            note.remove(item)
        ET.SubElement(note, "rest")
        ET.SubElement(note, "duration").text = "256"
        ET.SubElement(note, "voice").text = "1"
        ET.SubElement(note, "type").text = "quarter"

    expected_error(GENERIC, mutate(file["data"], short), "pickups are not supported")

    def overfull(root):
        child(first_note(first_measure(parts(root)[0])), "duration").text = "1024"

    expected_error(GENERIC, mutate(file["data"], overfull), "crosses the barline")


def test_import_rejects_polyphony_and_unsupported_notation():
    _, _, file, _ = check_roundtrip(DEFAULT_SCORE)

    def backup(root):
        measure = first_measure(parts(root)[0])
        backup_element = ET.SubElement(measure, "backup")
        ET.SubElement(backup_element, "duration").text = "256"

    expected_error(DEFAULT_SCORE, mutate(file["data"], backup), "<backup>")

    def chord(root):
        first_note(first_measure(parts(root)[1])).insert(0, ET.Element("chord"))

    expected_error(DEFAULT_SCORE, mutate(file["data"], chord), "<chord>")

    def voice(root):
        child(first_note(first_measure(parts(root)[1])), "voice").text = "2"

    expected_error(DEFAULT_SCORE, mutate(file["data"], voice), "voice 2")

    def grace(root):
        first_note(first_measure(parts(root)[1])).insert(0, ET.Element("grace"))

    expected_error(DEFAULT_SCORE, mutate(file["data"], grace), "grace")

    def tuplet(root):
        note = first_note(first_measure(parts(root)[1]))
        modification = ET.SubElement(note, "time-modification")
        ET.SubElement(modification, "actual-notes").text = "3"
        ET.SubElement(modification, "normal-notes").text = "2"

    expected_error(DEFAULT_SCORE, mutate(file["data"], tuplet), "tuplets")

    def transpose(root):
        attributes = child(first_measure(parts(root)[0]), "attributes")
        ET.SubElement(attributes, "transpose")

    expected_error(DEFAULT_SCORE, mutate(file["data"], transpose), "transposing")

    def unpitched(root):
        pitch = first_pitch(parts(root)[1])
        pitch.tag = "unpitched"
        for item in list(pitch):
            pitch.remove(item)

    expected_error(DEFAULT_SCORE, mutate(file["data"], unpitched), "unpitched")


def test_import_rejects_pitch_range_and_microtones():
    _, _, file, _ = check_roundtrip(DEFAULT_SCORE)

    def octave(root):
        child(first_pitch(parts(root)[1]), "octave").text = "10"

    expected_error(DEFAULT_SCORE, mutate(file["data"], octave), "octave 10")

    def below(root):
        pitch = first_pitch(parts(root)[1])
        child(pitch, "step").text = "C"
        child(pitch, "octave").text = "0"
        alter = child(pitch, "alter")
        if alter is None:
            alter = ET.SubElement(pitch, "alter")
        alter.text = "-2"

    expected_error(DEFAULT_SCORE, mutate(file["data"], below), "outside the supported range")

    def microtone(root):
        pitch = first_pitch(parts(root)[1])
        alter = child(pitch, "alter")
        if alter is None:
            alter = ET.SubElement(pitch, "alter")
        alter.text = "0.5"

    expected_error(DEFAULT_SCORE, mutate(file["data"], microtone), "microtonal")


def test_import_rejects_broken_ties():
    _, _, file, _ = check_roundtrip(CROSSING)
    root = parsed(file["data"])
    tied = [note for note in parts(root)[0].iter()
            if local(note) == "note" and any(local(item) == "tie" for item in note)]
    assert len(tied) == 2
    strip_tie(tied[1], "stop")
    expected_error(CROSSING, ET.tostring(root, encoding="utf-8"), "not continued")

    def stop_only(root):
        for candidate in parts(root)[1].iter():
            if local(candidate) == "note" and any(local(item) == "pitch" for item in candidate):
                ET.SubElement(candidate, "tie", {"type": "stop"})
                return

    expected_error(DEFAULT_SCORE, mutate(exported(DEFAULT_SCORE)[1]["data"], stop_only),
                   "tie ends without starting")

    root = parsed(file["data"])
    tied = [note for note in parts(root)[0].iter()
            if local(note) == "note" and any(local(item) == "tie" for item in note)]
    child(child(tied[1], "pitch"), "step").text = "D"
    expected_error(CROSSING, ET.tostring(root, encoding="utf-8"), "changes pitch")

    root = parsed(file["data"])
    tied = [note for note in parts(root)[0].iter()
            if local(note) == "note" and any(local(item) == "tie" for item in note)]
    replace_with_rest(tied[1])
    expected_error(CROSSING, ET.tostring(root, encoding="utf-8"), "interrupted by a rest")


def test_import_rejects_wrong_parts_and_roots():
    _, _, file, _ = check_roundtrip(DEFAULT_SCORE)
    expected_error(DEFAULT_SCORE, b"PK\x03\x04rest", "compressed")
    expected_error(DEFAULT_SCORE, b"<score-partwise>", "not valid XML")
    expected_error(DEFAULT_SCORE, b"<foo/>", "not a MusicXML score")
    expected_error(DEFAULT_SCORE, b'<score-timewise version="3.1"><part-list/></score-timewise>',
                   "score-timewise")
    expected_error(DEFAULT_SCORE,
                   b'<?xml version="1.0"?><!DOCTYPE score-partwise [<!ENTITY x "y">]><score-partwise/>',
                   "entity declaration")

    def missing(root):
        root.remove(parts(root)[1])

    expected_error(DEFAULT_SCORE, mutate(file["data"], missing), "has 1 part(s)")

    def extra(root):
        extra_part = ET.fromstring(ET.tostring(parts(root)[1]))
        extra_part.set("id", "P3")
        root.append(extra_part)

    expected_error(DEFAULT_SCORE, mutate(file["data"], extra), "has 3 part(s)")


def test_import_rejects_bad_attributes():
    _, _, file, _ = check_roundtrip(DEFAULT_SCORE)

    def mid_measure(root):
        measure = first_measure(parts(root)[0])
        attributes = ET.SubElement(measure, "attributes")
        ET.SubElement(attributes, "divisions").text = "512"

    expected_error(DEFAULT_SCORE, mutate(file["data"], mid_measure), "divisions change inside measure")

    def staves(root):
        attributes = child(first_measure(parts(root)[0]), "attributes")
        ET.SubElement(attributes, "staves").text = "2"

    expected_error(DEFAULT_SCORE, mutate(file["data"], staves), "multiple staves")

    def composite(root):
        time = elements(root, "time")[0]
        ET.SubElement(time, "beats").text = "3"

    expected_error(DEFAULT_SCORE, mutate(file["data"], composite), "composite time signature")


def test_import_rejects_oversized_files():
    _, _, file, _ = check_roundtrip(DEFAULT_SCORE)
    original = musicxml.MAX_FILE_BYTES
    try:
        musicxml.MAX_FILE_BYTES = 10
        expected_error(DEFAULT_SCORE, file["data"], "larger than")
    finally:
        musicxml.MAX_FILE_BYTES = original


def test_export_rejects_unrepresentable_scores():
    export_error(LOW_PITCH, "C0")
    export_error(DEFAULT_SCORE.replace("% verse", "% " + "x" * 101, 1), "Section names")
    changed = DEFAULT_SCORE.replace('"Am7"z16', '[K:C]"Am7"z16', 1).replace(
        "E2A2c4B2A2G4", "[K:C]E2A2c4B2A2G4", 1)
    info = inspect_score(changed)
    assert info["grid_available"] is False
    export_error(changed, "single key and meter")
    expected_error(changed, exported(DEFAULT_SCORE)[1]["data"], "single key and meter")


def _run():
    """Minimal runner so the tests work without pytest installed."""
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failures = 0
    for name, function in tests:
        try:
            function()
            print(f"  PASS  {name}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {name}: {type(error).__name__}: {error}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())

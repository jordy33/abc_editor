"""Full-song MusicXML bridge for the native YuE2 ABC dialect.

One `.musicxml` file carries the whole song as two monophonic parts, `Vocal`
and `Ins`. Every ABC bar becomes one `<measure>` in both parts, including
explicit rests, so the file opens as a normal two-staff score in MuseScore or
Logic Pro instead of a pile of per-section fragments.

The ABC remains the source of truth for bars, sections, tempo, key, and chord
symbols. Import replaces **only the notes** and refuses to guess when the bar
structure changed. Export preflights with `build_score` and uses the exact
piano-roll ticks (`ROLL_PPQ = 256`) as MusicXML divisions, so importing a file
that was not edited returns the original ABC byte-for-byte.

Only standard-library modules are used. See references/abc-editing.md.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from fractions import Fraction

from .abc_score import AbcError, CHORD, KEYS, NATURAL, QUALITIES, key_accidentals, meter_value
from .score import build_score, inspect_score

ROLL_PPQ = 256
DIVISIONS = ROLL_PPQ
BRIDGE_VERSION = "musicxml-v1"
DEFAULT_NAME = "yue2-score"
MAX_FILE_BYTES = 16 * 1024 * 1024

VOICE_ORDER = ("Vocal", "Ins")
INS_TOKENS = {"ins", "inst", "instrument", "instrumental"}
VOCAL_TOKENS = {"vocal", "vocals", "voice", "voices", "melody", "voc"}

KINDS = {
    "": "major",
    "m": "minor",
    "dim": "diminished",
    "aug": "augmented",
    "7": "dominant",
    "maj7": "major-seventh",
    "m7": "minor-seventh",
    "dim7": "diminished-seventh",
    "m7b5": "half-diminished",
    "sus4": "suspended-fourth",
    "sus2": "suspended-second",
    "6": "major-sixth",
    "m6": "minor-sixth",
    "7sus4": "suspended-fourth",
    "m(maj7)": "major-minor",
}

BEAT_UNITS = {
    "maxima": 32, "long": 16, "breve": 8, "whole": 4, "half": 2,
    "quarter": 1, "eighth": 0.5, "16th": 0.25, "32nd": 0.125,
    "64th": 0.0625, "128th": 0.03125, "256th": 0.015625,
    "512th": 0.0078125, "1024th": 0.00390625,
}

# Base note values in roll ticks, longest first.
NOTE_TYPES = (
    (8192, "maxima"),
    (4096, "long"),
    (2048, "breve"),
    (1024, "whole"),
    (512, "half"),
    (256, "quarter"),
    (128, "eighth"),
    (64, "16th"),
    (32, "32nd"),
    (16, "64th"),
    (8, "128th"),
    (4, "256th"),
    (2, "512th"),
    (1, "1024th"),
)

XML_DECL = b'<?xml version="1.0" encoding="UTF-8"?>\n'
DOCTYPE = (b'<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
           b'"http://www.musicxml.org/dtds/partwise.dtd">\n')

_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _display_values():
    """Standard values with zero, one, or two dots, in descending tick order."""
    by_ticks = {}
    for base, name in NOTE_TYPES:
        by_ticks.setdefault(base, (name, 0))
        if base >= 2:
            by_ticks.setdefault(base * 3 // 2, (name, 1))
        if base % 4 == 0:
            by_ticks.setdefault(base * 7 // 4, (name, 2))
    return sorted(((ticks, name, dots) for ticks, (name, dots) in by_ticks.items()), reverse=True)


_DISPLAY_VALUES = _display_values()


def _split_ticks(ticks):
    """Decompose an integer tick length into standard note values."""
    if ticks <= 0:
        raise AbcError("MusicXML cannot write a zero-length note or rest.")
    out, remaining = [], ticks
    for value, name, dots in _DISPLAY_VALUES:
        while remaining >= value:
            out.append((value, name, dots))
            remaining -= value
    if remaining:
        raise AbcError(f"MusicXML cannot represent a duration of {ticks} roll ticks.")
    return out


def _spell(pitch, key):
    """Spell a MIDI pitch using the key signature, like `build_score` does."""
    key_alt = key_accidentals(key)
    sharp_key = KEYS[key] >= 0
    candidates = []
    for letter, base in NATURAL.items():
        for alt in (0, 1, -1):
            if (base + alt) % 12 == pitch % 12:
                candidates.append((key_alt[letter] != alt, alt != 0, (alt < 0) == sharp_key, letter, alt))
    if not candidates:
        raise AbcError(f"Pitch {pitch} has no supported spelling.")
    _, _, _, letter, alt = min(candidates)
    octave = (pitch - alt) // 12 - 1
    if not 0 <= octave <= 9:
        raise AbcError(f"MusicXML writes octaves 0-9 (C0 is MIDI 12); pitch {pitch} is out of range.")
    return letter, alt, octave


def _xml_text(text):
    return _BAD_XML.sub(" ", str(text))


def _safe_name(name):
    text = (name or DEFAULT_NAME).strip()
    for suffix in (".musicxml", ".abc", ".xml"):
        if text.lower().endswith(suffix):
            text = text[:-len(suffix)]
            break
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    text = text[:60].rstrip(" .")
    return (text or DEFAULT_NAME) + ".musicxml"


def _pitch_name(text):
    match = re.fullmatch(r"([A-G])(bb|##|b|#)?", text or "")
    if match is None:
        raise AbcError(f"Unsupported pitch name {text!r}.")
    return match.group(1), {"": 0, "b": -1, "bb": -2, "#": 1, "##": 2}[match.group(2) or ""]


def _chord_parts(symbol):
    if CHORD.fullmatch(symbol) is None:
        raise AbcError(f"Unsupported chord {symbol!r}.")
    root_text, _, bass_text = symbol.partition("/")
    match = re.fullmatch(r"([A-G](?:bb|##|b|#)?)(.*)", root_text)
    if match is None:
        raise AbcError(f"Unsupported chord {symbol!r}.")
    quality = match.group(2)
    if quality not in QUALITIES:
        raise AbcError(f"Unsupported chord {symbol!r}.")
    return match.group(1), quality, (bass_text or None)


# ---------------------------------------------------------------------------
# Export


def export_song(score_abc, name=None):
    """Write the whole song as a two-part MusicXML 3.1 score.

    Returns `{"files": [{"name", "data", "bars", "parts", "notes"}],
    "summary": {...}}`. `data` is UTF-8 XML bytes; the server base64-encodes it
    for the browser.
    """
    if name is not None and not isinstance(name, str):
        raise AbcError("The MusicXML file name must be text.")
    info = inspect_score(score_abc)
    if not info["grid_available"]:
        raise AbcError("MusicXML export needs a score with a single key and meter.")
    build_score(info)  # Preflight: inspect_score accepts some documents build_score rejects.

    roll = info["roll"]
    sections = roll["sections"]
    bar_ticks = roll["bar_ticks"]
    bars = info["bars"]
    meter_value(info["meter"])
    for voice in VOICE_ORDER:
        for note in roll["tracks"][voice]:
            try:
                _spell(note["pitch"], info["key"])
            except AbcError as error:
                where = _section_at(note["start"], sections, bar_ticks)
                raise AbcError(f"{voice}: pitch {note['pitch']} in {where} cannot be written in MusicXML ({error})")

    root = ET.Element("score-partwise", {"version": "3.1"})
    identification = ET.SubElement(root, "identification")
    encoding = ET.SubElement(identification, "encoding")
    ET.SubElement(encoding, "software").text = "YuE2 ABC editor"
    miscellaneous = ET.SubElement(identification, "miscellaneous")
    ET.SubElement(miscellaneous, "miscellaneous-field", {"name": "yue2-bridge"}).text = BRIDGE_VERSION
    ET.SubElement(miscellaneous, "miscellaneous-field", {"name": "yue2-song"}).text = (
        f"bars={bars} meter={info['meter']} key={info['key']} bpm={info['bpm']}")

    part_list = ET.SubElement(root, "part-list")
    for part_id, voice, abbreviation, channel, program in (
            ("P1", "Vocal", "Voc", "1", "54"),
            ("P2", "Ins", "Ins", "2", "1")):
        score_part = ET.SubElement(part_list, "score-part", {"id": part_id})
        ET.SubElement(score_part, "part-name").text = voice
        ET.SubElement(score_part, "part-abbreviation").text = abbreviation
        instrument = ET.SubElement(score_part, "score-instrument", {"id": f"{part_id}-I1"})
        ET.SubElement(instrument, "instrument-name").text = voice
        midi = ET.SubElement(score_part, "midi-instrument", {"id": f"{part_id}-I1"})
        ET.SubElement(midi, "midi-channel").text = channel
        ET.SubElement(midi, "midi-program").text = program
        ET.SubElement(midi, "volume").text = "80"
        ET.SubElement(midi, "pan").text = "0"

    root.append(_build_part("P1", "Vocal", info))
    root.append(_build_part("P2", "Ins", info))

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    data = XML_DECL + DOCTYPE + body + b"\n"
    if len(data) > MAX_FILE_BYTES:
        raise AbcError(f"The MusicXML file would be larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")

    notes = {voice: len(roll["tracks"][voice]) for voice in VOICE_ORDER}
    return {
        "files": [{
            "name": _safe_name(name),
            "data": data,
            "bars": bars,
            "parts": list(VOICE_ORDER),
            "notes": notes,
        }],
        "summary": {
            "bars": bars,
            "bpm": info["bpm"],
            "meter": info["meter"],
            "key": info["key"],
            "divisions": DIVISIONS,
            "sections": [{"name": section["name"], "bars": section["bars"], "start": section["start"]}
                         for section in sections],
            "notes": notes,
        },
    }


def _build_part(part_id, voice, info):
    roll = info["roll"]
    notes = roll["tracks"][voice]
    chords = roll["chords"] if voice == "Vocal" else []
    sections = roll["sections"]
    bar_ticks = roll["bar_ticks"]
    bars = info["bars"]
    key = info["key"]
    section_starts = {section["start"]: (index, section["name"])
                      for index, section in enumerate(sections, 1)}

    part = ET.Element("part", {"id": part_id})
    for index in range(bars):
        measure = ET.SubElement(part, "measure", {"number": str(index + 1)})
        if index == 0:
            _attributes(measure, info)
            if voice == "Vocal":
                _tempo(measure, info["bpm"])
        if index in section_starts and voice == "Vocal":
            section_index, section_name = section_starts[index]
            _rehearsal(measure, section_name or f"Section {section_index}")

        start, end = index * bar_ticks, (index + 1) * bar_ticks
        measure_notes = [note for note in notes
                         if note["start"] < end and note["start"] + note["duration"] > start]
        measure_chords = [chord for chord in chords if start <= chord["start"] < end]

        if not measure_notes and not any(start < chord["start"] < end for chord in measure_chords):
            for chord in measure_chords:
                if chord["start"] == start:
                    _harmony(measure, chord["symbol"])
            _measure_rest(measure, bar_ticks)
            continue

        boundaries = {start, end}
        for note in measure_notes:
            boundaries.add(max(start, note["start"]))
            boundaries.add(min(end, note["start"] + note["duration"]))
        for chord in measure_chords:
            boundaries.add(chord["start"])
        boundaries = sorted(boundaries)

        for left, right in zip(boundaries, boundaries[1:]):
            if right <= left:
                continue
            for chord in measure_chords:
                if chord["start"] == left:
                    _harmony(measure, chord["symbol"])
            note = next((note for note in measure_notes
                         if note["start"] < right and note["start"] + note["duration"] > left), None)
            if note is None:
                _rest(measure, right - left)
            else:
                _note(measure, note, left, right, key)
    return part


def _attributes(measure, info):
    attributes = ET.SubElement(measure, "attributes")
    ET.SubElement(attributes, "divisions").text = str(DIVISIONS)
    key = ET.SubElement(attributes, "key")
    ET.SubElement(key, "fifths").text = str(KEYS[info["key"]])
    ET.SubElement(key, "mode").text = "minor" if info["key"].endswith("m") else "major"
    n, d = meter_value(info["meter"])
    time = ET.SubElement(attributes, "time")
    ET.SubElement(time, "beats").text = str(n)
    ET.SubElement(time, "beat-type").text = str(d)
    clef = ET.SubElement(attributes, "clef")
    ET.SubElement(clef, "sign").text = "G"
    ET.SubElement(clef, "line").text = "2"


def _tempo(measure, bpm):
    direction = ET.SubElement(measure, "direction", {"placement": "above"})
    direction_type = ET.SubElement(direction, "direction-type")
    metronome = ET.SubElement(direction_type, "metronome")
    ET.SubElement(metronome, "beat-unit").text = "quarter"
    ET.SubElement(metronome, "per-minute").text = str(bpm)
    ET.SubElement(direction, "sound", {"tempo": str(bpm)})


def _rehearsal(measure, name):
    direction = ET.SubElement(measure, "direction", {"placement": "above"})
    direction_type = ET.SubElement(direction, "direction-type")
    ET.SubElement(direction_type, "rehearsal").text = _xml_text(name)


def _harmony(measure, symbol):
    root_name, quality, bass_name = _chord_parts(symbol)
    harmony = ET.SubElement(measure, "harmony")
    root = ET.SubElement(harmony, "root")
    step, alter = _pitch_name(root_name)
    ET.SubElement(root, "root-step").text = step
    if alter:
        ET.SubElement(root, "root-alter").text = str(alter)
    kind = ET.SubElement(harmony, "kind", {"text": quality} if quality else {})
    kind.text = KINDS[quality]
    if bass_name:
        bass = ET.SubElement(harmony, "bass")
        step, alter = _pitch_name(bass_name)
        ET.SubElement(bass, "bass-step").text = step
        if alter:
            ET.SubElement(bass, "bass-alter").text = str(alter)


def _note(measure, note, left, right, key):
    step, alter, octave = _spell(note["pitch"], key)
    note_start = note["start"]
    note_end = note_start + note["duration"]
    cursor = left
    for value, name, dots in _split_ticks(right - left):
        end = cursor + value
        stop = cursor > note_start
        start = end < note_end
        element = ET.SubElement(measure, "note")
        pitch = ET.SubElement(element, "pitch")
        ET.SubElement(pitch, "step").text = step
        if alter:
            ET.SubElement(pitch, "alter").text = str(alter)
        ET.SubElement(pitch, "octave").text = str(octave)
        ET.SubElement(element, "duration").text = str(value)
        if stop:
            ET.SubElement(element, "tie", {"type": "stop"})
        if start:
            ET.SubElement(element, "tie", {"type": "start"})
        ET.SubElement(element, "voice").text = "1"
        ET.SubElement(element, "type").text = name
        for _ in range(dots):
            ET.SubElement(element, "dot")
        if stop or start:
            notations = ET.SubElement(element, "notations")
            if stop:
                ET.SubElement(notations, "tied", {"type": "stop"})
            if start:
                ET.SubElement(notations, "tied", {"type": "start"})
        cursor = end


def _rest(measure, ticks):
    for value, name, dots in _split_ticks(ticks):
        element = ET.SubElement(measure, "note")
        ET.SubElement(element, "rest")
        ET.SubElement(element, "duration").text = str(value)
        ET.SubElement(element, "voice").text = "1"
        ET.SubElement(element, "type").text = name
        for _ in range(dots):
            ET.SubElement(element, "dot")


def _measure_rest(measure, ticks):
    element = ET.SubElement(measure, "note")
    ET.SubElement(element, "rest", {"measure": "yes"})
    ET.SubElement(element, "duration").text = str(ticks)
    ET.SubElement(element, "voice").text = "1"


# ---------------------------------------------------------------------------
# Import


def import_song(score_abc, data, name="song.musicxml"):
    """Replace both voices with the notes from a full-song MusicXML file.

    The ABC controls bars, sections, tempo, key, and chords. The file must keep
    the same number of full measures. Returns an `inspect_score`-shaped dict
    plus an `import` summary. If the notes did not change, the original ABC is
    returned untouched (fast path), so exporting and re-importing is lossless.
    """
    info = inspect_score(score_abc)
    if not info["grid_available"]:
        raise AbcError("MusicXML import needs a score with a single key and meter.")
    raw = _coerce_bytes(data)
    if len(raw) > MAX_FILE_BYTES:
        raise AbcError(f"The MusicXML file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    root = _parse_root(raw, name)
    parts = [element for element in root if _local(element.tag) == "part"]
    vocal_part, ins_part, source, warnings = _identify_parts(root, parts, name)
    default_divisions = _default_divisions(root)

    vocal_notes, markers = _parse_part(vocal_part, "Vocal", info, default_divisions, name)
    ins_notes, _ = _parse_part(ins_part, "Ins", info, default_divisions, name)

    roll = info["roll"]
    tracks = {"Vocal": sorted(vocal_notes, key=lambda note: note["start"]),
              "Ins": sorted(ins_notes, key=lambda note: note["start"])}
    for voice in VOICE_ORDER:
        _check_monophonic(tracks[voice], voice, roll["sections"], roll["bar_ticks"], roll["total_ticks"])

    warnings.extend(_tempo_warnings(root, info, name))
    warnings.extend(_key_warnings(root, info, name))
    expected = [(section["start"], section["name"] or f"Section {index}")
                for index, section in enumerate(roll["sections"], 1)]
    actual = [(measure, text) for measure, text in markers]
    if actual and actual != expected:
        warnings.append(f"{name}: rehearsal marks do not match the ABC sections; the ABC sections win.")

    unchanged = tracks == roll["tracks"]
    if unchanged:
        result = dict(info)
    else:
        result = build_score({**info, "roll": {**roll, "tracks": tracks}})

    notes = {voice: len(tracks[voice]) for voice in VOICE_ORDER}
    result["import"] = {
        "file": name,
        "bars": info["bars"],
        "measures": info["bars"],
        "notes": notes,
        "parts": {voice: {"notes": notes[voice]} for voice in VOICE_ORDER},
        "source": source,
        "unchanged": unchanged,
        "warnings": warnings,
        "markers": [{"measure": measure + 1, "text": text} for measure, text in markers],
    }
    return result


def _coerce_bytes(data):
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    raise AbcError("Supply the MusicXML file as text or bytes.")


def _parse_root(raw, name):
    if raw[:4] == b"PK\x03\x04":
        raise AbcError(f"MusicXML file {name!r} is compressed (.mxl). Export uncompressed MusicXML (.musicxml).")
    probe = raw
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            probe = raw.decode("utf-16", "ignore").encode("utf-8")
        except Exception:
            pass
    if b"<!ENTITY" in probe.upper():
        raise AbcError(f"MusicXML file {name!r} contains an entity declaration, which is not allowed.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise AbcError(f"MusicXML file {name!r} is not valid XML: {error}.") from None
    local = _local(root.tag)
    if local == "score-timewise":
        raise AbcError(f"MusicXML file {name!r} is score-timewise; export score-partwise from MuseScore or Logic.")
    if local != "score-partwise":
        raise AbcError(f"MusicXML file {name!r} is not a MusicXML score (root <{local}>).")
    return root


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(element, name):
    if element is None:
        return None
    return next((child for child in element if _local(child.tag) == name), None)


def _children(element, name):
    if element is None:
        return []
    return [child for child in element if _local(child.tag) == name]


def _text(element):
    if element is None:
        return ""
    return (element.text or "").strip()


def _integer_text(element, label):
    text = _text(element)
    if not text:
        raise AbcError(f"MusicXML is missing {label}.")
    try:
        return int(text)
    except ValueError:
        raise AbcError(f"MusicXML has a non-integer {label}: {text!r}.") from None


def _part_metas(root):
    part_list = next((element for element in root if _local(element.tag) == "part-list"), None)
    metas = {}
    if part_list is None:
        return metas
    for score_part in _children(part_list, "score-part"):
        part_id = score_part.get("id")
        instrument = _child(score_part, "score-instrument")
        midi = _child(score_part, "midi-instrument")
        metas[part_id] = {
            "name": _text(_child(score_part, "part-name")),
            "abbrev": _text(_child(score_part, "part-abbreviation")),
            "instrument": _text(_child(instrument, "instrument-name")),
            "channel": _text(_child(midi, "midi-channel")),
        }
    return metas


def _tokens(text):
    return {token for token in re.split(r"[^a-z0-9]+", (text or "").lower()) if token}


def _voice_hint(meta):
    tokens = (_tokens(meta.get("name")) | _tokens(meta.get("abbrev")) | _tokens(meta.get("instrument")))
    if tokens & INS_TOKENS:
        return "Ins", "names"
    if tokens & VOCAL_TOKENS:
        return "Vocal", "names"
    channel = (meta.get("channel") or "").strip()
    if channel == "1":
        return "Vocal", "channels"
    if channel == "2":
        return "Ins", "channels"
    return None, None


def _identify_parts(root, parts, name):
    if len(parts) != 2:
        raise AbcError(f"MusicXML file {name!r} has {len(parts)} part(s); YuE2 needs exactly two: Vocal and Ins.")
    metas = _part_metas(root)
    hints = [_voice_hint(metas.get(part.get("id"), {})) for part in parts]
    assigned, sources = {}, set()
    for index, (part, (voice, source)) in enumerate(zip(parts, hints)):
        if voice is None:
            continue
        sources.add(source)
        if voice in assigned:
            raise AbcError(f"MusicXML file {name!r}: more than one part is identified as {voice}.")
        assigned[voice] = (index, part)
    if len(assigned) == 1:
        missing = "Ins" if "Vocal" in assigned else "Vocal"
        used = next(iter(assigned.values()))[0]
        assigned[missing] = (1 - used, parts[1 - used])
        sources.add("order")
    elif not assigned:
        assigned = {"Vocal": (0, parts[0]), "Ins": (1, parts[1])}
        sources.add("order")
    if "names" in sources:
        source = "part names"
    elif "channels" in sources:
        source = "MIDI channels"
    else:
        source = "part order"
    warnings = []
    if "order" in sources:
        warnings.append(f"{name}: part names were not recognized; part 1 was assigned to Vocal and part 2 to Ins.")
    return assigned["Vocal"][1], assigned["Ins"][1], source, warnings


def _default_divisions(root):
    for element in root.iter():
        if _local(element.tag) == "divisions":
            try:
                value = int(_text(element))
            except ValueError:
                return None
            return value if value > 0 else None
    return None


def _measure_divisions(bar_ticks, divisions, name, voice, measure):
    value = Fraction(bar_ticks * divisions, ROLL_PPQ)
    if value.denominator != 1:
        raise AbcError(f"MusicXML file {name!r}: divisions {divisions} cannot represent measure {measure} "
                       f"of {voice} as whole divisions; export with a finer divisions value.")
    return value.numerator


def _parse_part(part, voice, info, default_divisions, name):
    measures = _children(part, "measure")
    if len(measures) != info["bars"]:
        raise AbcError(f"MusicXML file {name!r}: the {voice} part has {len(measures)} measure(s), but the score "
                       f"has {info['bars']}. Keep the bar structure when editing.")
    bar_ticks = info["roll"]["bar_ticks"]
    total = info["roll"]["total_ticks"]
    expected_meter = meter_value(info["meter"])
    divisions = default_divisions
    open_tie = None
    notes, markers = [], []

    for measure_index, measure in enumerate(measures):
        if (measure.get("implicit") or "").lower() in ("yes", "true") or \
                (measure.get("number") or "").strip() == "0":
            raise AbcError(f"MusicXML file {name!r}: measure {measure_index + 1} of {voice} is a pickup; "
                           "the score uses full measures.")
        cursor, expected = 0, None
        measure_start = measure_index * bar_ticks

        for element in measure:
            tag = _local(element.tag)
            if tag == "attributes":
                declared = _child(element, "divisions")
                if declared is not None:
                    if cursor != 0:
                        raise AbcError(f"MusicXML file {name!r}: divisions change inside measure "
                                       f"{measure_index + 1} of {voice}.")
                    divisions = _integer_text(declared, "divisions")
                    if divisions <= 0:
                        raise AbcError(f"MusicXML file {name!r}: divisions must be positive.")
                    expected = None
                staves = _child(element, "staves")
                if staves is not None and _integer_text(staves, "staves") > 1:
                    raise AbcError(f"MusicXML file {name!r}: {voice} has multiple staves; YuE2 voices are monophonic.")
                if _child(element, "transpose") is not None:
                    raise AbcError(f"MusicXML file {name!r}: {voice} is transposing; write it at concert pitch.")
                time = _child(element, "time")
                if time is not None:
                    if cursor != 0:
                        raise AbcError(f"MusicXML file {name!r}: time signature changes inside measure "
                                       f"{measure_index + 1} of {voice}.")
                    if _child(time, "senza-misura") is not None or _child(time, "interchangeable") is not None:
                        raise AbcError(f"MusicXML file {name!r}: unsupported time signature in measure "
                                       f"{measure_index + 1} of {voice}.")
                    beats, beat_types = _children(time, "beats"), _children(time, "beat-type")
                    if len(beats) != 1 or len(beat_types) != 1:
                        raise AbcError(f"MusicXML file {name!r}: composite time signature in measure "
                                       f"{measure_index + 1} of {voice}.")
                    n, d = _integer_text(beats[0], "beats"), _integer_text(beat_types[0], "beat-type")
                    if n <= 0 or d <= 0 or d & (d - 1):
                        raise AbcError(f"MusicXML file {name!r}: invalid time signature in measure "
                                       f"{measure_index + 1} of {voice}.")
                    if (n, d) != expected_meter:
                        raise AbcError(f"MusicXML file {name!r}: {voice} is in {n}/{d}, but the score is "
                                       f"{info['meter']}. Keep the meter when editing.")
                continue
            if tag == "note":
                cursor, expected, open_tie = _parse_note(
                    element, voice, name, measure_index, cursor, expected, bar_ticks, total,
                    divisions, measure_start, open_tie, notes)
                continue
            if tag == "backup":
                raise AbcError(f"MusicXML file {name!r}: {voice} uses <backup> (several voices per staff); "
                               "YuE2 needs one monophonic voice per part.")
            if tag == "forward":
                duration = _integer_text(_child(element, "duration"), f"<forward> duration in measure {measure_index + 1}")
                if open_tie is not None:
                    raise AbcError(f"MusicXML file {name!r}: a tie in {voice} is interrupted by a gap in "
                                   f"measure {measure_index + 1}.")
                if divisions is None:
                    raise AbcError(f"MusicXML file {name!r}: {voice} uses notes before any <divisions>.")
                if expected is None:
                    expected = _measure_divisions(bar_ticks, divisions, name, voice, measure_index + 1)
                cursor += duration
                if cursor > expected:
                    raise AbcError(f"MusicXML file {name!r}: measure {measure_index + 1} of {voice} is overfull.")
                continue
            if tag == "direction":
                for direction_type in _children(element, "direction-type"):
                    for rehearsal in _children(direction_type, "rehearsal"):
                        markers.append((measure_index, _text(rehearsal)))

        if expected is None:
            if divisions is None:
                raise AbcError(f"MusicXML file {name!r}: the {voice} part has no <divisions>.")
            expected = _measure_divisions(bar_ticks, divisions, name, voice, measure_index + 1)
        if cursor != expected:
            if measure_index == 0 and cursor < expected:
                raise AbcError(f"MusicXML file {name!r}: the first measure of {voice} is short "
                               f"({cursor} of {expected} divisions); pickups are not supported.")
            kind = "short" if cursor < expected else "overfull"
            raise AbcError(f"MusicXML file {name!r}: measure {measure_index + 1} of {voice} is {kind} "
                           f"({cursor} of {expected} divisions). Keep full measures.")

    if open_tie is not None:
        raise AbcError(f"MusicXML file {name!r}: the {voice} part ends with an unterminated tie.")
    return notes, markers


def _parse_note(element, voice, name, measure_index, cursor, expected, bar_ticks, total,
                divisions, measure_start, open_tie, notes):
    where = f"measure {measure_index + 1} of {voice}"
    if _child(element, "grace") is not None:
        raise AbcError(f"MusicXML file {name!r}: grace notes in {where} are not supported.")
    if _child(element, "cue") is not None:
        raise AbcError(f"MusicXML file {name!r}: cue notes in {where} are not supported.")
    if _child(element, "chord") is not None:
        raise AbcError(f"MusicXML file {name!r}: <chord> in {where}; YuE2 voices are monophonic.")
    if _child(element, "time-modification") is not None:
        raise AbcError(f"MusicXML file {name!r}: tuplets in {where} are not supported.")
    notations = _child(element, "notations")
    if notations is not None and _child(notations, "tuplet") is not None:
        raise AbcError(f"MusicXML file {name!r}: tuplets in {where} are not supported.")
    voice_element = _child(element, "voice")
    if voice_element is not None and _text(voice_element) not in ("", "1"):
        raise AbcError(f"MusicXML file {name!r}: voice {_text(voice_element)} in {where}; only one voice per part is supported.")
    staff = _child(element, "staff")
    if staff is not None and _text(staff) not in ("", "1"):
        raise AbcError(f"MusicXML file {name!r}: staff {_text(staff)} in {where}; only one staff per part is supported.")
    duration = _integer_text(_child(element, "duration"), f"note duration in {where}")
    if duration <= 0:
        raise AbcError(f"MusicXML file {name!r}: zero-length note in {where}.")
    if divisions is None:
        raise AbcError(f"MusicXML file {name!r}: {voice} uses notes before any <divisions>.")
    if expected is None:
        expected = _measure_divisions(bar_ticks, divisions, name, voice, measure_index + 1)
    start_divisions = cursor
    cursor += duration
    if cursor > expected:
        raise AbcError(f"MusicXML file {name!r}: a note or rest in {where} crosses the barline; "
                       "split it with ties.")
    rest = _child(element, "rest")
    if rest is not None:
        if (rest.get("measure") or "").lower() in ("yes", "true"):
            if start_divisions != 0 or duration != expected:
                raise AbcError(f"MusicXML file {name!r}: a full-measure rest in {where} has the wrong duration.")
        if open_tie is not None:
            raise AbcError(f"MusicXML file {name!r}: a tie in {voice} is interrupted by a rest in {where}.")
        return cursor, expected, open_tie
    pitch_element = _child(element, "pitch")
    if pitch_element is None:
        if _child(element, "unpitched") is not None:
            raise AbcError(f"MusicXML file {name!r}: unpitched notes in {where} are not supported.")
        raise AbcError(f"MusicXML file {name!r}: a note in {where} has no pitch.")
    pitch = _pitch_value(pitch_element, name, where)

    start_ticks = Fraction(start_divisions * ROLL_PPQ, divisions)
    duration_ticks = Fraction(duration * ROLL_PPQ, divisions)
    if start_ticks.denominator != 1 or duration_ticks.denominator != 1:
        raise AbcError(f"MusicXML file {name!r}: {where} is off the editor's 1/{ROLL_PPQ}-quarter grid "
                       "(tuplets or microtiming); use straight rhythms.")
    absolute_start = measure_start + int(start_ticks)
    absolute_duration = int(duration_ticks)
    absolute_end = absolute_start + absolute_duration
    if absolute_end > total:
        raise AbcError(f"MusicXML file {name!r}: a note in {where} extends past the end of the song.")

    tie_start, tie_stop = _tie_flags(element, notations)
    if open_tie is not None:
        if not tie_stop:
            raise AbcError(f"MusicXML file {name!r}: a tie in {voice} is not continued in {where}.")
        if pitch != open_tie["pitch"]:
            raise AbcError(f"MusicXML file {name!r}: a tie in {voice} changes pitch in {where}.")
        if absolute_start != open_tie["start"] + open_tie["duration"]:
            raise AbcError(f"MusicXML file {name!r}: a tie in {voice} has a gap in {where}.")
        open_tie["duration"] = absolute_end - open_tie["start"]
        if not tie_start:
            notes.append(open_tie)
            open_tie = None
    else:
        if tie_stop:
            raise AbcError(f"MusicXML file {name!r}: a tie ends without starting in {where}.")
        note = {"start": absolute_start, "duration": absolute_duration, "pitch": pitch}
        if tie_start:
            open_tie = note
        else:
            notes.append(note)
    return cursor, expected, open_tie


def _pitch_value(pitch_element, name, where):
    step = _text(_child(pitch_element, "step")).upper()
    if step not in NATURAL:
        raise AbcError(f"MusicXML file {name!r}: invalid pitch step in {where}.")
    alter_text = _text(_child(pitch_element, "alter")) or "0"
    try:
        alter_value = float(alter_text)
    except ValueError:
        raise AbcError(f"MusicXML file {name!r}: invalid pitch alteration in {where}.") from None
    if not alter_value.is_integer():
        raise AbcError(f"MusicXML file {name!r}: microtonal pitch in {where} is not supported.")
    octave_text = _text(_child(pitch_element, "octave"))
    if not octave_text:
        raise AbcError(f"MusicXML file {name!r}: missing pitch octave in {where}.")
    try:
        octave = int(octave_text)
    except ValueError:
        raise AbcError(f"MusicXML file {name!r}: invalid pitch octave in {where}.") from None
    if not 0 <= octave <= 9:
        raise AbcError(f"MusicXML file {name!r}: octave {octave} in {where} is outside MusicXML's 0-9 range.")
    pitch = (octave + 1) * 12 + NATURAL[step] + int(alter_value)
    if not 12 <= pitch <= 127:
        raise AbcError(f"MusicXML file {name!r}: pitch {pitch} in {where} is outside the supported range "
                       "(MusicXML's lowest note is C0 = MIDI 12).")
    return pitch


def _tie_flags(element, notations):
    flags = set()
    for tie in _children(element, "tie"):
        kind = (tie.get("type") or "").lower()
        if kind == "continue":
            flags.update(("start", "stop"))
        elif kind in ("start", "stop"):
            flags.add(kind)
    for tied in _children(notations, "tied"):
        kind = (tied.get("type") or "").lower()
        if kind == "continue":
            flags.update(("start", "stop"))
        elif kind in ("start", "stop"):
            flags.add(kind)
    return "start" in flags, "stop" in flags


def _tempo_values(root):
    sound = set()
    for element in root.iter():
        if _local(element.tag) == "sound" and element.get("tempo"):
            try:
                sound.add(int(round(float(element.get("tempo")))))
            except ValueError:
                pass
    if sound:
        return sound
    metronome = set()
    for element in root.iter():
        if _local(element.tag) != "metronome":
            continue
        per_minute = _text(_child(element, "per-minute"))
        beat_unit = _text(_child(element, "beat-unit"))
        if not per_minute or beat_unit not in BEAT_UNITS:
            continue
        try:
            bpm = float(per_minute) * BEAT_UNITS[beat_unit]
        except ValueError:
            continue
        if _child(element, "beat-unit-dot") is not None:
            bpm *= 1.5
        metronome.add(int(round(bpm)))
    return metronome


def _key_values(root):
    values = set()
    for element in root.iter():
        if _local(element.tag) != "key":
            continue
        fifths = _text(_child(element, "fifths"))
        if not fifths:
            continue
        try:
            number = int(fifths)
        except ValueError:
            continue
        mode = _text(_child(element, "mode")).lower()
        values.add((number, "minor" if mode.startswith("min") else "major"))
    return values


def _tempo_warnings(root, info, name):
    values = _tempo_values(root)
    if len(values) > 1:
        return [f"{name}: the file contains multiple tempos; the score keeps {info['bpm']} BPM."]
    if values:
        bpm = next(iter(values))
        if bpm != info["bpm"]:
            return [f"{name}: the file is at {bpm} BPM; the score keeps {info['bpm']} BPM."]
    return []


def _key_warnings(root, info, name):
    values = _key_values(root)
    expected = (KEYS[info["key"]], "minor" if info["key"].endswith("m") else "major")
    if len(values) > 1:
        return [f"{name}: the file contains multiple key signatures; the score keeps {info['key']}."]
    if values and next(iter(values)) != expected:
        return [f"{name}: the file uses a different key signature; the score keeps {info['key']} "
                "and imports the notes as written."]
    return []


def _section_at(ticks, sections, bar_ticks):
    bar = ticks // bar_ticks
    for index, section in enumerate(sections, 1):
        if section["start"] <= bar < section["start"] + section["bars"]:
            return f"section {index} {section['name']!r}, bar {bar - section['start'] + 1}"
    return f"bar {bar + 1}"


def _check_monophonic(notes, voice, sections, bar_ticks, total):
    end, last = 0, None
    for note in notes:
        if note["start"] < end:
            where = _section_at(note["start"], sections, bar_ticks)
            raise AbcError(f"{voice}: notes overlap in {where} (MIDI pitches {last['pitch']} and "
                           f"{note['pitch']}). Each YuE2 voice is monophonic; shorten or remove one of the "
                           "notes in the MusicXML file.")
        if note["start"] + note["duration"] > total:
            where = _section_at(note["start"], sections, bar_ticks)
            raise AbcError(f"{voice}: a note starting in {where} extends past the end of the song.")
        end = note["start"] + note["duration"]
        last = note

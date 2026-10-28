"""Standard MIDI export/import for the native YuE2 ABC dialect.

The editor's workflow is deliberately file-based: every ABC section becomes one
``.mid`` file, and the ABC stays the source of truth. A MIDI file carries only
the notes of its section, plus enough metadata to identify that section after a
round trip through Logic Pro, MuseScore, or another DAW.

Design rules
------------
* One SMF (format 1) per section, with a conductor track followed by one track
  per voice (``Vocal`` and ``Ins``). Both voice tracks are always present, even
  when empty, so re-importing an untouched file also clears a section that was
  emptied in the DAW.
* Tick 0 of a file is the first bar of its section. Notes that start in the
  section are exported with their full duration, so a note crossing into the
  next section keeps its tail. On import, a note may only *start* inside the
  section; its end may overlap the next section.
* The file name (``01_intro.mid``) and the ``YUE2-SECTION`` marker inside the
  file both carry the zero-padded section number. If they disagree, the import
  is rejected instead of guessing.
* The ABC is authoritative for everything except notes: section order and bar
  counts, chords, tempo, meter, and key stay as they are in the loaded score.
  Meter changes in a MIDI file are rejected because they would move the grid;
  tempo/key differences produce warnings.
* YuE2 voices are monophonic. Overlapping notes are rejected with the section
  and bar, rather than silently dropped.

Only the Python standard library is used.
"""

from __future__ import annotations

import re
import struct
import unicodedata
from dataclasses import dataclass, field

from .abc_score import AbcError, KEYS, meter_value
from .score import build_score, inspect_score

PPQ = 3840                     # ticks per quarter note in exported SMFs
ROLL_PPQ = 256                 # app/score.py's ticks per quarter note
DEFAULT_QUANTUM = 32           # native 1/32-note grid in roll ticks
MAX_FILES = 256
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024

SECTION_RE = re.compile(
    r"YUE2-SECTION\s+(\d+)\s+([^\s]+)(?:\s+bars=(\d+))?(?:\s+voices=([^\s]+))?",
    re.IGNORECASE,
)
TRACK_NAME_RE = re.compile(r"YUE2(?:-SECTION)?\s+(\d+)\b(?:\s+([A-Za-z0-9][A-Za-z0-9_]*))?",
                           re.IGNORECASE)
VOICE_RE = re.compile(r"YUE2-VOICE\s+(Vocal|Ins)\b", re.IGNORECASE)

VOICE_LOOKUP = {
    "vocal": "Vocal", "voice": "Vocal", "melody": "Vocal", "vocals": "Vocal",
    "ins": "Ins", "inst": "Ins", "instrumental": "Ins", "instrument": "Ins",
}


def _slug(name: str) -> str:
    """ASCII, lower-case, underscore-separated file/marker slug."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text[:40] or "section"


def _filename(index: int, name: str, count: int) -> str:
    width = max(2, len(str(count)))
    return f"{index:0{width}d}_{_slug(name)}.mid"


def _grid_label(quantum: int) -> str:
    labels = {256: "quarter-note", 128: "8th-note", 64: "16th-note",
              32: "32nd-note", 16: "64th-note", 8: "128th-note"}
    return labels.get(quantum, f"{quantum}-tick")


# --------------------------------------------------------------------- SMF writer


def _vlq(value: int) -> bytes:
    if value < 0:
        raise ValueError("A MIDI variable-length quantity cannot be negative.")
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _meta(kind: int, payload: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(payload)) + payload


def _text_meta(kind: int, text: str) -> bytes:
    return _meta(kind, text.encode("utf-8", "replace"))


def _tempo_meta(bpm: int) -> bytes:
    microseconds = int(round(60_000_000 / bpm))
    if not 0 < microseconds <= 0xFFFFFF:
        raise AbcError("MIDI cannot represent a tempo below 4 BPM.")
    return _meta(0x51, microseconds.to_bytes(3, "big"))


def _meter_meta(meter: str) -> bytes:
    numerator, denominator = meter_value(meter)
    if numerator > 255:
        raise AbcError("MIDI time signatures support at most 255 beats per bar.")
    exponent = denominator.bit_length() - 1
    return _meta(0x58, bytes([numerator, exponent, 24, 8]))


def _key_meta(key: str) -> bytes:
    return _meta(0x59, struct.pack(">bB", KEYS[key], 1 if key.endswith("m") else 0))


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack(">I", len(payload)) + payload


def _track_chunk(events: list) -> bytes:
    """Serialise ``(tick, order, raw_event)`` tuples as an MTrk chunk."""
    payload = bytearray()
    tick = 0
    for at, _order, event in sorted(events, key=lambda item: (item[0], item[1])):
        if at < tick:
            raise ValueError("MIDI events must be sorted by tick.")
        payload += _vlq(at - tick) + event
        tick = at
    payload += _vlq(0) + b"\xFF\x2F\x00"  # end of track
    return _chunk(b"MTrk", bytes(payload))


def _smf(tracks: list) -> bytes:
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), PPQ)
    return header + b"".join(_track_chunk(track) for track in tracks)


def build_section_midi(index: int, name: str, bars: int, bpm: int, meter: str, key: str,
                       bar_ticks: int, vocal: list, ins: list, width: int = 2,
                       declared_voices: str | None = "Vocal,Ins") -> bytes:
    """Build one section file from section-relative roll-tick note dictionaries."""
    code = f"{index:0{width}d}"
    slug = _slug(name)
    section_ticks = bars * bar_ticks
    section_tick = section_ticks * (PPQ // ROLL_PPQ)
    scale = PPQ // ROLL_PPQ

    marker = f"YUE2-SECTION {code} {slug} bars={bars}"
    if declared_voices:
        marker += f" voices={declared_voices}"

    conductor = [
        (0, 0, _text_meta(0x03, f"YUE2 {code} {slug}")),
        (0, 1, _tempo_meta(bpm)),
        (0, 2, _meter_meta(meter)),
        (0, 3, _key_meta(key)),
        (0, 4, _text_meta(0x06, marker)),
        (0, 5, _text_meta(0x01, marker)),
        (section_tick, 6, _text_meta(0x06, f"YUE2-END {code}")),
    ]

    def voice_track(voice: str, channel: int, program: int, notes: list) -> list:
        events = [
            (0, 0, _text_meta(0x03, f"YUE2 {code} {slug} - {voice}")),
            (0, 1, _text_meta(0x01, f"YUE2-VOICE {voice}")),
            (0, 2, bytes([0xC0 | channel, program])),
            (section_tick, 6, _text_meta(0x06, f"YUE2-END {code}")),
        ]
        for note in notes:
            start = note["start"] * scale
            end = (note["start"] + note["duration"]) * scale
            if end <= start:
                continue
            events.append((start, 11, bytes([0x90 | channel, note["pitch"], 96])))
            events.append((end, 10, bytes([0x80 | channel, note["pitch"], 0])))
        return events

    tracks = [
        conductor,
        voice_track("Vocal", 0, 53, vocal),   # GM 54: Voice Oohs
        voice_track("Ins", 1, 0, ins),        # GM 1: Acoustic Grand Piano
    ]
    return _smf(tracks)


# --------------------------------------------------------------------- SMF reader


@dataclass
class MidiNote:
    channel: int
    start: int
    end: int
    pitch: int
    velocity: int


@dataclass
class MidiTrack:
    index: int
    name: str = ""
    texts: list = field(default_factory=list)
    markers: list = field(default_factory=list)
    meta: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    unclosed: int = 0
    end_tick: int = 0


@dataclass
class MidiFile:
    ppq: int
    format: int
    tracks: list
    tempo: int | None = None
    meter: tuple | None = None
    key_signature: tuple | None = None
    tempo_changes: int = 0
    meter_changes: int = 0
    key_changes: int = 0


def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for _ in range(5):
        if pos >= len(data):
            raise AbcError("Truncated MIDI variable-length quantity.")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise AbcError("Invalid MIDI variable-length quantity.")


def _parse_track(payload: bytes, index: int, filename: str) -> MidiTrack:
    track = MidiTrack(index=index)
    pos = 0
    tick = 0
    running = None
    active = {}  # (channel, pitch) -> [(start_tick, velocity), ...]

    while pos < len(payload):
        delta, pos = _read_vlq(payload, pos)
        tick += delta
        if pos >= len(payload):
            break
        byte = payload[pos]
        if byte & 0x80:
            status = byte
            pos += 1
            if status < 0xF0:
                running = status
            elif status in (0xF0, 0xF7) or 0xF1 <= status <= 0xF6:
                # SysEx and system-common messages cancel running status. Meta
                # events (0xFF) do not, and real-time bytes never do.
                running = None
        else:
            if running is None:
                raise AbcError(f"MIDI file {filename!r}: running status without a status byte.")
            status = running

        if status == 0xFF:
            if pos >= len(payload):
                raise AbcError(f"MIDI file {filename!r}: truncated meta event.")
            kind = payload[pos]
            pos += 1
            length, pos = _read_vlq(payload, pos)
            body = payload[pos:pos + length]
            if len(body) != length:
                raise AbcError(f"MIDI file {filename!r}: truncated meta event payload.")
            pos += length
            if kind == 0x03:
                track.name = body.decode("utf-8", "replace")
            elif kind == 0x06:
                track.markers.append(body.decode("utf-8", "replace"))
            elif kind == 0x01:
                track.texts.append(body.decode("utf-8", "replace"))
            elif kind in (0x51, 0x58, 0x59):
                track.meta.append((tick, kind, body))
            elif kind == 0x2F:
                break
            continue

        if status in (0xF0, 0xF7):
            length, pos = _read_vlq(payload, pos)
            pos += length
            continue
        if 0xF1 <= status <= 0xF6:
            # System-common messages have fixed payloads; none are meaningful here.
            pos += {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(status, 0)
            continue
        if status >= 0xF8:
            # System real-time messages carry no data.
            continue

        high = status & 0xF0
        channel = status & 0x0F
        data_len = 1 if high in (0xC0, 0xD0) else 2
        if pos + data_len > len(payload):
            raise AbcError(f"MIDI file {filename!r}: truncated channel event.")
        data = payload[pos:pos + data_len]
        pos += data_len

        if high == 0x90 and data[1] > 0:
            active.setdefault((channel, data[0]), []).append((tick, data[1]))
        elif high == 0x80 or (high == 0x90 and data[1] == 0):
            pitch = data[0]
            queue = active.get((channel, pitch))
            if queue:
                start, velocity = queue.pop(0)
                if tick > start:
                    track.notes.append(MidiNote(channel, start, tick, pitch, velocity))
            # A note-off without a note-on is ignored; DAWs sometimes emit those.

    for (channel, pitch), queue in active.items():
        for start, velocity in queue:
            track.unclosed += 1
            if tick > start:
                track.notes.append(MidiNote(channel, start, tick, pitch, velocity))
    track.end_tick = tick
    return track


def parse_midi(data: bytes, filename: str = "<midi>") -> MidiFile:
    """Parse a format 0/1 SMF. Format 2 and SMPTE division are refused."""
    if not isinstance(data, (bytes, bytearray)):
        raise AbcError(f"MIDI file {filename!r}: expected binary data.")
    data = bytes(data)
    if len(data) < 14 or data[:4] != b"MThd":
        raise AbcError(f"MIDI file {filename!r} is not a Standard MIDI File.")
    header_len = struct.unpack(">I", data[4:8])[0]
    if header_len < 6 or len(data) < 8 + header_len:
        raise AbcError(f"MIDI file {filename!r}: truncated MIDI header.")
    fmt, track_count, division = struct.unpack(">HHH", data[8:14])
    if fmt not in (0, 1):
        raise AbcError(f"MIDI file {filename!r}: format {fmt} is not supported; use format 0 or 1.")
    if division & 0x8000:
        raise AbcError(f"MIDI file {filename!r}: SMPTE time division is not supported.")
    if division == 0:
        raise AbcError(f"MIDI file {filename!r}: invalid time division.")

    pos = 8 + header_len
    tracks = []
    for index in range(track_count):
        if pos + 8 > len(data) or data[pos:pos + 4] != b"MTrk":
            raise AbcError(f"MIDI file {filename!r}: expected {track_count} track(s), found {index}.")
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        payload = data[pos + 8:pos + 8 + length]
        if len(payload) != length:
            raise AbcError(f"MIDI file {filename!r}: truncated track {index + 1}.")
        tracks.append(_parse_track(payload, index, filename))
        pos += 8 + length

    midi = MidiFile(ppq=division, format=fmt, tracks=tracks)
    tempos, meters, keys = [], [], []
    for track in tracks:
        for _tick, kind, body in track.meta:
            if kind == 0x51 and len(body) == 3:
                micros = int.from_bytes(body, "big")
                if micros > 0:
                    tempos.append(int(round(60_000_000 / micros)))
            elif kind == 0x58 and len(body) >= 2:
                meters.append((body[0], 2 ** body[1]))
            elif kind == 0x59 and len(body) >= 2:
                keys.append((struct.unpack(">b", body[0:1])[0], body[1]))
    if tempos:
        midi.tempo = tempos[0]
        midi.tempo_changes = len(set(tempos))
    if meters:
        midi.meter = meters[0]
        midi.meter_changes = len(set(meters))
    if keys:
        midi.key_signature = keys[0]
        midi.key_changes = len(set(keys))
    return midi


# ------------------------------------------------------------- section matching


def _leaf_stem(filename: str) -> str:
    leaf = re.split(r"[\\/]", filename or "")[-1]
    return leaf.rsplit(".", 1)[0] if "." in leaf else leaf


def _expected_names(sections: list) -> str:
    names = [_filename(i, section["name"], len(sections)) for i, section in enumerate(sections, 1)]
    if len(names) > 12:
        names = names[:12] + [f"... and {len(names) - 12} more"]
    return ", ".join(names)


def _canonical_voice(text: str) -> str | None:
    return VOICE_LOOKUP.get((text or "").strip().lower())


def _match_section(midi: MidiFile, filename: str, sections: list, slugs: list) -> tuple[int, frozenset | None]:
    """Return ``(section_number, declared_voices)`` or raise AbcError."""
    marker_codes, marker_slugs, marker_bars, declared_sets = set(), {}, set(), set()
    track_codes, track_slugs = set(), {}

    for track in midi.tracks:
        for text in track.texts + track.markers:
            match = SECTION_RE.search(text)
            if not match:
                continue
            code = int(match.group(1))
            marker_codes.add(code)
            marker_slugs.setdefault(code, set()).add(match.group(2).lower())
            if match.group(3):
                marker_bars.add(int(match.group(3)))
            if match.group(4):
                voices = frozenset(_canonical_voice(part) for part in match.group(4).split(","))
                if None in voices:
                    raise AbcError(f"MIDI file {filename!r}: unknown voice in 'voices={match.group(4)}'.")
                declared_sets.add(voices)
        name_match = TRACK_NAME_RE.search(track.name or "")
        if name_match:
            code = int(name_match.group(1))
            track_codes.add(code)
            if name_match.group(2):
                track_slugs.setdefault(code, set()).add(name_match.group(2).lower())

    codes = marker_codes | track_codes
    if len(codes) > 1:
        raise AbcError(f"MIDI file {filename!r} contains more than one section number: "
                       f"{', '.join(map(str, sorted(codes)))}. Export one section per file.")
    if len(marker_bars) > 1:
        raise AbcError(f"MIDI file {filename!r} declares more than one section length.")
    if len(declared_sets) > 1:
        raise AbcError(f"MIDI file {filename!r} declares conflicting voice lists.")

    code = next(iter(codes)) if codes else None
    filename_code = None
    filename_slug = None
    stem = _leaf_stem(filename)
    numbered = re.match(r"^(\d+)(?:[_\-\s]+(.*))?$", stem)
    if numbered:
        filename_code = int(numbered.group(1))
        if numbered.group(2):
            filename_slug = _slug(numbered.group(2))
    elif stem:
        filename_slug = _slug(stem)

    if code is not None and filename_code is not None and code != filename_code:
        raise AbcError(f"MIDI file {filename!r} is named for section {filename_code}, but its "
                       f"YUE2-SECTION marker says section {code}. Keep the exported name or the marker consistent.")
    if code is None:
        code = filename_code

    if code is None:
        # A bare name such as "verse.mid" is accepted only if it is unambiguous.
        matches = [i for i, slug in enumerate(slugs, 1) if filename_slug and slug == filename_slug]
        if len(matches) == 1:
            code = matches[0]
        elif len(matches) > 1:
            raise AbcError(f"MIDI file {filename!r} matches more than one section. "
                           f"Use the exported numeric file names: {_expected_names(sections)}.")
        else:
            raise AbcError(f"Cannot match MIDI file {filename!r} to a section. Keep the exported "
                           f"file name ({_expected_names(sections)}) or the YUE2-SECTION marker inside the file.")
    if not 1 <= code <= len(sections):
        raise AbcError(f"MIDI file {filename!r} points to section {code}, but the score has "
                       f"{len(sections)} section(s). Re-export the MIDI files.")

    section = sections[code - 1]
    expected_slug = slugs[code - 1]
    for source, found in (("YUE2-SECTION marker", marker_slugs.get(code, set())),
                          ("track name", track_slugs.get(code, set()))):
        if len(found) > 1:
            raise AbcError(f"MIDI file {filename!r}: its {source}s disagree about the name of "
                           f"section {code}: {', '.join(repr(value) for value in sorted(found))}.")
        if found and next(iter(found)) != expected_slug:
            raise AbcError(f"MIDI file {filename!r}: its {source} says section {code} "
                           f"{next(iter(found))!r}, but section {code} is now {section['name']!r}. "
                           f"Re-export the MIDI files.")
    # A numeric file-name prefix is a claim about the section. A bare name is
    # only a fallback when the file has no marker, so a renamed file such as
    # "Untitled.mid" must not be rejected just because its slug is unrelated.
    # If embedded metadata already confirms the code and name, a descriptive
    # suffix in the file name ("02_chorus copy.mid") is harmless.
    if filename_code is not None and filename_slug and filename_slug != expected_slug \
            and not marker_slugs.get(code) and not track_slugs.get(code):
        raise AbcError(f"MIDI file {filename!r}: the name suggests section {code} {filename_slug!r}, "
                       f"but section {code} is now {section['name']!r}. Re-export the MIDI files.")
    if marker_bars and next(iter(marker_bars)) != section["bars"]:
        raise AbcError(f"MIDI file {filename!r} says section {code} has {next(iter(marker_bars))} bar(s), "
                       f"but it now has {section['bars']}. Re-export the MIDI files.")

    declared = next(iter(declared_sets)) if declared_sets else None
    return code, declared


# ------------------------------------------------------------------------ import


def _scale_ticks(value: int, ppq: int, quantum: int) -> int:
    """Round ``value * 256 / ppq`` to the nearest multiple of ``quantum`` exactly."""
    numerator = value * ROLL_PPQ
    denominator = ppq * quantum
    return ((2 * numerator + denominator) // (2 * denominator)) * quantum


def _track_voice(track: MidiTrack) -> str | None:
    for text in track.texts + track.markers:
        match = VOICE_RE.search(text)
        if match:
            return match.group(1).capitalize()
    name = track.name or ""
    if "-" in name:
        tail = name.rsplit("-", 1)[-1].strip().lower()
        if tail in VOICE_LOOKUP:
            return VOICE_LOOKUP[tail]
    for word in re.findall(r"[A-Za-z]+", name):
        voice = VOICE_LOOKUP.get(word.lower())
        if voice:
            return voice
    return None


def _extract_voices(midi: MidiFile, filename: str, quantum: int, section_ticks: int,
                    bar_ticks: int, label: str) -> tuple[dict, set, list]:
    notes = {"Vocal": [], "Ins": []}
    present = set()
    warnings = []
    off_grid = 0
    unclosed = 0

    for track in midi.tracks:
        label_voice = _track_voice(track)
        groups = {}
        for note in track.notes:
            if note.channel == 9:
                raise AbcError(f"MIDI file {filename!r}: drums on channel 10 are not allowed in "
                               f"section {label}.")
            if label_voice:
                voice = label_voice
            elif note.channel == 0:
                voice = "Vocal"
            elif note.channel == 1:
                voice = "Ins"
            else:
                raise AbcError(f"MIDI file {filename!r}: track {track.index + 1} uses channel "
                               f"{note.channel + 1}; YuE2 only has Vocal (channel 1) and Ins (channel 2). "
                               "Keep the exported track names or channels.")
            groups.setdefault(voice, []).append(note)
        unclosed += track.unclosed
        for voice, voice_notes in groups.items():
            present.add(voice)
            for note in voice_notes:
                start = _scale_ticks(note.start, midi.ppq, quantum)
                duration = _scale_ticks(note.end - note.start, midi.ppq, quantum)
                if duration < quantum:
                    duration = quantum
                if note.start * ROLL_PPQ != start * midi.ppq or \
                        (note.end - note.start) * ROLL_PPQ != duration * midi.ppq:
                    off_grid += 1
                if start < 0 or start >= section_ticks:
                    local_bar = start // bar_ticks + 1
                    raise AbcError(f"MIDI file {filename!r}: {voice} note (pitch {note.pitch}) starts at "
                                   f"bar {local_bar} of section {label}, which has only "
                                   f"{section_ticks // bar_ticks} bar(s). Delete or move the note.")
                notes[voice].append({"start": start, "duration": duration, "pitch": note.pitch})

    if off_grid:
        warnings.append(f"{filename}: {off_grid} note(s) were quantized to the "
                        f"{_grid_label(quantum)} grid; triplets and swing are approximated.")
    if unclosed:
        warnings.append(f"{filename}: {unclosed} note(s) had no note-off and were closed at the "
                        "end of their track.")
    return notes, present, warnings


def _section_at(ticks: int, sections: list, bar_ticks: int) -> str:
    bar = ticks // bar_ticks
    for index, section in enumerate(sections, 1):
        if section["start"] <= bar < section["start"] + section["bars"]:
            return f"section {index} {section['name']!r}, bar {bar - section['start'] + 1}"
    return f"bar {bar + 1}"


def _check_monophonic(notes: list, voice: str, sections: list, bar_ticks: int, total: int) -> None:
    ordered = sorted(notes, key=lambda note: (note["start"], note["pitch"]))
    last_end = -1
    last = None
    for note in ordered:
        if note["start"] < last_end:
            where = _section_at(note["start"], sections, bar_ticks)
            raise AbcError(
                f"{voice}: notes overlap in {where} (MIDI pitch {last['pitch']} and {note['pitch']}). "
                "Each YuE2 voice is monophonic; shorten or remove one of the notes in the MIDI file."
            )
        if note["start"] + note["duration"] > total:
            where = _section_at(note["start"], sections, bar_ticks)
            raise AbcError(f"{voice}: a note starting in {where} extends past the end of the song.")
        if note["start"] + note["duration"] > last_end:
            last_end = note["start"] + note["duration"]
            last = note


def import_sections(score_abc, files: list) -> dict:
    """Replace the notes of matching sections and return a freshly built score.

    ``files`` is a list of ``{"name": str, "data": bytes}``. The loaded ABC is
    the base document; only sections addressed by a file change.
    """
    if not isinstance(files, list) or not files:
        raise AbcError("Select at least one MIDI file to import.")
    if len(files) > MAX_FILES:
        raise AbcError(f"Import at most {MAX_FILES} MIDI files at once.")

    info = inspect_score(score_abc)
    if not info["grid_available"]:
        raise AbcError("MIDI import does not support scores with key or meter changes.")
    roll = info["roll"]
    sections = roll["sections"]
    bar_ticks = roll["bar_ticks"]
    total = roll["total_ticks"]
    if not sections:
        raise AbcError("The score has no sections to import into.")

    slugs = [_slug(section["name"]) for section in sections]
    parsed = []
    total_bytes = 0
    for item in files:
        if not isinstance(item, dict):
            raise AbcError("Each MIDI file needs a name and binary data.")
        name, data = item.get("name"), item.get("data")
        if not isinstance(name, str) or not isinstance(data, (bytes, bytearray)):
            raise AbcError("Each MIDI file needs a name and binary data.")
        if len(data) > MAX_FILE_BYTES:
            raise AbcError(f"MIDI file {name!r} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_BYTES:
            raise AbcError(f"The selected MIDI files exceed {MAX_TOTAL_BYTES // (1024 * 1024)} MB in total.")
        midi = parse_midi(bytes(data), name)
        code, declared = _match_section(midi, name, sections, slugs)
        parsed.append({"file": name, "midi": midi, "code": code, "declared": declared})

    seen = {}
    for item in parsed:
        if item["code"] in seen:
            section = sections[item["code"] - 1]
            raise AbcError(f"Files {seen[item['code']]!r} and {item['file']!r} both target section "
                           f"{item['code']} {section['name']!r}. Import one file per section.")
        seen[item["code"]] = item["file"]

    unit_line = info.get("unit", "1/16")
    try:
        current_unit = max(1, 1024 // int(unit_line.split("/", 1)[1]))
    except (ValueError, IndexError):
        current_unit = 64
    quantum = max(1, min(DEFAULT_QUANTUM, current_unit, bar_ticks & -bar_ticks))

    tracks = {voice: [dict(note) for note in roll["tracks"][voice]] for voice in ("Vocal", "Ins")}
    warnings = []
    applied = []

    for item in parsed:
        code, midi, filename = item["code"], item["midi"], item["file"]
        section = sections[code - 1]
        label = f"{code} ({section['name']!r})"
        start = section["start"] * bar_ticks
        end = start + section["bars"] * bar_ticks
        section_ticks = section["bars"] * bar_ticks

        if midi.meter is not None:
            score_meter = meter_value(info["meter"])
            if midi.meter_changes > 1:
                raise AbcError(f"MIDI file {filename!r} contains more than one time signature; "
                               "YuE2 scores use one meter. Change the meter in the editor instead.")
            if midi.meter != score_meter:
                raise AbcError(f"MIDI file {filename!r} is {midi.meter[0]}/{midi.meter[1]}, but the "
                               f"score is {info['meter']}. Change the meter in the editor or re-export "
                               "the MIDI files.")
        if midi.tempo_changes > 1:
            warnings.append(f"{filename}: the MIDI file has multiple tempo changes; the score keeps "
                            f"{info['bpm']} BPM.")
        elif midi.tempo is not None and midi.tempo != info["bpm"]:
            warnings.append(f"{filename}: the MIDI file is at {midi.tempo} BPM; the score keeps "
                            f"{info['bpm']} BPM.")
        if midi.key_changes > 1:
            warnings.append(f"{filename}: the MIDI file has multiple key signatures; the score keeps "
                            f"{info['key']}.")
        elif midi.key_signature is not None:
            expected_key = (KEYS[info["key"]], 1 if info["key"].endswith("m") else 0)
            if midi.key_signature != expected_key:
                warnings.append(f"{filename}: the MIDI file uses a different key signature; the score "
                                f"keeps {info['key']}. Notes are imported as written.")

        notes, present, voice_warnings = _extract_voices(
            midi, filename, quantum, section_ticks, bar_ticks, label
        )
        warnings.extend(voice_warnings)
        authoritative = (item["declared"] or set()) | present
        for voice in ("Vocal", "Ins"):
            if voice not in authoritative:
                continue
            kept = [note for note in tracks[voice] if not start <= note["start"] < end]
            imported = notes.get(voice, [])
            for note in imported:
                note["start"] += start
            tracks[voice] = kept + imported
        applied.append({
            "file": filename,
            "section": code,
            "name": section["name"],
            "voices": sorted(authoritative),
            "notes": {voice: len(notes.get(voice, [])) for voice in sorted(authoritative)},
        })

    for voice in ("Vocal", "Ins"):
        _check_monophonic(tracks[voice], voice, sections, bar_ticks, total)

    data = dict(info)
    data["roll"] = dict(roll)
    data["roll"]["tracks"] = tracks
    result = build_score(data)
    result["import"] = {
        "applied": applied,
        "unchanged": [{"section": i, "name": section["name"]}
                      for i, section in enumerate(sections, 1) if i not in seen],
        "warnings": warnings,
    }
    return result


def export_sections(score_abc) -> dict:
    """Return one section-relative MIDI file for every section in the ABC."""
    info = inspect_score(score_abc)
    if not info["grid_available"]:
        raise AbcError("MIDI export does not support scores with key or meter changes.")
    roll = info["roll"]
    sections = roll["sections"]
    bar_ticks = roll["bar_ticks"]
    if not sections:
        raise AbcError("The score has no sections to export.")
    count = len(sections)
    width = max(2, len(str(count)))
    meter = info["meter"]
    meter_value(meter)  # validate before building any file
    if meter_value(meter)[0] > 255:
        raise AbcError("MIDI time signatures support at most 255 beats per bar.")

    files = []
    summary_sections = []
    for index, section in enumerate(sections, 1):
        start = section["start"] * bar_ticks
        end = start + section["bars"] * bar_ticks
        vocal = [dict(note) for note in roll["tracks"]["Vocal"] if start <= note["start"] < end]
        ins = [dict(note) for note in roll["tracks"]["Ins"] if start <= note["start"] < end]
        for note in vocal + ins:
            note["start"] -= start
        filename = _filename(index, section["name"], count)
        data = build_section_midi(index, section["name"], section["bars"], info["bpm"],
                                  meter, info["key"], bar_ticks, vocal, ins, width)
        files.append({
            "name": filename,
            "bytes": data,
            "section": index,
            "section_name": section["name"],
            "bars": section["bars"],
            "notes": {"Vocal": len(vocal), "Ins": len(ins)},
        })
        summary_sections.append({"section": index, "name": section["name"],
                                 "file": filename, "bars": section["bars"]})
    return {
        "files": files,
        "summary": {"sections": summary_sections, "bpm": info["bpm"],
                    "meter": meter, "key": info["key"], "ppq": PPQ},
    }

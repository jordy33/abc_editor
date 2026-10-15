"""Score-logic tests. These need no browser and no third-party packages.

    .venv/bin/python -m pytest tests/ -q      (if pytest is installed)
    .venv/bin/python tests/test_score.py      (plain runner, no pytest)

The expectations mirror `tests/test_score_editor.py` in ComfyUI-FL-YuE2 so the
standalone editor is verified to produce the same scores the ComfyUI node does.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.abc_score import AbcError
from app.score import DEFAULT_SCORE, build_score, inspect_score, score_hash


def test_default_score_is_instrumental_with_two_sections():
    info = inspect_score(DEFAULT_SCORE)
    assert info["bars"] == 8
    assert abs(info["seconds"] - 32 * 60 / 88) < 1e-9
    assert info["instrumental"] and info["grid_available"]
    assert [s["name"] for s in info["sections"]] == ["verse", "outro"]


def test_roundtrip_preserves_pitches_timing_and_chords():
    original = inspect_score(DEFAULT_SCORE)
    result = build_score(original)
    assert result["roll"] == original["roll"]
    assert result["instrumental"]


def test_ties_across_bars_and_chord_changes():
    data = inspect_score(DEFAULT_SCORE)
    data["roll"]["tracks"]["Vocal"] = [{"start": 64, "duration": 2304, "pitch": 66}]
    data["roll"]["chords"].append({"start": 320, "symbol": "D7"})
    result = build_score(data)
    assert result["roll"]["tracks"] == data["roll"]["tracks"]
    assert sorted(result["roll"]["chords"], key=lambda c: c["start"]) == sorted(data["roll"]["chords"], key=lambda c: c["start"])


def test_short_notes_and_adjacent_attacks():
    data = inspect_score(DEFAULT_SCORE)
    data["roll"]["tracks"]["Ins"] = [{"start": 1, "duration": 1, "pitch": 0}, {"start": 2, "duration": 2, "pitch": 127}]
    result = build_score(data)
    assert result["roll"]["tracks"] == data["roll"]["tracks"]


def test_key_change_does_not_transpose_notes():
    data = inspect_score(DEFAULT_SCORE)
    data["key"] = "F#"
    assert build_score(data)["roll"]["tracks"] == data["roll"]["tracks"]


def test_tempo_change_roundtrips():
    data = inspect_score(DEFAULT_SCORE)
    data["bpm"] = 143
    assert build_score(data)["bpm"] == 143


def test_windows_newlines_and_trailing_space_are_normalised():
    assert inspect_score(DEFAULT_SCORE.replace("\n", "\r\n") + "\r\n")["abc"] == DEFAULT_SCORE


def test_compressed_rests_expand_for_grid():
    score = DEFAULT_SCORE.replace('"Am7"z16|"Dm7"z16|"G7"z16|"Cmaj7"z16|', "Z4|")
    assert inspect_score(score)["sections"][0]["vocal"] == ["Z"] * 4


def test_raw_key_changes_are_preserved_and_disable_the_grid():
    score = DEFAULT_SCORE.replace('"Am7"z16', '[K:C]"Am7"z16', 1).replace("E2A2c4B2A2G4", "[K:C]E2A2c4B2A2G4")
    info = inspect_score(score)
    assert not info["grid_available"]
    assert info["abc"] == score


def test_score_hash_is_stable():
    assert score_hash(DEFAULT_SCORE) == score_hash(DEFAULT_SCORE)
    assert score_hash(DEFAULT_SCORE) != score_hash(DEFAULT_SCORE.replace("=88", "=89"))


NATIVE_SAMPLE = """X:1
T:
M:4/4
L:1/16
Q:1/4=123
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C#m
% verse
V: Vocal
Z2|"C#m"z16|"F#m"dc2c2c2BB2B2A2G2|
V: Ins
Z4|
"""


def test_export_uses_native_spelling():
    """Key-signature spelling, no redundant '1' or accidentals, Z for empty bars."""
    out = build_score(inspect_score(NATIVE_SAMPLE))["abc"]
    assert out == NATIVE_SAMPLE
    assert "=" not in out.split("K:C#m", 1)[1] and "^" not in out.split("K:C#m", 1)[1]
    assert "z16|z16" not in out


# --- rejection cases -------------------------------------------------------

INVALID_SYNTAX = [
    ("E2A2c4B2A2G4", "E2A2c4B2A2G2", "duration"),
    ('"Am7"z16', '"Cmaj9"z16', "unsupported chord"),
    ("A8z8", "A8-A8-", "unresolved tie"),
    ("A8z8", "A8-B8", "tie changes pitch"),
    ("A8z8", "[ACE]16", "unsupported token"),
    ("A8z8", '"Am7"A8z8', "Native chord symbols belong in Vocal"),
]


def test_invalid_notation_is_rejected():
    for before, after, message in INVALID_SYNTAX:
        try:
            inspect_score(DEFAULT_SCORE.replace(before, after))
        except AbcError as error:
            assert message in str(error), f"{message!r} not in {error!s}"
        else:
            raise AssertionError(f"expected rejection for {message}")


INVALID_NOTES = [
    ({"start": 1, "duration": 256, "pitch": 60}, "overlap"),
    ({"start": 8190, "duration": 256, "pitch": 60}, "past the end"),
    ({"start": 8000, "duration": 64, "pitch": 128}, "MIDI pitch"),
]


def test_invalid_piano_roll_notes_are_rejected():
    for note, message in INVALID_NOTES:
        data = inspect_score(DEFAULT_SCORE)
        data["roll"]["tracks"]["Ins"].append(note)
        try:
            build_score(data)
        except AbcError as error:
            assert message in str(error), f"{message!r} not in {error!s}"
        else:
            raise AssertionError(f"expected rejection for {message}")


def test_input_boundaries_are_rejected():
    for value in (None, [], "x" * 200001):
        try:
            inspect_score(value)
        except AbcError:
            pass
        else:
            raise AssertionError(f"expected rejection for {type(value).__name__}")


def test_unsupported_key_is_rejected():
    data = inspect_score(DEFAULT_SCORE)
    data["key"] = "H"
    try:
        build_score(data)
    except AbcError as error:
        assert "supported major or minor key" in str(error)
    else:
        raise AssertionError("expected rejection for key H")


def _run():
    """Minimal runner so the tests work without pytest installed."""
    tests = [(name, obj) for name, obj in sorted(globals().items()) if name.startswith("test_") and callable(obj)]
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

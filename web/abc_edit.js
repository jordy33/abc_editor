/*
 * Semantic text-edit engine for the native YuE2 ABC dialect.
 *
 * This is the browser-side mirror of `app/abc_score.py` (parsing semantics:
 * per-letter/per-bar accidental propagation, tie merging, Z expansion) and of
 * `app/score.py`'s `music_bar` (emission rules: minimal accidentals, bare tie
 * continuations, greedy duration decomposition, whole-rest bars as `Z`).
 *
 * Every operation rewrites only the touched bars' text and keeps each bar's
 * total duration identical by construction; the server's `/validate` endpoint
 * remains the final authority on every produced string. The notation view
 * applies these edits as surgical string splices, so scores with mid-song
 * meter changes — which the piano roll cannot model — stay fully editable.
 *
 * All functions here are pure: they take a source string (already normalised
 * by a successful validate round-trip) and return a new string plus the char
 * range of the edited note, never touching the DOM or the network.
 */

export const DURATIONS_DESC = [48, 32, 24, 16, 12, 8, 6, 4, 3, 2, 1];
const DURATION_SET = new Set(DURATIONS_DESC);

const TOKEN = /"([^"\n]*)"|\[K:([^\]\n]+)\]|(\^\^|__|\^|_|=)?([A-Ga-gz])([,']*)([0-9]*)(-?)/y;
const NATURAL = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const KEYS = {};
["Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#"]
    .forEach((k, i) => { KEYS[k] = i - 7; });
["Abm", "Ebm", "Bbm", "Fm", "Cm", "Gm", "Dm", "Am", "Em", "Bm", "F#m", "C#m", "G#m", "D#m", "A#m"]
    .forEach((k, i) => { KEYS[k] = i - 7; });

export class EditError extends Error {}

function fail(condition, message) {
    if (condition) throw new EditError(message);
}

export function keyAccidentals(key) {
    fail(!(key in KEYS), `Unsupported key ${key}`);
    const count = KEYS[key];
    const result = { C: 0, D: 0, E: 0, F: 0, G: 0, A: 0, B: 0 };
    const order = count > 0 ? "FCGDAEB" : "BEADGCF";
    for (let i = 0; i < Math.abs(count); i++) result[order[i]] = count > 0 ? 1 : -1;
    return result;
}

function meterValue(text) {
    const m = /^([1-9][0-9]*)\/([1-9][0-9]*)$/.exec(text);
    fail(!m, `Unsupported meter ${text}`);
    return { n: parseInt(m[1], 10), d: parseInt(m[2], 10) };
}

/* ------------------------------------------------------------------ analyze */

/*
 * Parse `source` (lines 0-7 are the fixed header) into a bar-indexed model
 * with absolute char offsets. Assumes the text already passed the server's
 * validator; still fails closed on anything structurally surprising.
 *
 * Bar: { voice, index, charStart, charEnd, meter:{n,d}, meterUnits, key,
 *        zrun: null|{s,e,count,pos}, events:[], chords:[], keyChanges:[] }
 * Note event: { kind:"note", letter, alt, pitch, written, baseText, units,
 *               off, span:[s,e], pieces:[{s,e}], tieIn, tieOut,
 *               chainPrev:{barIdx,eventIdx}|null, chainNext:...|null }
 * Rest event: { kind:"rest", units, off, span:[s,e] }
 * Offsets (`off`) and durations (`units`) are in L: units within the bar.
 */
export function analyze(source) {
    const lines = source.split("\n");
    const lineStart = [];
    let pos = 0;
    for (const line of lines) { lineStart.push(pos); pos += line.length + 1; }
    const unitMatch = /^L:1\/([0-9]+)$/.exec(lines[3]);
    fail(!unitMatch, "Missing L: header");
    const denom = parseInt(unitMatch[1], 10);
    const headerMeter = meterValue(lines[2].slice(2));
    const headerKey = lines[7].slice(2);
    keyAccidentals(headerKey);

    const voices = { Vocal: null, Ins: null };
    for (const name of ["Vocal", "Ins"]) {
        voices[name] = {
            meter: { ...headerMeter }, key: headerKey,
            pending: null,  // {pitch, written, ref:{barIdx,eventIdx}}
            bars: [],
        };
    }

    let cursor = 8;
    while (cursor < lines.length) {
        while (cursor < lines.length && lines[cursor].startsWith("% ")) cursor++;
        if (cursor >= lines.length || lines[cursor] === "") break;
        for (const name of ["Vocal", "Ins"]) {
            fail(lines[cursor] !== `V: ${name}`, `Expected V: ${name} at line ${cursor + 1}`);
            cursor++;
            const voice = voices[name];
            while (cursor < lines.length && /^[MK]:/.test(lines[cursor])) {
                if (lines[cursor][0] === "M") voice.meter = meterValue(lines[cursor].slice(2));
                else { voice.key = lines[cursor].slice(2); keyAccidentals(voice.key); }
                cursor++;
            }
            parseMusicLine(voice, name, lines[cursor], lineStart[cursor], denom);
            cursor++;
        }
    }
    for (const name of ["Vocal", "Ins"]) fail(voices[name].pending !== null, `${name}: unresolved tie`);
    return { source, denom, headerKey, bars: { Vocal: voices.Vocal.bars, Ins: voices.Ins.bars } };
}

function meterUnitsOf(meter, denom) {
    const units = (meter.n * denom) / meter.d;
    fail(!Number.isInteger(units), `Meter ${meter.n}/${meter.d} does not fit L:1/${denom}`);
    return units;
}

function parseMusicLine(voice, name, line, lineOffset, denom) {
    fail(line === undefined || !line.endsWith("|"), `${name}: missing music line`);
    // Split into bar bodies with absolute char ranges (excluding the barlines).
    let start = 0;
    for (let i = 0; i < line.length; i++) {
        if (line[i] !== "|") continue;
        const bodyStart = lineOffset + start, bodyEnd = lineOffset + i;
        const body = line.slice(start, i);
        start = i + 1;
        const zrun = /^\s*Z([2-4])?\s*$/.exec(body);
        if (zrun) {
            const count = zrun[1] ? parseInt(zrun[1], 10) : 1;
            for (let p = 0; p < count; p++) {
                fail(voice.pending !== null, `${name}: tie enters a full-measure rest`);
                const meterUnits = meterUnitsOf(voice.meter, denom);
                voice.bars.push({
                    voice: name, index: voice.bars.length, charStart: bodyStart, charEnd: bodyEnd,
                    meter: { ...voice.meter }, meterUnits, key: voice.key,
                    zrun: { s: bodyStart, e: bodyEnd, count, pos: p },
                    events: [{ kind: "rest", units: meterUnits, off: 0, span: [bodyStart, bodyEnd] }],
                    chords: [], keyChanges: [],
                });
            }
            continue;
        }
        parseBar(voice, name, body, bodyStart, denom);
    }
}

function parseBar(voice, name, body, bodyStart, denom) {
    const meterUnits = meterUnitsOf(voice.meter, denom);
    const bar = {
        voice: name, index: voice.bars.length, charStart: bodyStart, charEnd: bodyStart + body.length,
        meter: { ...voice.meter }, meterUnits, key: voice.key, zrun: null,
        events: [], chords: [], keyChanges: [],
    };
    const local = {};  // accidental propagation: letter -> alt, per bar
    let off = 0;
    let cursor = 0;
    while (cursor < body.length) {
        if (/\s/.test(body[cursor])) { cursor++; continue; }
        TOKEN.lastIndex = cursor;
        const m = TOKEN.exec(body);
        fail(!m || m.index !== cursor, `${name}: unsupported token at "${body.slice(cursor, cursor + 24)}"`);
        const tokS = bodyStart + cursor, tokE = bodyStart + TOKEN.lastIndex;
        cursor = TOKEN.lastIndex;
        const [, chord, inlineKey, acc, note, octave, durText, tie] = m;
        if (chord !== undefined) { bar.chords.push({ off, symbol: chord, span: [tokS, tokE] }); continue; }
        if (inlineKey !== undefined) {
            keyAccidentals(inlineKey);
            voice.key = inlineKey;
            bar.keyChanges.push({ off, key: inlineKey, span: [tokS, tokE] });
            for (const k in local) delete local[k];
            continue;
        }
        const units = durText ? parseInt(durText, 10) : 1;
        fail(!DURATION_SET.has(units), `${name}: unsupported duration ${units}`);
        if (note === "z") {
            fail(voice.pending !== null, `${name}: tie enters a rest`);
            bar.events.push({ kind: "rest", units, off, span: [tokS, tokE] });
            off += units;
            continue;
        }
        const letter = note.toUpperCase();
        let written = 60 + NATURAL[letter] + (note === note.toLowerCase() ? 12 : 0);
        written += 12 * ((octave.match(/'/g) || []).length - (octave.match(/,/g) || []).length);
        let alt = letter in local ? local[letter] : keyAccidentals(voice.key)[letter];
        if (acc) {
            alt = { "=": 0, _: -1, __: -2, "^": 1, "^^": 2 }[acc];
            local[letter] = alt;
        }
        let pitch = written + alt;
        const last = bar.events[bar.events.length - 1];
        if (voice.pending !== null) {
            const { pitch: oldPitch, written: oldWritten, ref } = voice.pending;
            if (!acc && written === oldWritten) pitch = oldPitch;
            fail(pitch !== oldPitch, `${name}: tie changes pitch`);
            if (ref === null) {
                // continuation within this bar: absorb into the open note event
                last.units += units;
                last.span[1] = tokE;
                last.pieces.push({ s: tokS, e: tokE });
                voice.pending = tie ? { pitch, written, ref: null } : null;
                off += units;
                continue;
            }
            // continuation entering this bar across the barline
            const ev = {
                kind: "note", letter, alt: pitch - written, pitch, written,
                baseText: note + octave, units, off, span: [tokS, tokE],
                pieces: [{ s: tokS, e: tokE }], tieIn: true, tieOut: false,
                chainPrev: ref, chainNext: null,
            };
            bar.events.push(ev);
            const prev = voice.bars[ref.barIdx].events[ref.eventIdx];
            prev.tieOut = true;
            prev.chainNext = { barIdx: bar.index, eventIdx: bar.events.length - 1 };
            voice.pending = tie ? { pitch, written, ref: { barIdx: bar.index, eventIdx: bar.events.length - 1 } } : null;
            off += units;
            continue;
        }
        fail(pitch < 0 || pitch > 127, `${name}: pitch outside MIDI range`);
        bar.events.push({
            kind: "note", letter, alt, pitch, written, baseText: note + octave,
            units, off, span: [tokS, tokE], pieces: [{ s: tokS, e: tokE }],
            tieIn: false, tieOut: false, chainPrev: null, chainNext: null,
        });
        if (tie) {
            // `ref` stays null while the tie may resolve inside this bar; it is
            // promoted to a cross-bar ref when the bar closes still pending.
            voice.pending = { pitch, written, ref: null };
        }
        off += units;
    }
    fail(off !== meterUnits, `${name}: bar sums ${off} units, expected ${meterUnits}`);
    if (voice.pending !== null && voice.pending.ref === null) {
        voice.pending.ref = { barIdx: bar.index, eventIdx: bar.events.length - 1 };
    }
    voice.bars.push(bar);
}

/* --------------------------------------------------------------- selection */

/* Resolve a char index (from abcjs' startChar) to the full tie chain. */
export function findNote(model, charIndex) {
    for (const name of ["Vocal", "Ins"]) {
        for (const bar of model.bars[name]) {
            if (charIndex < bar.charStart || charIndex >= bar.charEnd) continue;
            for (let i = 0; i < bar.events.length; i++) {
                const ev = bar.events[i];
                if (charIndex < ev.span[0] || charIndex >= ev.span[1]) continue;
                return makeRef(model, name, bar.index, i);
            }
            // Clicked a Z bar: abcjs maps every expanded bar to the same token.
            if (bar.events.length === 1 && bar.events[0].kind === "rest") {
                return makeRef(model, name, bar.index, 0);
            }
        }
    }
    return null;
}

/* Resolve (voice, barIdx, unit offset) to a chain — stable across re-analyze. */
export function locate(model, voiceName, barIdx, offUnits) {
    const bar = model.bars[voiceName][barIdx];
    if (!bar) return null;
    for (let i = 0; i < bar.events.length; i++) {
        const ev = bar.events[i];
        if (ev.off <= offUnits && offUnits < ev.off + ev.units) return makeRef(model, voiceName, barIdx, i);
    }
    return null;
}

function makeRef(model, voiceName, barIdx, eventIdx) {
    const bars = model.bars[voiceName];
    let b = barIdx, e = eventIdx;
    // Rewind to the head of the tie chain.
    while (bars[b].events[e].chainPrev) ({ barIdx: b, eventIdx: e } = bars[b].events[e].chainPrev);
    const chain = [];
    let totalUnits = 0;
    let cur = { barIdx: b, eventIdx: e };
    while (cur) {
        const ev = bars[cur.barIdx].events[cur.eventIdx];
        chain.push({ ...cur });
        totalUnits += ev.units;
        cur = ev.chainNext;
    }
    const head = bars[b].events[e];
    return {
        voice: voiceName, chain, totalUnits,
        kind: head.kind, pitch: head.pitch,
        barIdx: b, off: head.off,
        spans: chain.map(c => bars[c.barIdx].events[c.eventIdx].span),
    };
}

/* ---------------------------------------------------------------- emission */

function spell(pitch, key) {
    const keyAlt = keyAccidentals(key);
    const sharpKey = KEYS[key] >= 0;
    const candidates = [];
    for (const letter in NATURAL) {
        for (const alt of [0, 1, -1]) {
            if ((((NATURAL[letter] + alt) % 12) + 12) % 12 === ((pitch % 12) + 12) % 12) {
                candidates.push([keyAlt[letter] !== alt ? 1 : 0, alt !== 0 ? 1 : 0,
                                 (alt < 0) === sharpKey ? 1 : 0, letter, alt]);
            }
        }
    }
    candidates.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2] || (a[3] < b[3] ? -1 : 1));
    const [, , , letter, alt] = candidates[0];
    const octave = Math.floor((pitch - alt) / 12) - 1;
    const baseText = octave >= 5 ? letter.toLowerCase() + "'".repeat(octave - 5)
                                 : letter + ",".repeat(4 - octave);
    return { letter, alt, baseText };
}

function decompose(units) {
    const parts = [];
    let left = units;
    for (const d of DURATIONS_DESC) while (left >= d) { parts.push(d); left -= d; }
    return parts;
}

/* Re-serialise one bar from its (possibly mutated) events. */
export function emitBar(bar) {
    // Merge adjacent rests so freed time coalesces naturally.
    const events = [];
    for (const ev of bar.events) {
        const last = events[events.length - 1];
        if (ev.kind === "rest" && last && last.kind === "rest") last.units += ev.units;
        else events.push({ ...ev });
    }
    let off = 0;
    for (const ev of events) { ev.off = off; off += ev.units; }
    fail(off !== bar.meterUnits, `bar ${bar.index + 1}: events sum ${off} units, expected ${bar.meterUnits}`);
    if (!bar.chords.length && !bar.keyChanges.length && events.every(ev => ev.kind === "rest")) return "Z";

    const bounds = new Set([0, bar.meterUnits]);
    for (const ev of events) { bounds.add(ev.off); bounds.add(ev.off + ev.units); }
    for (const c of bar.chords) bounds.add(c.off);
    for (const k of bar.keyChanges) bounds.add(k.off);
    const sorted = [...bounds].sort((a, b) => a - b);

    let key = bar.key;
    let keyAlt = keyAccidentals(key);
    const local = {};
    const parts = [];
    for (let i = 0; i < sorted.length - 1; i++) {
        const a = sorted[i], b = sorted[i + 1];
        for (const k of bar.keyChanges) {
            if (k.off === a) {
                parts.push(`[K:${k.key}]`);
                key = k.key; keyAlt = keyAccidentals(key);
                for (const l in local) delete local[l];
            }
        }
        for (const c of bar.chords) if (c.off === a) parts.push(`"${c.symbol}"`);
        const ev = events.find(x => x.off <= a && a < x.off + x.units);
        fail(!ev, `bar ${bar.index + 1}: gap in events at unit ${a}`);
        const evEnd = ev.off + ev.units;
        let continuing = ev.kind === "note" && (ev.off < a || ev.tieIn);
        let remaining = b - a;
        for (const piece of decompose(remaining)) {
            remaining -= piece;
            const atEventEnd = remaining === 0 && b >= evEnd;
            const tied = ev.kind === "note" && (!atEventEnd || ev.tieOut);
            let token;
            if (ev.kind === "rest") {
                token = "z";
            } else {
                token = ev.baseText;
                if (!continuing && (ev.letter in local ? local[ev.letter] : keyAlt[ev.letter]) !== ev.alt) {
                    token = { 0: "=", 1: "^", "-1": "_", "-2": "__", 2: "^^" }[ev.alt] + token;
                    local[ev.letter] = ev.alt;
                }
            }
            parts.push(token + (piece !== 1 ? String(piece) : "") + (tied && ev.kind === "note" ? "-" : ""));
            continuing = true;
        }
    }
    return parts.join("");
}

/* Replace the touched bars' text in `source`. `changed` maps are grouped per
 * zrun token so several bars expanded from one `Zn` splice together. */
export function spliceBars(model, changedBars) {
    const singles = [];           // {s, e, text}
    const zruns = new Map();      // "s:e" -> {s, e, count, texts: Map(pos -> text)}
    for (const bar of changedBars) {
        const text = emitBar(bar);
        if (!bar.zrun) { singles.push({ s: bar.charStart, e: bar.charEnd, text }); continue; }
        const k = `${bar.zrun.s}:${bar.zrun.e}`;
        if (!zruns.has(k)) zruns.set(k, { s: bar.zrun.s, e: bar.zrun.e, count: bar.zrun.count, texts: new Map() });
        zruns.get(k).texts.set(bar.zrun.pos, text);
    }
    for (const z of zruns.values()) {
        const pieces = [];
        let run = 0;
        for (let p = 0; p <= z.count; p++) {
            const text = p < z.count ? (z.texts.get(p) ?? "Z") : null;
            if (text === "Z") { run++; continue; }
            if (run) { pieces.push(run === 1 ? "Z" : `Z${run}`); run = 0; }
            if (text !== null) pieces.push(text);
        }
        singles.push({ s: z.s, e: z.e, text: pieces.join("|") });
    }
    singles.sort((a, b) => b.s - a.s);
    let source = model.source;
    for (const r of singles) source = source.slice(0, r.s) + r.text + source.slice(r.e);
    return source;
}

/* Run an edit and re-locate the edited chain in the fresh model so the caller
 * gets exact selection spans. `anchor` = {voice, barIdx, off} in units. */
function finish(model, changedBars, anchor) {
    const source = spliceBars(model, changedBars);
    const next = analyze(source);
    const ref = locate(next, anchor.voice, anchor.barIdx, anchor.off);
    return { source, ref, selStart: ref ? ref.spans[0][0] : null, selEnd: ref ? ref.spans[0][1] : null };
}

function chainEvents(model, ref) {
    return ref.chain.map(c => ({ bar: model.bars[ref.voice][c.barIdx], ev: model.bars[ref.voice][c.barIdx].events[c.eventIdx], ...c }));
}

/* ------------------------------------------------------------- operations */

/* Lengthen/shorten the chain to `newUnits`, compensating with the rests that
 * follow it. Bar totals never change. */
export function setDuration(model, ref, newUnits) {
    fail(ref.kind !== "note", "Select a note first.");
    fail(!Number.isInteger(newUnits) || newUnits < 1, "Duration must be at least 1 unit.");
    let delta = newUnits - ref.totalUnits;
    if (delta === 0) return finish(model, [], { voice: ref.voice, barIdx: ref.barIdx, off: ref.off });
    const bars = model.bars[ref.voice];
    const links = chainEvents(model, ref);
    const touched = new Set();
    const anchor = { voice: ref.voice, barIdx: ref.barIdx, off: ref.off };

    if (delta > 0) {
        // Consume following rests only, walking bar to bar.
        const tail = links[links.length - 1];
        let barIdx = tail.barIdx;
        let evIdx = tail.eventIdx;
        let lastNote = tail.ev;
        touched.add(barIdx);
        while (delta > 0) {
            const bar = bars[barIdx];
            const nextEv = bar.events[evIdx + 1];
            if (nextEv) {
                fail(nextEv.kind !== "rest",
                     "The next note is in the way. Shorten it first, or use Merge if it has the same pitch.");
                const take = Math.min(delta, nextEv.units);
                lastNote.units += take;
                nextEv.units -= take;
                nextEv.off += take;
                delta -= take;
                if (nextEv.units === 0) bar.events.splice(evIdx + 1, 1);
                continue;
            }
            // Chain tail reaches the end of this bar: extend into the next bar.
            const nextBar = bars[barIdx + 1];
            fail(!nextBar, "The song ends here; nothing left to extend into.");
            const first = nextBar.events[0];
            fail(!first || first.kind !== "rest",
                 "The next bar starts with a note. Shorten it first, or use Merge if it has the same pitch.");
            const take = Math.min(delta, first.units);
            lastNote.tieOut = true;
            const cont = {
                kind: "note", letter: lastNote.letter, alt: lastNote.alt, pitch: lastNote.pitch,
                written: lastNote.written, baseText: lastNote.baseText, units: take, off: 0,
                span: [nextBar.charStart, nextBar.charStart], pieces: [],
                tieIn: true, tieOut: false, chainPrev: { barIdx, eventIdx: evIdx }, chainNext: null,
            };
            lastNote.chainNext = { barIdx: barIdx + 1, eventIdx: 0 };
            first.units -= take;
            first.off = take;
            if (first.units === 0) nextBar.events.splice(0, 1, cont);
            else nextBar.events.splice(0, 0, cont);
            delta -= take;
            touched.add(barIdx + 1);
            barIdx += 1;
            evIdx = 0;
            lastNote = cont;
        }
    } else {
        // Truncate from the tail; freed units become rest where they were freed.
        fail(newUnits < 1, "A note needs at least 1 unit; use Delete to turn it into a rest.");
        for (let i = links.length - 1; i >= 0; i--) {
            const { bar, ev, eventIdx } = links[i];
            const keptSoFar = sumUnitsBefore(links, i);
            const keep = Math.max(0, Math.min(ev.units, newUnits - keptSoFar));
            const freed = ev.units - keep;
            if (freed <= 0) break;
            touched.add(bar.index);
            if (keep === 0) {
                // Whole link dissolves into rest.
                bar.events.splice(eventIdx, 1, { kind: "rest", units: ev.units, off: ev.off, span: ev.span });
                const prev = ev.chainPrev ? bars[ev.chainPrev.barIdx].events[ev.chainPrev.eventIdx] : null;
                if (prev) { prev.tieOut = false; prev.chainNext = null; }
            } else {
                ev.units = keep;
                ev.tieOut = false;
                ev.chainNext = null;
                bar.events.splice(eventIdx + 1, 0, { kind: "rest", units: freed, off: ev.off + keep, span: ev.span });
            }
        }
    }
    return finish(model, [...touched].map(i => bars[i]), anchor);
}

function sumUnitsBefore(links, i) {
    let total = 0;
    for (let k = 0; k < i; k++) total += links[k].ev.units;
    return total;
}

/* Tie the chain into the immediately following note (same sounding pitch). */
export function mergeWithNext(model, ref) {
    fail(ref.kind !== "note", "Select a note first.");
    const bars = model.bars[ref.voice];
    const tail = ref.chain[ref.chain.length - 1];
    const bar = bars[tail.barIdx];
    const ev = bar.events[tail.eventIdx];
    const touched = [bar];
    let nextEv = bar.events[tail.eventIdx + 1];
    let nextBar = bar;
    if (!nextEv) {
        nextBar = bars[tail.barIdx + 1];
        fail(!nextBar, "The song ends after this note.");
        nextEv = nextBar.events[0];
        touched.push(nextBar);
    }
    fail(!nextEv || nextEv.kind !== "note", "The next event is a rest; use + to extend into it instead.");
    fail(nextEv.pitch !== ref.pitch, "Merge needs the next note to have the same pitch.");
    fail(nextEv.tieIn, "Already merged.");
    if (nextBar === bar) {
        ev.units += nextEv.units;
        if (nextEv.tieOut) { ev.tieOut = true; ev.chainNext = nextEv.chainNext; relinkPrev(bars, nextEv.chainNext, { barIdx: bar.index, eventIdx: tail.eventIdx }); }
        bar.events.splice(tail.eventIdx + 1, 1);
    } else {
        ev.tieOut = true;
        ev.chainNext = { barIdx: nextBar.index, eventIdx: 0 };
        nextEv.tieIn = true;
        nextEv.chainPrev = { barIdx: bar.index, eventIdx: tail.eventIdx };
    }
    return finish(model, touched, { voice: ref.voice, barIdx: ref.barIdx, off: ref.off });
}

function relinkPrev(bars, next, prevRef) {
    if (!next) return;
    const ev = bars[next.barIdx].events[next.eventIdx];
    ev.chainPrev = prevRef;
}

/* Chromatic repitch of the whole chain. */
export function changePitch(model, ref, deltaSemitones) {
    fail(ref.kind !== "note", "Select a note first.");
    const pitch = ref.pitch + deltaSemitones;
    fail(pitch < 0 || pitch > 127, "That pitch is outside the MIDI range.");
    return repitch(model, ref, pitch);
}

/* Staff-step repitch (for drag): move N diatonic steps, key-signature alt. */
export function changePitchDiatonic(model, ref, steps) {
    fail(ref.kind !== "note", "Select a note first.");
    const bars = model.bars[ref.voice];
    const head = bars[ref.chain[0].barIdx].events[ref.chain[0].eventIdx];
    const letters = "CDEFGAB";
    const octave = Math.floor(head.written / 12) - 1;
    let idx = letters.indexOf(head.letter) + 7 * octave + steps;
    const letter = letters[((idx % 7) + 7) % 7];
    const newOctave = Math.floor(idx / 7);
    const key = bars[ref.chain[0].barIdx].key;
    const alt = keyAccidentals(key)[letter];
    const pitch = 12 * (newOctave + 1) + NATURAL[letter] + alt;
    fail(pitch < 0 || pitch > 127, "That pitch is outside the MIDI range.");
    return repitch(model, ref, pitch, { letter, alt, baseText: baseTextFor(letter, newOctave) });
}

function baseTextFor(letter, octave) {
    return octave >= 5 ? letter.toLowerCase() + "'".repeat(octave - 5)
                       : letter + ",".repeat(4 - octave);
}

function repitch(model, ref, pitch, spelling = null) {
    const bars = model.bars[ref.voice];
    const key = bars[ref.chain[0].barIdx].key;
    const s = spelling || spell(pitch, key);
    const touched = [];
    for (const c of ref.chain) {
        const ev = bars[c.barIdx].events[c.eventIdx];
        ev.pitch = pitch;
        ev.letter = s.letter;
        ev.alt = s.alt;
        ev.written = pitch - s.alt;
        ev.baseText = s.baseText;
        touched.push(bars[c.barIdx]);
    }
    return finish(model, touched, { voice: ref.voice, barIdx: ref.barIdx, off: ref.off });
}

/* Replace the whole chain with rests. */
export function toRest(model, ref) {
    fail(ref.kind !== "note", "Select a note first.");
    const bars = model.bars[ref.voice];
    const touched = [];
    for (const c of ref.chain) {
        const bar = bars[c.barIdx];
        const ev = bar.events[c.eventIdx];
        bar.events.splice(c.eventIdx, 1, { kind: "rest", units: ev.units, off: ev.off, span: ev.span });
        touched.push(bar);
    }
    return finish(model, touched, { voice: ref.voice, barIdx: ref.barIdx, off: ref.off });
}

/* Turn a selected rest into a note. Default pitch: the previous note in the
 * voice, else the key's tonic. */
export function toNote(model, ref, pitch = null) {
    fail(ref.kind !== "rest", "Select a rest first.");
    const bars = model.bars[ref.voice];
    const c = ref.chain[0];
    const bar = bars[c.barIdx];
    const ev = bar.events[c.eventIdx];
    if (pitch === null) {
        pitch = previousPitch(bars, c.barIdx, c.eventIdx);
        if (pitch === null) {
            const tonicLetter = bar.key.replace(/m$/, "")[0];
            const accChar = bar.key.replace(/m$/, "").slice(1);
            pitch = 60 + NATURAL[tonicLetter] + (accChar === "#" ? 1 : accChar === "b" ? -1 : 0);
        }
    }
    fail(pitch < 0 || pitch > 127, "That pitch is outside the MIDI range.");
    const s = spell(pitch, bar.key);
    bar.events.splice(c.eventIdx, 1, {
        kind: "note", letter: s.letter, alt: s.alt, pitch, written: pitch - s.alt,
        baseText: s.baseText, units: ev.units, off: ev.off, span: ev.span,
        pieces: [], tieIn: false, tieOut: false, chainPrev: null, chainNext: null,
    });
    return finish(model, [bar], { voice: ref.voice, barIdx: c.barIdx, off: ev.off });
}

function previousPitch(bars, barIdx, eventIdx) {
    for (let b = barIdx; b >= 0; b--) {
        const events = bars[b].events;
        for (let i = (b === barIdx ? eventIdx : events.length) - 1; i >= 0; i--) {
            if (events[i].kind === "note") return events[i].pitch;
        }
    }
    return null;
}

/* Expose for the CDP-driven browser checks. */
if (typeof window !== "undefined") {
    window.yue2AbcEdit = {
        analyze, findNote, locate, emitBar, spliceBars, keyAccidentals,
        setDuration, mergeWithNext, changePitch, changePitchDiatonic, toRest, toNote,
        DURATIONS_DESC, EditError,
    };
}

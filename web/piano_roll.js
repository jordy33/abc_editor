export function el(tag, text, parent) {
    const item = document.createElement(tag);
    if (text) item.textContent = text;
    parent?.append(item);
    return item;
}

export function button(parent, text, action, secondary = false) {
    const item = el("button", text, parent);
    item.type = "button"; item.className = secondary ? "secondary" : ""; item.onclick = action;
    return item;
}

function combo(parent, title, options, value, change) {
    const item = el("select", null, el("label", title, parent));
    for (const option of options) {
        const [value, label] = Array.isArray(option) ? option : [option, option];
        el("option", label, item).value = value;
    }
    item.value = value; item.onchange = () => change(item.value);
    return item;
}

const PPQ = 256, KEYS = 48, ROW = 16, TOP = 24;
const pitchName = pitch => ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"][pitch % 12] + (Math.floor(pitch / 12) - 1);
function shape(tag, attributes, parent) {
    const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, value);
    parent.append(item); return item;
}

const pitchClass = name => ({C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11}[name[0]] + [...name.slice(1)].reduce((sum, accidental) => sum + (accidental === "#" ? 1 : accidental === "b" ? -1 : 0), 0) + 12) % 12;

/**
 * How many beats a bar contains, from the score's meter.
 *
 * The beat is the quarter note, so a bar holds `numerator * 4 / denominator`
 * of them: 4/4 -> 4, 3/4 -> 3, 5/4 -> 5, 6/8 -> 3, 12/8 -> 6. This replaces a
 * hard-coded `/4`, which silently mislabelled every compound meter.
 */
export function beatsPerBar(meter) {
    const match = /^(\d+)\s*\/\s*(\d+)$/.exec(meter ?? "");
    if (!match) return 4;
    const [, n, d] = match.map(Number);
    if (!n || !d) return 4;
    return (n * 4) / d;
}

/* Chord voicing ------------------------------------------------------------
 * The score stores only a chord *symbol* ("C#m7"), not notes. To hear it we
 * expand that symbol into a sensible voicing, the same way a keyboard player
 * would: root in the bass, chord tones stacked above it, kept in a register
 * below the melody so the two do not fight.
 *
 * Intervals are semitones above the root, matching app/abc_score.py QUALITIES.
 */
const CHORD_INTERVALS = {
    "": [0, 4, 7], "m": [0, 3, 7], "dim": [0, 3, 6], "aug": [0, 4, 8],
    "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10],
    "dim7": [0, 3, 6, 9], "m7b5": [0, 3, 6, 10], "sus4": [0, 5, 7],
    "sus2": [0, 2, 7], "6": [0, 4, 7, 9], "m6": [0, 3, 7, 9],
    "7sus4": [0, 5, 7, 10], "m(maj7)": [0, 3, 7, 11],
};

/** Split "C#m7/G#" into its parts. Returns null for an unparseable symbol. */
function parseChord(symbol) {
    const match = /^([A-G](?:bb|##|b|#)?)(.*?)(?:\/([A-G](?:bb|##|b|#)?))?$/.exec(symbol ?? "");
    if (!match) return null;
    const [, rootText, quality, bassText] = match;
    const intervals = CHORD_INTERVALS[quality];
    if (!intervals) return null;
    // Middle octave: C4..B4 -> MIDI 60..71. pitchClass alone gives 0..11.
    const root = 60 + pitchClass(rootText);
    const bass = bassText === undefined ? root : 60 + pitchClass(bassText);
    return {root, intervals, bass};
}

/**
 * Turn a chord symbol into MIDI pitches, voiced in a low register.
 * `lowest` is the MIDI note the bass should sit on; chord tones stack above.
 */
function chordVoicing(symbol, lowest = 40) {
    const parsed = parseChord(symbol);
    if (!parsed) return [];
    const notes = new Set();
    // Place the bass note at `lowest` (or as near it as its pitch class allows).
    let bass = parsed.bass;
    while (bass < lowest - 6) bass += 12;
    while (bass > lowest + 6) bass -= 12;
    notes.add(bass);
    // Stack the chord tones above the bass, within a tenth or so.
    for (const interval of parsed.intervals) {
        let pitch = parsed.root + interval;
        while (pitch < bass) pitch += 12;
        while (pitch > bass + 16) pitch -= 12;
        notes.add(pitch);
    }
    return [...notes].filter(pitch => pitch >= 0 && pitch <= 127).sort((a, b) => a - b);
}

export function transposeScore(data, key) {
    let delta = (pitchClass(key.replace(/m$/, "")) - pitchClass(data.key.replace(/m$/, "")) + 12) % 12;
    if (delta > 6) delta -= 12;
    const notes = Object.values(data.roll.tracks).flat();
    if (notes.some(note => note.pitch + delta < 0 || note.pitch + delta > 127)) throw new Error("That key would move a note outside the supported pitch range.");
    for (const note of notes) note.pitch += delta;
    const names = key.includes("b") || ["F", "Dm", "Gm", "Cm", "Fm"].includes(key) ? ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"] : ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
    for (const chord of data.roll.chords) chord.symbol = chord.symbol.replace(/(^|\/)([A-G](?:bb|##|b|#)?)/g, (_, prefix, root) => prefix + names[(pitchClass(root) + delta + 12) % 12]);
    data.key = key;
}

export class PianoRoll {
    constructor(editor) { this.editor = editor; this.section = 0; this.track = "Ins"; this.snap = 64; this.low = 48; this.selected = new Set(); this.position = 0; this.playing = false; this.metronome = false; this.harmony = true; }
    get data() { return this.editor.data; }
    row(parent) { const item = el("div", null, parent); item.className = "row"; return item; }
    bounds() {
        const section = this.data.roll.sections[this.section], start = section.start * this.data.roll.bar_ticks;
        return {section, start, end: start + section.bars * this.data.roll.bar_ticks};
    }
    render() {
        const scroll = {left: this.scroll?.scrollLeft || 0, top: this.scroll?.scrollTop || 0};
        this.section = Math.min(this.section, this.data.roll.sections.length - 1); this.selected.clear();
        const root = this.editor.piano; root.replaceChildren();
        const controls = this.row(root);
        const tempo = el("input", null, el("label", "Tempo", controls));
        tempo.type = "number"; tempo.min = 1; tempo.max = 1000; tempo.value = this.data.bpm;
        tempo.onchange = () => this.editor.commit(data => { data.bpm = Number(tempo.value); });
        const keyInput = combo(controls, "Key", this.data.keys, this.data.key, key => this.editor.commit(data => transposeScore(data, key)));
        keyInput.title = "Transpose all notes and chord roots by the same interval. Rhythm and melodic intervals are preserved; changing major/minor does not rewrite the melody.";
        combo(controls, "Melody", [["Ins", "Instrumental"], ["Vocal", "Sung melody"]], this.track, track => { this.pause(); this.track = track; this.render(); });
        combo(controls, "Snap", [[256, "Quarter notes"], [128, "Eighth notes"], [64, "Sixteenth notes"], [32, "32nd notes"]], this.snap, snap => { this.snap = Number(snap); this.draw(); });
        button(controls, "Center notes", () => this.fit(), true);
        const sections = this.row(root);
        combo(sections, "Section", this.data.roll.sections.map((s, i) => [i, `${i + 1}. ${s.name}`]), this.section, index => { this.pause(); this.section = Number(index); this.position = this.bounds().start; this.render(); });
        const arrangement = el("details", null, root); el("summary", "Arrange sections", arrangement);
        const structure = this.row(arrangement);
        const name = el("input", null, structure); name.value = this.bounds().section.name; name.style.width = "120px";
        name.setAttribute("aria-label", "Section name"); name.onchange = () => this.editor.commit(data => { data.roll.sections[this.section].name = name.value; });
        button(structure, "+ Section", () => this.editor.commit(data => { data.roll.sections.push({name: "new section", bars: 4}); }));
        button(structure, "Duplicate", () => this.duplicateSection(), true);
        button(structure, "Earlier", () => this.moveSection(-1), true).disabled = this.section === 0;
        button(structure, "Later", () => this.moveSection(1), true).disabled = this.section === this.data.roll.sections.length - 1;
        button(structure, "Remove section", () => this.removeSection(), true).disabled = this.data.roll.sections.length === 1;
        const tools = this.row(root);
        this.playButton = button(tools, this.playing ? "Pause" : "Play", () => this.togglePlayback()); button(tools, "Stop", () => this.stop(), true);
        const metronome = el("input", null, el("label", "Metronome", tools)); metronome.type = "checkbox"; metronome.checked = this.metronome;
        metronome.onchange = () => { const playing = this.playing; this.pause(); this.metronome = metronome.checked; if (playing) this.play(); this.editor.root.focus(); };
        const harmony = el("input", null, el("label", "Harmony", tools)); harmony.type = "checkbox"; harmony.checked = this.harmony;
        harmony.title = "Hear the chord symbols during playback. They sound in the Sung melody voice, where YuE2 keeps them.";
        harmony.onchange = () => { const playing = this.playing; this.pause(); this.harmony = harmony.checked; if (playing) this.play(); this.editor.root.focus(); };
        button(tools, "Delete selected", () => this.deleteNote(), true);
        button(tools, "Clear melody", () => this.editor.commit(data => { data.roll.tracks[this.track] = []; }), true);
        button(tools, "+ Bar", () => this.addBar(), true);
        button(tools, "Octave ↓", () => { this.low = Math.max(0, this.low - 12); this.draw(); }, true);
        button(tools, "Octave ↑", () => { this.low = Math.min(92, this.low + 12); this.draw(); }, true);
        button(tools, "Instrumental only", () => this.editor.commit(data => { data.roll.tracks.Vocal = []; this.track = "Ins"; }), true);
        // Harmony opens in a modal rather than inline: editing a chord re-renders
        // the whole roll, which would scroll the page away from an inline panel
        // on every change. A modal keeps its own position.
        this.harmonyButton = button(tools, "Chords…", () => this.openHarmony());
        this.harmonyButton.title = "Edit the chord progression beat by beat";
        el("p", "Shift-drag to box-select notes; Shift-click toggles a note. Drag selected notes to move them together. Draw on empty space; drag a single note’s right edge to resize. Drag the timeline to scrub. Space plays/pauses; arrows move the selection; Shift + up/down shifts an octave. Delete removes; Ctrl/Cmd + Z undoes.", root).className = "help";
        this.noteStatus = el("div", "Select a note to see pitch, position and length.", root); this.noteStatus.className = "help";
        // A strip of per-bar chord buttons sits directly above the timeline, so
        // the progression reads at a glance and any bar opens its own editor.
        this.chordStrip = el("div", null, root);
        this.chordStrip.className = "chord-strip";
        const stripLabel = el("div", "♫", this.chordStrip);
        stripLabel.className = "chord-strip-label";
        stripLabel.title = "Chords by bar — click a bar to edit its harmony";
        this.chordStripViewport = el("div", null, this.chordStrip);
        this.chordStripViewport.className = "chord-strip-viewport";
        this.chordStripRow = el("div", null, this.chordStripViewport);
        this.chordStripRow.className = "chord-strip-row";
        this.scroll = el("div", null, root); this.scroll.className = "roll";
        this.draw(); this.scroll.scrollLeft = scroll.left; this.scroll.scrollTop = scroll.top;
    }

    /**
     * Rebuild the per-bar chord strip.
     *
     * Widths mirror `draw()` exactly so each button lines up with its bar in the
     * grid below; called from draw() so the two never drift apart.
     */
    drawChordStrip(width) {
        if (!this.chordStripRow) return;
        const {start, section} = this.bounds();
        const barTicks = this.data.roll.bar_ticks;
        const px = width ?? this.zoom ?? 1;
        this.chordStripRow.replaceChildren();
        const barWidth = barTicks / PPQ * px;
        for (let bar = 0; bar < section.bars; bar++) {
            const barStart = start + bar * barTicks, barEnd = barStart + barTicks;
            const inBar = this.data.roll.chords
                .filter(chord => chord.start >= barStart && chord.start < barEnd)
                .sort((a, b) => a.start - b.start);
            const item = el("button", null, this.chordStripRow);
            item.type = "button";
            item.className = "chord-strip-button" + (inBar.length ? "" : " empty");
            item.style.width = `${Math.max(30, barWidth)}px`;
            const number = el("span", String(section.start + bar + 1), item);
            number.className = "chord-strip-number";
            // Summarise the bar: one symbol, or several if it changes mid-bar.
            const symbol = el("span", inBar.length ? [...new Set(inBar.map(c => c.symbol))].join(" ") : "—", item);
            symbol.className = "chord-strip-symbol";
            item.title = inBar.length
                ? `Bar ${section.start + bar + 1}: ${inBar.map(c => c.symbol).join(" → ")} — click to edit`
                : `Bar ${section.start + bar + 1}: no chord — click to add one`;
            item.onclick = () => this.openHarmony(bar);
        }
        this.syncChordStrip();
    }

    /** Keep the strip horizontally aligned with the roll's scroll position. */
    syncChordStrip() {
        if (!this.chordStripRow) return;
        this.chordStripRow.style.transform = `translateX(${-(this.scroll?.scrollLeft || 0)}px)`;
    }
    draw() {
        this.scroll.replaceChildren(); const {start, end, section} = this.bounds();
        this.zoom = ((this.scroll.clientWidth || 760) - KEYS) * PPQ / (end - start);
        const PX = this.zoom;
        const width = KEYS + (end - start) / PPQ * PX, height = TOP + 36 * ROW;
        this.svg = shape("svg", {width, height, viewBox: `0 0 ${width} ${height}`, role: "application", "aria-label": `${this.track} piano roll`}, this.scroll);
        for (let row = 0; row < 36; row++) {
            const pitch = this.low + 35 - row, black = [1, 3, 6, 8, 10].includes(pitch % 12);
            shape("rect", {x: 0, y: TOP + row * ROW, width, height: ROW, fill: black ? "#211029" : "#302038", stroke: "#44304e", "stroke-width": 0.5}, this.svg);
        }
        for (let tick = 0; tick <= end - start; tick += this.snap) {
            const x = KEYS + tick / PPQ * PX;
            shape("line", {x1: x, x2: x, y1: TOP, y2: height, stroke: tick % this.data.roll.bar_ticks === 0 ? "#b293c0" : "#573460", "stroke-width": 0.5}, this.svg);
        }
        shape("rect", {x: KEYS, y: 0, width: width - KEYS, height: TOP, fill: "#16727c", cursor: "ew-resize"}, this.svg);
        const labelEvery = Math.max(1, Math.ceil(60 / (this.data.roll.bar_ticks / PPQ * PX)));
        for (let bar = 0; bar < section.bars; bar += labelEvery) shape("text", {x: KEYS + bar * this.data.roll.bar_ticks / PPQ * PX + 5, y: 16, fill: "#fff", "font-size": 11, "pointer-events": "none"}, this.svg).textContent = `Bar ${section.start + bar + 1}`;
        this.noteRects = [];
        this.data.roll.tracks[this.track].forEach((note, index) => {
            if (note.start >= end || note.start + note.duration <= start || note.pitch < this.low || note.pitch >= this.low + 36) return;
            const x = KEYS + (Math.max(start, note.start) - start) / PPQ * PX;
            const w = (Math.min(end, note.start + note.duration) - Math.max(start, note.start)) / PPQ * PX;
            const rect = shape("rect", {x, y: TOP + (this.low + 35 - note.pitch) * ROW + 1, width: w, height: ROW - 2, rx: 3,
                fill: this.track === "Ins" ? "#43cfb1" : "#c28aff", stroke: "#16727c", "stroke-width": 1, "data-note": index, cursor: "grab"}, this.svg);
            this.noteRects.push({index, rect});
            shape("title", {}, rect).textContent = `${pitchName(note.pitch)} · ${note.duration / PPQ} beats`;
            if (w >= 8) shape("rect", {x: x + w - 4, y: TOP + (this.low + 35 - note.pitch) * ROW + 3, width: 3, height: ROW - 6, fill: "#fff", opacity: 0.65, "data-note": index, cursor: "ew-resize"}, this.svg);
            if (w >= 32) shape("text", {x: x + 3, y: TOP + (this.low + 35 - note.pitch) * ROW + 12, fill: "#18352f", "font-size": 10, "pointer-events": "none"}, this.svg).textContent = pitchName(note.pitch);
        });
        const keyboard = shape("g", {}, this.svg);
        shape("rect", {x: 0, y: 0, width: KEYS, height: TOP, fill: "#29003d"}, keyboard);
        for (let row = 0; row < 36; row++) {
            const pitch = this.low + 35 - row, black = [1, 3, 6, 8, 10].includes(pitch % 12);
            shape("rect", {x: 0, y: TOP + row * ROW, width: KEYS, height: ROW, fill: black ? "#392342" : "#e6dfea", stroke: "#765287", cursor: "pointer"}, keyboard);
            shape("text", {x: 4, y: TOP + row * ROW + 12, fill: black ? "#ddd" : "#211029", "font-size": 10, "pointer-events": "none"}, keyboard).textContent = pitchName(pitch);
        }
        this.scroll.onscroll = () => { keyboard.setAttribute("transform", `translate(${this.scroll.scrollLeft || 0},0)`); this.syncChordStrip(); };
        this.scroll.onscroll();
        // The strip is built from the same zoom the grid just used, so the two
        // stay aligned; rebuilt on every draw because section/width can change.
        this.drawChordStrip(PX);
        this.cursor = shape("line", {y1: 0, y2: height, stroke: "#fff", "stroke-width": 2, cursor: "ew-resize", "data-playhead": "true"}, this.svg);
        this.cursorHandle = shape("rect", {y: 1, width: 10, height: TOP - 2, rx: 3, fill: "#fff", cursor: "ew-resize", "data-playhead": "true"}, this.svg);
        this.updateCursor();
        this.showSelection();
        this.svg.oncontextmenu = event => { event.preventDefault(); const index = event.target.getAttribute("data-note"); if (index !== null) { this.selected = new Set([Number(index)]); this.deleteNote(); } };
        this.svg.onpointerdown = event => this.pointer(event);
    }
    pointer(event) {
        const PX = this.zoom;
        if (event.button !== 0 || this.editor.busy) return;
        event.preventDefault(); event.stopPropagation(); this.editor.root.focus({preventScroll: true});
        const {start, end} = this.bounds(), svg = this.svg, rect = svg.getBoundingClientRect();
        const width = Number(svg.getAttribute("width")), height = Number(svg.getAttribute("height"));
        const point = e => ({x: (e.clientX - rect.left) * width / rect.width, y: (e.clientY - rect.top) * height / rect.height});
        const first = point(event);
        if (event.shiftKey && first.x >= KEYS && first.y >= TOP) { this.boxSelect(event, point); return; }
        if ((first.y < TOP && first.x >= KEYS) || event.target.getAttribute("data-playhead")) { this.scrub(event, point); return; }
        if (first.y < TOP) return;
        if (first.x < KEYS + (this.scroll.scrollLeft || 0)) { this.audition(this.low + 35 - Math.floor((first.y - TOP) / ROW)); return; }
        const hit = event.target.getAttribute("data-note"), index = hit === null ? -1 : Number(hit);
        this.pause();
        if (this.selected.has(index) && this.selected.size > 1) { this.moveSelection(event, point, index); return; }
        const old = index < 0 ? null : structuredClone(this.data.roll.tracks[this.track][index]); this.selected = new Set(index < 0 ? [] : [index]);
        this.showSelection();
        const resize = old && event.target.getAttribute("cursor") === "ew-resize";
        const next = old ? structuredClone(old) : {start: Math.max(start, Math.min(end - this.snap, start + Math.floor((first.x - KEYS) / PX * PPQ / this.snap) * this.snap)),
            duration: Math.min(this.snap, end - start), pitch: Math.max(0, Math.min(127, this.low + 35 - Math.floor((first.y - TOP) / ROW)))};
        const preview = shape("rect", {rx: 3, fill: "#fff", opacity: 0.65, "pointer-events": "none"}, svg);
        let heardPitch;
        const paint = () => {
            if (next.pitch !== heardPitch) { heardPitch = next.pitch; this.audition(next.pitch); }
            this.noteStatus.textContent = `${pitchName(next.pitch)} · bar ${Math.floor(next.start / this.data.roll.bar_ticks) + 1} · beat ${1 + (next.start % this.data.roll.bar_ticks) / PPQ} · ${next.duration / PPQ} beats long`;
            preview.setAttribute("x", KEYS + (Math.max(start, next.start) - start) / PPQ * PX);
            preview.setAttribute("y", TOP + (this.low + 35 - next.pitch) * ROW + 1);
            preview.setAttribute("width", (Math.min(end, next.start + next.duration) - Math.max(start, next.start)) / PPQ * PX);
            preview.setAttribute("height", ROW - 2);
        };
        paint(); svg.setPointerCapture(event.pointerId);
        svg.onpointermove = e => {
            const p = point(e);
            if (old) {
                const delta = Math.round((p.x - first.x) / PX * PPQ / this.snap) * this.snap;
                if (resize) next.duration = Math.max(this.snap, Math.min(this.data.roll.total_ticks - old.start, old.duration + delta));
                else {
                    next.start = Math.max(0, Math.min(this.data.roll.total_ticks - old.duration, old.start + delta));
                    next.pitch = Math.max(0, Math.min(127, old.pitch - Math.round((p.y - first.y) / ROW)));
                }
            } else next.duration = Math.min(end - next.start, Math.max(this.snap, Math.ceil((p.x - first.x) / PX * PPQ / this.snap) * this.snap));
            paint();
        };
        const finish = () => { svg.onpointermove = null; svg.onpointerup = null; svg.onpointercancel = null; };
        svg.onpointercancel = () => { finish(); this.draw(); };
        svg.onpointerup = () => {
            finish(); if (old && JSON.stringify(old) === JSON.stringify(next)) { this.draw(); return; }
            this.editor.commit(data => { if (index < 0) data.roll.tracks[this.track].push(next); else data.roll.tracks[this.track][index] = next; }).then(success => {
                if (success) { this.selectNotes([next]); this.draw(); }
            });
        };
    }
    selectNotes(notes) {
        this.selected = new Set(this.data.roll.tracks[this.track].flatMap((note, index) => notes.some(n => n.start === note.start && n.duration === note.duration && n.pitch === note.pitch) ? [index] : []));
    }
    selectionBox(notes, parent) {
        const {start, end} = this.bounds();
        notes = notes.filter(n => n.start < end && n.start + n.duration > start && n.pitch >= this.low && n.pitch < this.low + 36);
        if (!notes.length) return null;
        const x = KEYS + (Math.max(start, Math.min(...notes.map(n => n.start))) - start) / PPQ * this.zoom;
        const right = KEYS + (Math.min(end, Math.max(...notes.map(n => n.start + n.duration))) - start) / PPQ * this.zoom;
        const high = Math.max(...notes.map(n => n.pitch)), low = Math.min(...notes.map(n => n.pitch));
        return shape("rect", {x, y: TOP + (this.low + 35 - high) * ROW, width: right - x, height: (high - low + 1) * ROW,
            fill: "none", stroke: "#fff", "stroke-width": 1.5, "stroke-dasharray": "5 3", "pointer-events": "none"}, parent);
    }
    showSelection() {
        for (const {index, rect} of this.noteRects) {
            rect.setAttribute("stroke", this.selected.has(index) ? "white" : "#16727c");
            rect.setAttribute("stroke-width", this.selected.has(index) ? 2 : 1);
        }
        this.selectionOutline?.remove();
        this.selectionOutline = this.selectionBox([...this.selected].map(i => this.data.roll.tracks[this.track][i]), this.svg);
        this.noteStatus.textContent = this.selected.size ? `${this.selected.size} note${this.selected.size === 1 ? "" : "s"} selected` : "Shift-drag to select notes.";
    }
    boxSelect(event, point) {
        this.pause(); const svg = this.svg, first = point(event), before = new Set(this.selected), {start, end} = this.bounds();
        const marquee = shape("rect", {fill: "#43cfb1", "fill-opacity": 0.15, stroke: "#fff", "stroke-dasharray": "5 3", "pointer-events": "none"}, svg);
        let dragged = false;
        const update = e => {
            const p = point(e); dragged ||= Math.hypot(p.x - first.x, p.y - first.y) > 3;
            if (!dragged) return;
            const left = Math.max(KEYS, Math.min(first.x, p.x)), right = Math.min(Number(svg.getAttribute("width")), Math.max(first.x, p.x));
            const top = Math.max(TOP, Math.min(first.y, p.y)), bottom = Math.min(TOP + 36 * ROW, Math.max(first.y, p.y));
            for (const [name, value] of Object.entries({x: left, y: top, width: Math.max(0, right - left), height: Math.max(0, bottom - top)})) marquee.setAttribute(name, value);
            this.selected = new Set(before);
            this.data.roll.tracks[this.track].forEach((note, index) => {
                if (note.start >= end || note.start + note.duration <= start || note.pitch < this.low || note.pitch >= this.low + 36) return;
                const x = KEYS + (note.start - start) / PPQ * this.zoom, y = TOP + (this.low + 35 - note.pitch) * ROW;
                if (x < right && x + note.duration / PPQ * this.zoom > left && y < bottom && y + ROW > top) this.selected.add(index);
            });
            this.showSelection();
        };
        const finish = () => { svg.onpointermove = null; svg.onpointerup = null; svg.onpointercancel = null; if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId); marquee.remove(); };
        svg.setPointerCapture(event.pointerId); svg.onpointermove = update;
        svg.onpointerup = e => {
            update(e);
            if (!dragged) {
                const hit = event.target.getAttribute("data-note");
                if (hit !== null) { const index = Number(hit); this.selected.has(index) ? this.selected.delete(index) : this.selected.add(index); }
            }
            finish(); this.showSelection();
        };
        svg.onpointercancel = () => { finish(); this.selected = before; this.showSelection(); };
    }
    async commitSelection(notes) {
        const indices = [...this.selected], before = indices.map(i => ({...this.data.roll.tracks[this.track][i]}));
        const success = await this.editor.commit(data => { indices.forEach((index, i) => { data.roll.tracks[this.track][index] = notes[i]; }); });
        this.selectNotes(success ? notes : before); this.draw();
        return success;
    }
    moveSelection(event, point, anchor) {
        const svg = this.svg, first = point(event), indices = [...this.selected], before = indices.map(i => ({...this.data.roll.tracks[this.track][i]}));
        let next = before, preview, heardPitch;
        const minStart = Math.min(...before.map(n => n.start)), maxEnd = Math.max(...before.map(n => n.start + n.duration));
        const minPitch = Math.min(...before.map(n => n.pitch)), maxPitch = Math.max(...before.map(n => n.pitch));
        const update = e => {
            const p = point(e);
            const delta = Math.max(-minStart, Math.min(this.data.roll.total_ticks - maxEnd, Math.round((p.x - first.x) / this.zoom * PPQ / this.snap) * this.snap));
            const pitch = Math.max(-minPitch, Math.min(127 - maxPitch, -Math.round((p.y - first.y) / ROW)));
            next = before.map(n => ({...n, start: n.start + delta, pitch: n.pitch + pitch}));
            preview?.remove(); preview = shape("g", {"pointer-events": "none"}, svg);
            for (const note of next) this.selectionBox([note], preview);
            this.selectionBox(next, preview);
            const heard = next[indices.indexOf(anchor)].pitch;
            if (heard !== heardPitch) { heardPitch = heard; this.audition(heard); }
        };
        const finish = () => { svg.onpointermove = null; svg.onpointerup = null; svg.onpointercancel = null; if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId); preview?.remove(); };
        svg.setPointerCapture(event.pointerId); update(event); svg.onpointermove = update;
        svg.onpointerup = e => { update(e); finish(); if (JSON.stringify(before) !== JSON.stringify(next)) this.commitSelection(next); };
        svg.onpointercancel = () => { finish(); this.draw(); };
    }
    /* ------------------------------------------------------------------ chords
     * Harmony is edited in a modal, not inline.
     *
     * Every chord edit goes through editor.commit(), which re-validates the
     * score server-side and calls render() — rebuilding the whole piano roll.
     * An inline panel inside that tree was therefore destroyed and rebuilt on
     * each change, throwing away the page scroll position, so editing a chord
     * halfway down a long score scrolled you back to the top.
     *
     * The modal lives outside the roll's DOM. It survives a render() untouched,
     * keeps its own scroll, and only refreshes the numbers it needs.
     */

    /**
     * Open the chord editor.
     *
     * `focusBar` (a 0-based bar index within the section) limits the modal to a
     * single bar, which is how the strip above the timeline opens it: a whole
     * section at once was a wall of controls and hard to navigate. Passing
     * nothing shows the whole section. Either way the bar can be changed from
     * inside the modal with ◀ / ▶.
     */
    openHarmony(focusBar = null) {
        if (this.harmonyModal) { this.harmonyFocusBar = focusBar; this.renderHarmonyBody(); this.harmonyModal.focus(); return; }
        this.harmonyFocusBar = focusBar;
        this.harmonyScope = focusBar === null ? "section" : "bar";
        const overlay = el("div");
        overlay.className = "fl-yue2-modal-overlay";
        const modal = el("div", null, overlay);
        modal.className = "fl-yue2-modal";
        modal.tabIndex = -1;
        this.harmonyModal = modal;

        const header = el("div", null, modal);
        header.className = "modal-header";
        this.harmonyTitle = el("strong", "Chords", header);
        this.harmonySummary = el("span", null, header);
        this.harmonySummary.className = "modal-summary";

        // Bar navigation: step through bars without leaving the modal.
        const nav = el("div", null, header);
        nav.className = "modal-nav";
        this.harmonyPrev = button(nav, "◀", () => this.stepHarmonyBar(-1), true);
        this.harmonyBarLabel = el("span", "", nav);
        this.harmonyBarLabel.className = "modal-bar-label";
        this.harmonyNext = button(nav, "▶", () => this.stepHarmonyBar(1), true);
        this.harmonyScopeButton = button(nav, "Whole section", () => this.toggleHarmonyScope(), true);

        const close = button(header, "Close", () => this.closeHarmony(), true);
        close.className = "modal-close";

        this.harmonyBody = el("div", null, modal);
        this.harmonyBody.className = "modal-body";

        const footer = el("div", null, modal);
        footer.className = "modal-footer";
        el("span", "Click a chord name to hear it. Edits save as you make them.", footer).className = "modal-hint";
        // Contextual clear: it removes what you are actually looking at — the
        // focused bar, or the section in whole-section view. Naming the scope
        // on the button avoids the surprise of wiping the whole score.
        this.harmonyClearButton = button(footer, "Clear bar", () => this.clearHarmonyScope(), true);

        // Click the backdrop or press Escape to dismiss.
        overlay.onpointerdown = event => { if (event.target === overlay) this.closeHarmony(); };
        modal.onkeydown = event => {
            if (event.key === "Escape") { event.stopPropagation(); this.closeHarmony(); }
            if (event.key === "ArrowLeft") { event.stopPropagation(); this.stepHarmonyBar(-1); }
            if (event.key === "ArrowRight") { event.stopPropagation(); this.stepHarmonyBar(1); }
            // Keep typing inside the modal from triggering roll shortcuts.
            event.stopPropagation();
        };

        this.editor.root.append(overlay);
        this.renderHarmonyBody();
        modal.focus();
    }

    /** Move the modal one bar left or right within the current section. */
    stepHarmonyBar(delta) {
        const {section} = this.bounds();
        const current = this.harmonyFocusBar ?? 0;
        this.harmonyFocusBar = Math.max(0, Math.min(section.bars - 1, current + delta));
        if (this.harmonyScope === "section") this.harmonyScope = "bar";
        this.renderHarmonyBody();
    }

    /** Switch between editing one bar and the whole section. */
    toggleHarmonyScope() {
        this.harmonyScope = this.harmonyScope === "bar" ? "section" : "bar";
        if (this.harmonyScope === "bar" && this.harmonyFocusBar === null) this.harmonyFocusBar = 0;
        this.renderHarmonyBody();
    }

    /**
     * Clear the chords of whatever the modal is currently showing.
     *
     * In bar view that is the focused bar only; in section view it is every bar
     * of the current section. The whole score is never cleared from here — for
     * that, each section can be cleared in turn.
     */
    clearHarmonyScope() {
        const {start, section} = this.bounds();
        const barTicks = this.data.roll.bar_ticks;
        if (this.harmonyScope === "bar") {
            const bar = Math.max(0, Math.min(section.bars - 1, this.harmonyFocusBar ?? 0));
            const barStart = start + bar * barTicks, barEnd = barStart + barTicks;
            return this.editor.commit(data => {
                data.roll.chords = data.roll.chords.filter(c => c.start < barStart || c.start >= barEnd);
            }).then(() => { this.renderHarmonyBody(); return true; });
        }
        const sectionStart = start, sectionEnd = start + section.bars * barTicks;
        return this.editor.commit(data => {
            data.roll.chords = data.roll.chords.filter(c => c.start < sectionStart || c.start >= sectionEnd);
        }).then(() => { this.renderHarmonyBody(); return true; });
    }

    closeHarmony() {
        // Restore the roll's own scroll after any re-render the edits caused.
        const keep = {left: this.scroll?.scrollLeft || 0, top: this.scroll?.scrollTop || 0};
        this.harmonyModal?.parentElement?.remove();
        this.harmonyModal = null;
        this.harmonyBody = null;
        this.harmonySummary = null;
        this.harmonyFocusBar = null;
        if (this.scroll) { this.scroll.scrollLeft = keep.left; this.scroll.scrollTop = keep.top; }
        this.editor.root.focus();
    }

    /** Rebuild the modal's list from current data, preserving its scroll. */
    renderHarmonyBody() {
        const body = this.harmonyBody;
        if (!body) return;
        const keep = body.scrollTop;
        body.replaceChildren();

        const {start, section} = this.bounds();
        const barTicks = this.data.roll.bar_ticks;
        const beats = beatsPerBar(this.data.meter);
        const slotsPerBar = Math.max(1, Math.ceil(beats - 1e-9));
        const beatTicks = barTicks / beats;
        const roots = ["C", "C#", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];

        // Which bars to show: just the focused one, or the whole section.
        const single = this.harmonyScope === "bar";
        const first = single ? Math.max(0, Math.min(section.bars - 1, this.harmonyFocusBar ?? 0)) : 0;
        const last = single ? first : section.bars - 1;
        if (single) this.harmonyFocusBar = first;

        this.harmonyTitle.textContent = single ? `Bar ${section.start + first + 1}` : "Chord progression";
        this.harmonySummary.textContent = `Section ${section.name} · ${this.data.meter} · ${slotsPerBar} beat${slotsPerBar === 1 ? "" : "s"}`;
        if (this.harmonyBarLabel) {
            this.harmonyBarLabel.textContent = `${section.start + first + 1} / ${section.start + section.bars}`;
            this.harmonyPrev.disabled = first === 0;
            this.harmonyNext.disabled = first === section.bars - 1;
            this.harmonyScopeButton.textContent = single ? "Whole section" : "One bar";
            this.harmonyBarLabel.hidden = !single;
            this.harmonyPrev.hidden = !single;
            this.harmonyNext.hidden = !single;
            // Say exactly what will be cleared, and disable when there is
            // nothing to clear so the button never looks destructive for nothing.
            const hasChords = single
                ? this.data.roll.chords.some(c => c.start >= start + first * barTicks && c.start < start + (first + 1) * barTicks)
                : this.data.roll.chords.some(c => c.start >= start && c.start < start + section.bars * barTicks);
            if (this.harmonyClearButton) {
                this.harmonyClearButton.textContent = single
                    ? `Clear bar ${section.start + first + 1}`
                    : `Clear section “${section.name}”`;
                this.harmonyClearButton.disabled = !hasChords;
                this.harmonyClearButton.title = single
                    ? `Remove every chord in bar ${section.start + first + 1}`
                    : `Remove every chord in the ${section.name} section`;
            }
        }

        // One commit per edit, and only the modal is rebuilt afterwards.
        const setAt = (tick, symbol) => this.editor.commit(data => {
            data.roll.chords = data.roll.chords.filter(c => c.start !== tick);
            if (symbol && symbol !== "—") data.roll.chords.push({start: tick, symbol});
        }).then(() => { this.renderHarmonyBody(); return true; });

        const moveFrom = (from, target, symbol) => this.editor.commit(data => {
            data.roll.chords = data.roll.chords.filter(c => c.start !== from && c.start !== target);
            data.roll.chords.push({start: target, symbol});
        }).then(() => { this.renderHarmonyBody(); return true; });

        const table = el("div", null, body);
        table.className = "chord-table";

        for (let bar = first; bar <= last; bar++) {
            const barStart = start + bar * barTicks, barEnd = barStart + barTicks;
            const inBar = this.data.roll.chords
                .filter(chord => chord.start >= barStart && chord.start < barEnd)
                .sort((a, b) => a.start - b.start);

            const barRow = el("div", null, table);
            barRow.className = "chord-bar" + (single ? " single" : "");
            const label = el("div", `Bar ${section.start + bar + 1}`, barRow);
            label.className = "chord-bar-label";

            const beatsRow = el("div", null, barRow);
            beatsRow.className = "chord-beats";

            for (let beat = 1; beat <= slotsPerBar; beat++) {
                const at = inBar.find(c => Math.round((c.start - barStart) / beatTicks + 1) === beat);
                beatsRow.append(this.chordBeatCell(barStart, beat, beatTicks, at, setAt, moveFrom, roots, slotsPerBar));
            }
        }
        body.scrollTop = single ? 0 : keep;
    }

    /** One beat cell: beat number, chord name, root/type pickers, clear. */
    chordBeatCell(barStart, beat, beatTicks, at, setAt, moveFrom, roots, slotsPerBar) {
        const tick = barStart + Math.round((beat - 1) * beatTicks);
        const cell = el("div", null, null);
        cell.className = "chord-cell" + (at ? " filled" : "");

        const top = el("div", null, cell);
        top.className = "chord-cell-top";
        const beatTag = el("span", `beat ${beat}`, top);
        beatTag.className = "chord-beat";

        const name = el("button", at?.symbol || "+", top);
        name.type = "button";
        name.className = "chord-name" + (at ? "" : " empty");
        name.title = at ? "Click to hear this chord" : "No chord on this beat — pick a root below";
        // When empty, clicking picks a sensible default so a chord can be added
        // without touching two dropdowns.
        name.onclick = () => at ? this.auditionChord(at.symbol) : setAt(tick, "C");

        const match = at?.symbol.match(/^([A-G][#b]?)(.*)$/);
        const picks = el("div", null, cell);
        picks.className = "chord-cell-picks";

        const rootSelect = combo(picks, "", [...new Set(["—", ...roots, ...(match ? [match[1]] : [])])], match?.[1] || "—",
            value => setAt(tick, value === "—" ? null : value + quality.value));
        rootSelect.title = "Chord root";
        const qualities = [...new Set([...this.data.qualities, ...(match ? [match[2]] : [])])];
        const quality = combo(picks, "", qualities.map(q => [q, q || "Major"]), match?.[2] || "",
            value => { if (rootSelect.value !== "—") setAt(tick, rootSelect.value + value); });
        quality.title = "Chord quality";

        // Moving this chord to another beat of the same bar, in one commit.
        const beatSelect = combo(picks, "", Array.from({length: slotsPerBar}, (_, i) => [i + 1, `→ ${i + 1}`]), beat,
            value => {
                const target = barStart + Math.round((Number(value) - 1) * beatTicks);
                if (!at) { setAt(target, "C"); return; }
                if (target === tick) return;
                moveFrom(tick, target, at.symbol);
            });
        beatSelect.title = "Move this chord to another beat";

        const remove = el("button", "−", picks);
        remove.type = "button";
        remove.className = "chord-remove";
        remove.title = at ? "Remove this chord" : "Nothing to remove";
        remove.disabled = !at;
        remove.onclick = () => setAt(tick, null);

        return cell;
    }
    deleteNote() { if (this.selected.size) this.editor.commit(data => { data.roll.tracks[this.track] = data.roll.tracks[this.track].filter((_, i) => !this.selected.has(i)); }); }
    async nudge(key, octave) {
        if (!this.selected.size || this.editor.busy) return;
        const next = [...this.selected].map(index => {
            const note = {...this.data.roll.tracks[this.track][index]};
            if (key === "ArrowUp" || key === "ArrowDown") note.pitch += (key === "ArrowUp" ? 1 : -1) * (octave ? 12 : 1);
            else note.start += (key === "ArrowRight" ? 1 : -1) * this.snap;
            return note;
        });
        if (next.some(n => n.pitch < 0 || n.pitch > 127 || n.start < 0 || n.start + n.duration > this.data.roll.total_ticks)) return;
        if (await this.commitSelection(next)) this.audition(next[0].pitch);
    }
    fit() {
        const {start, end} = this.bounds();
        const notes = this.data.roll.tracks[this.track].filter(n => n.start < end && n.start + n.duration > start);
        if (notes.length) this.low = Math.max(0, Math.min(92, Math.floor((Math.min(...notes.map(n => n.pitch)) + Math.max(...notes.map(n => n.pitch)) - 35) / 2)));
        this.render(); this.scroll.scrollLeft = 0; this.scroll.scrollTop = 0; this.scroll.onscroll();
    }
    async audition(pitch) {
        if (pitch < 0 || pitch > 127) return;
        clearTimeout(this.auditionTimer);
        const audio = this.auditionAudio ||= new AudioContext(), version = this.auditionVersion = (this.auditionVersion || 0) + 1;
        await audio.resume(); if (this.auditionAudio !== audio || version !== this.auditionVersion) return;
        this.auditionVoice?.stop();
        this.auditionVoice = this.tone(audio, pitch, audio.currentTime, audio.currentTime + 0.25);
        this.auditionTimer = setTimeout(() => this.stopAudition(), 450);
    }
    stopAudition() {
        clearTimeout(this.auditionTimer); this.auditionAudio?.close(); this.auditionAudio = null; this.auditionVoice = null; this.auditionVoices = null;
    }
    /** Play a chord's voicing on its own, for checking a symbol by ear. */
    async auditionChord(symbol) {
        const pitches = chordVoicing(symbol, 40);
        if (!pitches.length) return;
        clearTimeout(this.auditionTimer);
        const audio = this.auditionAudio ||= new AudioContext(), version = this.auditionVersion = (this.auditionVersion || 0) + 1;
        await audio.resume(); if (this.auditionAudio !== audio || version !== this.auditionVersion) return;
        this.auditionVoice?.stop();
        for (const voice of this.auditionVoices || []) voice.stop();
        const at = audio.currentTime;
        // A little longer than a single note, so the chord is recognisable.
        this.auditionVoices = pitches.map(pitch => this.tone(audio, pitch, at, at + 0.85, 0.09, "sawtooth"));
        this.auditionVoice = this.auditionVoices[0];
        this.auditionTimer = setTimeout(() => this.stopAudition(), 1100);
    }
    tone(audio, pitch, on, off, volume = 0.12, type = "triangle") {
        const oscillator = audio.createOscillator(), gain = audio.createGain();
        oscillator.type = type; oscillator.frequency.value = 440 * 2 ** ((pitch - 69) / 12);
        gain.gain.setValueAtTime(0, on); gain.gain.linearRampToValueAtTime(volume, on + Math.min(0.005, (off - on) / 2)); gain.gain.setTargetAtTime(0.001, off, 0.02);
        oscillator.connect(gain); gain.connect(audio.destination); oscillator.start(on); oscillator.stop(off + 0.15);
        return oscillator;
    }
    shift(data, at, delta) {
        for (const notes of Object.values(data.roll.tracks)) for (const note of notes) if (note.start >= at) note.start += delta;
        for (const chord of data.roll.chords) if (chord.start >= at) chord.start += delta;
    }
    checkBoundary(data, tick) {
        if (Object.values(data.roll.tracks).flat().some(n => n.start < tick && n.start + n.duration > tick)) throw new Error("A sustained note crosses this section boundary. Shorten it before changing sections.");
    }
    addBar() {
        const {end} = this.bounds(); this.editor.commit(data => { this.checkBoundary(data, end); this.shift(data, end, data.roll.bar_ticks); data.roll.sections[this.section].bars++; });
    }
    duplicateSection() {
        const {start, end, section} = this.bounds();
        this.editor.commit(data => {
            this.checkBoundary(data, start); this.checkBoundary(data, end);
            const copies = Object.fromEntries(Object.entries(data.roll.tracks).map(([name, notes]) => [name, notes.filter(n => n.start >= start && n.start < end).map(n => ({...n, start: n.start + end - start}))]));
            const chords = data.roll.chords.filter(c => c.start >= start && c.start < end).map(c => ({...c, start: c.start + end - start}));
            this.shift(data, end, end - start);
            for (const name of ["Vocal", "Ins"]) data.roll.tracks[name].push(...copies[name]);
            data.roll.chords.push(...chords); data.roll.sections.splice(this.section + 1, 0, {...section});
        });
    }
    removeSection() {
        const {start, end} = this.bounds();
        this.editor.commit(data => {
            this.checkBoundary(data, start); this.checkBoundary(data, end);
            for (const name of ["Vocal", "Ins"]) data.roll.tracks[name] = data.roll.tracks[name].filter(n => n.start < start || n.start >= end);
            data.roll.chords = data.roll.chords.filter(c => c.start < start || c.start >= end);
            this.shift(data, end, start - end); data.roll.sections.splice(this.section, 1);
        });
    }
    moveSection(delta) {
        const current = this.section, other = current + delta;
        if (other < 0 || other >= this.data.roll.sections.length) return;
        this.editor.commit(data => {
            const sections = data.roll.sections, ranges = sections.map(s => ({start: s.start * data.roll.bar_ticks, end: (s.start + s.bars) * data.roll.bar_ticks}));
            const order = sections.map((_, i) => i); [order[current], order[other]] = [order[other], order[current]];
            let cursor = 0; const shifts = {};
            for (const i of order) { shifts[i] = cursor - ranges[i].start; cursor += ranges[i].end - ranges[i].start; }
            for (const notes of Object.values(data.roll.tracks)) for (const note of notes) {
                const i = ranges.findIndex(r => note.start >= r.start && note.start < r.end);
                if (note.start + note.duration > ranges[i].end) throw new Error("Shorten notes crossing section boundaries before reordering.");
                note.start += shifts[i];
            }
            for (const chord of data.roll.chords) { const i = ranges.findIndex(r => chord.start >= r.start && chord.start < r.end); chord.start += shifts[i]; }
            data.roll.sections = order.map(i => sections[i]);
        });
    }
    updateCursor() {
        if (!this.cursor || !this.data?.grid_available) return;
        const {start, end} = this.bounds();
        const x = KEYS + (Math.max(start, Math.min(end, this.position)) - start) / PPQ * this.zoom;
        this.cursor.setAttribute("x1", x); this.cursor.setAttribute("x2", x); this.cursorHandle.setAttribute("x", x - 5);
    }
    scrub(event, point) {
        const svg = this.svg, {start, end} = this.bounds(), playing = this.playing;
        this.pause(); const before = this.position;
        const seek = e => { this.position = Math.max(start, Math.min(end, start + (point(e).x - KEYS) / this.zoom * PPQ)); this.updateCursor(); };
        const finish = () => {
            svg.onpointermove = null; svg.onpointerup = null; svg.onpointercancel = null;
            if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
            if (playing) this.play();
        };
        svg.setPointerCapture(event.pointerId); seek(event);
        svg.onpointermove = seek; svg.onpointerup = e => { seek(e); finish(); };
        svg.onpointercancel = () => { this.position = before; this.updateCursor(); finish(); };
    }
    togglePlayback() { if (!this.data?.grid_available) return; return this.playing ? this.pause() : this.play(); }
    async play() {
        this.pause(); this.stopAudition(); const {start, end} = this.bounds();
        if (this.position < start || this.position >= end) this.position = start;
        const audio = new AudioContext(); this.audio = audio; this.playing = true;
        this.playButton.textContent = "Pause";
        await audio.resume(); if (this.audio !== audio) return;
        const now = audio.currentTime + 0.04, seconds = 60 / this.data.bpm / PPQ, from = this.position;
        this.startedAt = now; this.startedFrom = from; this.secondsPerTick = seconds;
        for (const note of this.data.roll.tracks[this.track]) {
            const a = Math.max(from, note.start), b = Math.min(end, note.start + note.duration); if (b <= a) continue;
            const on = now + (a - from) * seconds, off = now + (b - from) * seconds;
            this.tone(audio, note.pitch, on, off);
        }
        // Harmony: each chord sounds from its start until the next chord (or the
        // end of the section). Voiced low and played softer than the melody so it
        // supports rather than masks it.
        //
        // The chords play regardless of which voice is displayed. They live in
        // the Vocal voice in native YuE2 ABC, but tying playback to `this.track`
        // meant the intro — which has almost no vocal notes, so you are looking
        // at Instrumental — appeared to have no harmony at all. The accompaniment
        // belongs to the song, not to whichever lane happens to be on screen.
        if (this.harmony && this.data.roll.chords.length) {
            const sectionChords = this.data.roll.chords
                .filter(chord => chord.start >= start && chord.start < end)
                .sort((a, b) => a.start - b.start);
            sectionChords.forEach((chord, index) => {
                const a = Math.max(from, chord.start);
                const next = sectionChords[index + 1];
                const b = Math.min(end, next ? next.start : end);
                if (b <= a) return;
                const on = now + (a - from) * seconds, off = now + (b - from) * seconds;
                for (const pitch of chordVoicing(chord.symbol, 40)) {
                    this.tone(audio, pitch, on, off, 0.075, "sawtooth");
                }
            });
        }
        if (this.metronome) {
            const beat = PPQ * 4 / Number(this.data.meter.split("/")[1]);
            for (let tick = Math.ceil(from / beat) * beat; tick < end; tick += beat) {
                const on = now + (tick - from) * seconds;
                this.tone(audio, tick % this.data.roll.bar_ticks === 0 ? 96 : 84, on, on + 0.015, 0.04, "square");
            }
        }
        const animate = () => {
            if (this.audio !== audio) return;
            this.position = Math.min(end, from + Math.max(0, audio.currentTime - now) / seconds);
            this.updateCursor();
            if (this.position >= end) { this.pause(); return; }
            this.animation = requestAnimationFrame(animate);
        };
        this.animation = requestAnimationFrame(animate);
    }
    pause() {
        if (this.audio) {
            if (this.startedAt !== undefined) this.position = Math.min(this.bounds().end, this.startedFrom + Math.max(0, this.audio.currentTime - this.startedAt) / this.secondsPerTick);
            this.audio.close(); this.audio = null;
        }
        this.startedAt = undefined; this.playing = false; cancelAnimationFrame(this.animation);
        if (this.playButton) this.playButton.textContent = "Play";
        this.updateCursor();
    }
    stop() { this.pause(); this.stopAudition(); this.position = this.data?.grid_available ? this.bounds().start : 0; this.updateCursor(); }
}

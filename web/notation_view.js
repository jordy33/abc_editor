/*
 * Sheet-music ("Partitura") view over the vendored abcjs renderer.
 *
 * Renders `editor.source` as engraved staves — including scores with mid-song
 * meter changes, which the piano roll cannot display — and turns clicks into
 * semantic selections via `abc_edit.js`. Edits are surgical text splices that
 * keep every bar's duration intact; each one round-trips the server validator
 * through the editor's normal write()/validate()/accept() pipeline, so undo,
 * localStorage persistence and the status line all behave as usual.
 *
 * Rendering never modifies the score. Only the toolbar/keyboard operations
 * write, and every write is validated before it sticks.
 */

import { el, button } from "./piano_roll.js";
import * as AbcEdit from "./abc_edit.js";
import { parseLyricSections, buildLyricsDisplay, makeOffsetMap } from "./lyrics.js";

const LYRICS_KEY = "fl-yue2-abc-editor/lyrics";

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

function pitchName(pitch) {
    return NOTE_NAMES[pitch % 12] + (Math.floor(pitch / 12) - 1);
}

// Duration ladder for the +/- stepper: the dialect's atoms plus tied extents.
const LADDER = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256];

export class NotationView {
    constructor(editor, container) {
        this.editor = editor;
        this.container = container;
        this.container.classList.add("fl-yue2-notation");
        this.selection = null;      // {voice, barIdx, off} — stable across renders
        this.model = null;
        this.applying = false;
        this.visual = null;

        this.toolbar = el("div", null, container);
        this.toolbar.className = "row notation-toolbar";
        this.info = el("span", "Click a note to select it.", this.toolbar);
        this.info.className = "notation-info";

        this.noteButtons = [];
        const noteBtn = (label, action, title) => {
            const b = button(this.toolbar, label, action, true);
            if (title) b.title = title;
            this.noteButtons.push(b);
            return b;
        };
        noteBtn("−", () => this.stepDuration(-1), "Shorten (frees time as rest)");
        noteBtn("+", () => this.stepDuration(1), "Lengthen into the following rest");
        this.namedDurations = el("span", null, this.toolbar);
        this.namedDurations.className = "notation-durations";
        noteBtn("▲", () => this.applyEdit((m, r) => AbcEdit.changePitch(m, r, 1)), "Pitch up a semitone (Shift+↑: octave)");
        noteBtn("▼", () => this.applyEdit((m, r) => AbcEdit.changePitch(m, r, -1)), "Pitch down a semitone (Shift+↓: octave)");
        this.restButton = noteBtn("Rest", () => this.toggleRest(), "Turn the note into a rest (Delete) or back into a note");
        this.mergeButton = noteBtn("Merge", () => this.applyEdit((m, r) => AbcEdit.mergeWithNext(m, r)),
                                   "Tie this note into the next note of the same pitch");
        this.updateToolbar();

        // Lyrics (display-only; the .abc never carries them).
        this.showLyrics = false;
        this.showSyllables = false;
        this.renderMap = null;
        this.lyricsText = null;
        try { this.lyricsText = localStorage.getItem(LYRICS_KEY); } catch { /* private mode */ }
        this.lyricsFile = el("input", null, container);
        this.lyricsFile.type = "file";
        this.lyricsFile.accept = ".txt";
        this.lyricsFile.hidden = true;
        this.lyricsFile.onchange = async () => {
            const file = this.lyricsFile.files[0];
            this.lyricsFile.value = "";
            if (!file) return;
            if (file.size > 200000) { this.editor.status("Maximum lyrics file size is 200 KB.", true); return; }
            this.lyricsText = await file.text();
            try { localStorage.setItem(LYRICS_KEY, this.lyricsText); } catch { /* private mode */ }
            const count = parseLyricSections(this.lyricsText).length;
            this.editor.status(`Lyrics loaded: ${count} tagged sections from ${file.name}.`);
            this.showLyrics = true;
            this.lyricsButton.classList.add("nv-on");
            this.render();
        };
        const importLyrics = button(this.toolbar, "Import lyrics", () => this.lyricsFile.click(), true);
        importLyrics.title = "Load the [Intro]/[Verse]… .txt that accompanies this song (display only)";
        this.lyricsButton = button(this.toolbar, "Letra", () => {
            this.showLyrics = !this.showLyrics;
            this.lyricsButton.classList.toggle("nv-on", this.showLyrics);
            if (this.showLyrics && !this.lyricsText) { this.editor.status("Import the lyrics .txt first.", true); }
            this.placeSectionLabels();
        }, true);
        this.lyricsButton.title = "Show each section's lyric text on the score";
        this.syllablesButton = button(this.toolbar, "Sílabas", () => {
            this.showSyllables = !this.showSyllables;
            this.syllablesButton.classList.toggle("nv-on", this.showSyllables);
            if (this.showSyllables && !this.lyricsText) { this.editor.status("Import the lyrics .txt first.", true); }
            this.render();
        }, true);
        this.syllablesButton.title = "Experimental: distribute syllables under the Vocal notes. "
            + "The alignment is a visual guess — YuE2 decides the real phrasing.";

        // Soundfont playback (General MIDI piano, vendored — works offline and
        // honors mid-song meter changes, unlike the piano roll's player).
        this.audio = el("div", null, container);
        this.audio.className = "notation-audio";
        this.synthControl = null;
        this.initAudio();

        this.paper = el("div", null, container);
        this.paper.className = "notation-paper";

        // Re-engrave when the pane's width changes (there is no responsive
        // mode in play; see render()), otherwise just re-glue the labels.
        this.labelObserver = new ResizeObserver(() => {
            cancelAnimationFrame(this.labelFrame);
            this.labelFrame = requestAnimationFrame(() => {
                if (this.visual && typeof this.renderedWidth === "number"
                        && Math.abs(this.paper.clientWidth - this.renderedWidth) > 2) {
                    this.render();
                } else {
                    this.placeSectionLabels();
                }
            });
        });
        this.labelObserver.observe(this.paper);
    }

    initAudio() {
        if (!window.ABCJS.synth || !window.ABCJS.synth.supportsAudio()) {
            this.audio.hidden = true;
            return;
        }
        this.synthControl = new window.ABCJS.synth.SynthController();
        const cursor = {
            beatSubdivisions: 1,
            onStart: () => { this.clearPlayingCursor(); this.cursorLineNum = null; this.cursorX = null; this.cursorMs = null; },
            onFinished: () => { this.clearPlayingCursor(); this.removeCursorLine(); },
            onEvent: (ev) => {
                this.clearPlayingCursor();
                for (const group of ev.elements || []) {
                    for (const g of group) g.classList.add("nv-playing");
                }
                this.moveCursorLine(ev);
            },
        };
        this.synthControl.load(this.audio, cursor, {
            displayPlay: true, displayProgress: true, displayRestart: true,
            displayLoop: true, displayWarp: true,
        });
    }

    clearPlayingCursor() {
        for (const g of this.paper.querySelectorAll(".nv-playing")) g.classList.remove("nv-playing");
    }

    /* MuseScore-style vertical playback cursor, drawn into the score's SVG.
     *
     * Timing events fire per sounding element in EITHER voice, so the raw
     * ev.top/ev.left hop between the Vocal and Ins staves and can even step
     * backwards when the other voice's notehead sits a little to the left.
     * Instead: span the whole system (both staves of the current abcjs-lN
     * line) vertically, and never let x move backwards within one system. */
    moveCursorLine(ev) {
        const svg = this.paper.querySelector("svg");
        if (!svg) return;

        // Only sounding NOTES drag the cursor. A whole-bar rest in the other
        // voice is engraved at a fixed x near its measure start and would
        // yank the cursor backwards mid-bar.
        const noteNodes = (ev.elements || []).flat()
            .filter(node => /(?:^|\s)abcjs-note(?:\s|$)/.test(node.getAttribute("class") || ""));
        if (!noteNodes.length) return;

        // Which system is sounding? Read the abcjs-lN class off the notes.
        let lineNum = null;
        for (const node of noteNodes) {
            const m = /(?:^|\s)abcjs-l(\d+)(?:\s|$)/.exec(node.getAttribute("class") || "");
            if (m) { lineNum = parseInt(m[1], 10); break; }
        }

        let x = Infinity;
        for (const node of noteNodes) {
            try { x = Math.min(x, node.getBBox().x); } catch { /* detached */ }
        }
        if (!Number.isFinite(x)) {
            if (typeof ev.left !== "number") return;
            x = ev.left;
        }
        x -= 2;

        // Within one system, time moving forward means x may never step back
        // (the two voices' noteheads differ slightly at the same beat). Time
        // stepping back means the user sought backwards: accept the new x.
        const sought = typeof this.cursorMs === "number"
            && typeof ev.milliseconds === "number" && ev.milliseconds < this.cursorMs;
        if (!sought && lineNum !== null && lineNum === this.cursorLineNum
                && typeof this.cursorX === "number" && x < this.cursorX) {
            x = this.cursorX;
        }
        this.cursorMs = typeof ev.milliseconds === "number" ? ev.milliseconds : this.cursorMs;
        this.cursorLineNum = lineNum;
        this.cursorX = x;

        const extent = this.lineExtent(svg, lineNum);
        const y1 = extent ? extent.top : ev.top;
        const y2 = extent ? extent.bottom : ev.top + (ev.height || 30);
        if (typeof y1 !== "number" || typeof y2 !== "number") return;

        let line = svg.querySelector(".nv-cursor");
        if (!line) {
            line = document.createElementNS("http://www.w3.org/2000/svg", "line");
            line.setAttribute("class", "nv-cursor");
            svg.appendChild(line);
        }
        line.setAttribute("x1", x);
        line.setAttribute("x2", x);
        line.setAttribute("y1", y1);
        line.setAttribute("y2", y2);
        // Keep the cursor in view while the song scrolls past.
        const rect = line.getBoundingClientRect();
        const paper = this.paper.getBoundingClientRect();
        if (rect.top < paper.top + 8 || rect.bottom > paper.bottom - 8) {
            this.paper.scrollTop += rect.top - paper.top - 60;
        }
    }

    /* Full vertical extent of one engraved system: the union of its staves
     * (both voices), cached per render. */
    lineExtent(svg, lineNum) {
        if (lineNum === null) return null;
        if (!this.lineExtents) this.lineExtents = new Map();
        if (this.lineExtents.has(lineNum)) return this.lineExtents.get(lineNum);
        let top = Infinity, bottom = -Infinity;
        for (const staff of svg.querySelectorAll(`.abcjs-staff.abcjs-l${lineNum}`)) {
            try {
                const box = staff.getBBox();
                top = Math.min(top, box.y);
                bottom = Math.max(bottom, box.y + box.height);
            } catch { /* detached */ }
        }
        const extent = top < bottom ? { top: top - 8, bottom: bottom + 8 } : null;
        this.lineExtents.set(lineNum, extent);
        return extent;
    }

    removeCursorLine() {
        for (const line of this.paper.querySelectorAll(".nv-cursor")) line.remove();
        this.cursorLineNum = null;
        this.cursorX = null;
        this.cursorMs = null;
    }

    setAudioTune() {
        if (!this.synthControl || !this.visual) return;
        this.synthControl.setTune(this.visual, false, {
            soundFontUrl: "vendor/soundfont/",
            soundFontVolumeMultiplier: 3,
            program: 0,
            chordsOff: false,
        }).catch(error => console.warn("audio:", error));
        // abcjs quirk: setTune(…, false) stores the new tune but leaves
        // isLoaded=true, so the next Play would resume the PREVIOUS tune's
        // audio and cursor timer (whose SVG nodes are already detached).
        // Invalidate so Play re-primes from the tune just rendered.
        this.synthControl.isLoaded = false;
    }

    /* ------------------------------------------------------------- render */

    render() {
        this.model = null;
        const scrollTop = this.paper.scrollTop;
        let source = this.editor.source;
        this.renderMap = null;
        if (this.showSyllables && this.lyricsText) {
            try {
                const built = buildLyricsDisplay(
                    this.getModel(),
                    (this.editor.data && this.editor.data.roll && this.editor.data.roll.sections) || [],
                    parseLyricSections(this.lyricsText));
                source = built.text;
                this.renderMap = makeOffsetMap(built.insertions);
                if (built.warnings.length) console.info("lyrics fit:", built.warnings);
            } catch (error) {
                console.warn("lyrics:", error);
            }
        }
        // NOTE: abcjs' responsive:"resize" mode sets inline overflow:hidden on
        // the container and absolutely positions the svg, which kills natural
        // trackpad scrolling. Render at an explicit width instead and
        // re-render when the pane's width actually changes.
        this.renderedWidth = this.paper.clientWidth;
        const result = window.ABCJS.renderAbc(this.paper, source, {
            add_classes: true,
            selectTypes: ["note"],
            staffwidth: Math.max(400, this.renderedWidth - 60),
            clickListener: (abcElem) => this.onClick(abcElem),
            selectionColor: "currentColor",
        });
        this.visual = result && result[0];
        // abcjs sizes the container itself (inline overflow:hidden + height),
        // which would kill the pane's own scrolling; our stylesheet rules.
        this.paper.style.overflow = "";
        this.paper.style.height = "";
        this.lineExtents = null;  // system geometry changed; recompute lazily
        this.buildNamedDurations();
        this.restoreSelection();
        this.placeSectionLabels();
        this.setAudioTune();
        this.paper.scrollTop = scrollTop;
    }

    /* Overlay the `% section` names (intro, verse, …) above their first bar.
     * Labels are plain DOM chips positioned over the engraving, so the ABC
     * source — and every char offset the edit engine relies on — is untouched. */
    placeSectionLabels() {
        for (const old of this.paper.querySelectorAll(".nv-section-label, .nv-lyric-block")) old.remove();
        const sections = this.editor.data && this.editor.data.roll && this.editor.data.roll.sections;
        if (!sections || !sections.length) return;
        let model;
        try { model = this.getModel(); } catch { return; }
        const lyricSections = this.showLyrics && this.lyricsText ? parseLyricSections(this.lyricsText) : null;
        const paperRect = this.paper.getBoundingClientRect();
        sections.forEach((section, index) => {
            const target = this.elementForBar(model, section.start);
            const g = target && target.abselem && target.abselem.elemset && target.abselem.elemset[0];
            if (!g) return;
            const rect = g.getBoundingClientRect();
            const left = Math.max(0, rect.left - paperRect.left + this.paper.scrollLeft - 4);
            const top = Math.max(0, rect.top - paperRect.top + this.paper.scrollTop - 30);
            const label = el("div", section.name, this.paper);
            label.className = "nv-section-label";
            label.style.left = `${left}px`;
            label.style.top = `${top}px`;
            const lyric = lyricSections && lyricSections[index];
            if (lyric && lyric.lines.length) {
                const block = el("div", lyric.lines.join("\n"), this.paper);
                block.className = "nv-lyric-block";
                block.style.left = `${left}px`;
                block.style.top = `${top + 22}px`;
            }
        });
    }

    elementForBar(model, barIdx) {
        for (const name of ["Vocal", "Ins"]) {
            const bar = model.bars[name][barIdx];
            if (!bar) continue;
            for (const elem of this.engravedElements()) {
                const s = this.sourceChar(elem.startChar);
                if (s !== null && s >= bar.charStart && s < bar.charEnd) return elem;
            }
        }
        return null;
    }

    getModel() {
        if (!this.model || this.model.source !== this.editor.source) {
            this.model = AbcEdit.analyze(this.editor.source);
        }
        return this.model;
    }

    buildNamedDurations() {
        this.namedDurations.textContent = "";
        let denom = 32;
        try { denom = this.getModel().denom; } catch { /* leave default */ }
        const named = [["16th", denom / 16], ["8th", denom / 8], ["1/4", denom / 4], ["1/2", denom / 2]];
        for (const [label, units] of named) {
            if (!Number.isInteger(units) || units < 1) continue;
            const b = button(this.namedDurations, label, () =>
                this.applyEdit((m, r) => AbcEdit.setDuration(m, r, units)), true);
            b.title = `Set the selected note to a ${label} note (${units} units)`;
            this.noteButtons.push(b);
        }
        this.updateToolbar();
    }

    stop() {
        if (this.synthControl) { try { this.synthControl.pause(); } catch { /* not started */ } }
        this.clearPlayingCursor();
        this.removeCursorLine();
    }

    /* ---------------------------------------------------------- selection */

    /* Rendered-text char offset -> true source offset (identity without lyrics). */
    sourceChar(renderChar) {
        if (typeof renderChar !== "number") return null;
        return this.renderMap ? this.renderMap.toSource(renderChar) : renderChar;
    }

    onClick(abcElem) {
        if (!abcElem || abcElem.el_type !== "note") return;
        const charIndex = this.sourceChar(abcElem.startChar);
        if (charIndex === null) return;  // clicked inside an injected lyric line
        let ref = null;
        try { ref = AbcEdit.findNote(this.getModel(), charIndex); }
        catch (error) { this.editor.status(error.message, true); return; }
        if (!ref) { this.clearSelection(); return; }
        this.select(ref);
        this.editor.root.focus();
    }

    select(ref) {
        this.selection = { voice: ref.voice, barIdx: ref.barIdx, off: ref.off };
        this.paintSelection(ref);
        this.describe(ref);
        this.updateToolbar(ref);
        // Mirror into the Advanced ABC textarea so switching panes lands here.
        const [s, e] = ref.spans[0];
        try { this.editor.raw.setSelectionRange(s, e); } catch { /* detached */ }
    }

    clearSelection() {
        this.selection = null;
        this.paintSelection(null);
        this.info.textContent = "Click a note to select it.";
        this.updateToolbar();
    }

    currentRef(model) {
        if (!this.selection) return null;
        return AbcEdit.locate(model, this.selection.voice, this.selection.barIdx, this.selection.off);
    }

    restoreSelection() {
        if (!this.selection) return;
        let ref = null;
        try { ref = this.currentRef(this.getModel()); } catch { /* stale */ }
        if (ref) this.select(ref);
        else this.clearSelection();
    }

    /* Highlight every engraved element whose source range falls inside the
     * selected chain's spans. */
    paintSelection(ref) {
        for (const g of this.paper.querySelectorAll(".nv-selected")) g.classList.remove("nv-selected");
        if (!ref || !this.visual) return;
        for (const elem of this.engravedElements()) {
            const s = this.sourceChar(elem.startChar);
            if (s !== null && ref.spans.some(([a, b]) => s >= a && s < b)) {
                for (const g of (elem.abselem && elem.abselem.elemset) || []) g.classList.add("nv-selected");
            }
        }
    }

    *engravedElements() {
        if (!this.visual || !this.visual.lines) return;
        for (const line of this.visual.lines) {
            if (!line.staff) continue;
            for (const staff of line.staff) {
                for (const voice of staff.voices) {
                    for (const elem of voice) if (elem.el_type === "note") yield elem;
                }
            }
        }
    }

    describe(ref) {
        const bar = this.getModel().bars[ref.voice][ref.barIdx];
        const meter = `${bar.meter.n}/${bar.meter.d}`;
        const what = ref.kind === "note" ? pitchName(ref.pitch) : "rest";
        const tied = ref.chain.length > 1 ? `, tied across ${ref.chain.length} bars` : "";
        this.info.textContent =
            `${ref.voice} · bar ${ref.barIdx + 1} (M:${meter}) · ${what} · ${ref.totalUnits} units${tied}`;
    }

    updateToolbar(ref = null) {
        const hasNote = !!ref && ref.kind === "note";
        const hasAny = !!ref;
        for (const b of this.noteButtons) b.disabled = !hasNote;
        this.restButton.disabled = !hasAny;
        this.restButton.textContent = ref && ref.kind === "rest" ? "Note" : "Rest";
    }

    /* -------------------------------------------------------------- edits */

    async applyEdit(fn) {
        if (this.applying || this.editor.busy) return false;
        this.applying = true;
        try {
            const model = this.getModel();
            const ref = this.currentRef(model);
            if (!ref) { this.editor.status("Click a note first.", true); return false; }
            let result;
            try {
                result = fn(model, ref);
            } catch (error) {
                this.model = null;  // fn may have half-mutated the model
                if (error instanceof AbcEdit.EditError) { this.editor.status(error.message, true); return false; }
                throw error;
            }
            this.model = null;
            if (result.source === this.editor.source) return true;
            if (result.ref) {
                this.selection = { voice: result.ref.voice, barIdx: result.ref.barIdx, off: result.ref.off };
            }
            this.stop();
            this.editor.pushUndo();
            this.editor.write(result.source);
            const ok = await this.editor.validate();  // accept() re-renders this pane
            if (!ok) {
                this.applying = false;
                await this.editor.restore(false);
                return false;
            }
            return true;
        } finally {
            this.applying = false;
        }
    }

    stepDuration(direction) {
        return this.applyEdit((model, ref) => {
            const current = ref.totalUnits;
            let target;
            if (direction > 0) target = LADDER.find(v => v > current);
            else target = [...LADDER].reverse().find(v => v < current);
            if (!target) throw new AbcEdit.EditError(direction > 0 ? "Already at the longest step." : "Already at the shortest step.");
            return AbcEdit.setDuration(model, ref, target);
        });
    }

    toggleRest() {
        return this.applyEdit((model, ref) =>
            ref.kind === "rest" ? AbcEdit.toNote(model, ref) : AbcEdit.toRest(model, ref));
    }

    /* ----------------------------------------------------------- keyboard */

    onKey(event) {
        if (event.key === "Escape") {
            if (!this.selection) return false;  // let the editor collapse instead
            this.clearSelection();
            return true;
        }
        if (event.code === "Space") {
            if (this.synthControl && !event.repeat) { try { this.synthControl.play(); } catch { /* no tune */ } }
            return true;
        }
        if (!this.selection) return false;
        if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            const step = (event.key === "ArrowUp" ? 1 : -1) * (event.shiftKey ? 12 : 1);
            this.applyEdit((m, r) => AbcEdit.changePitch(m, r, step));
            return true;
        }
        if (event.key === "+" || event.key === "=") { this.stepDuration(1); return true; }
        if (event.key === "-" || event.key === "_") { this.stepDuration(-1); return true; }
        if (event.key === "Delete" || event.key === "Backspace") {
            this.applyEdit((m, r) => r.kind === "note" ? AbcEdit.toRest(m, r)
                : (() => { throw new AbcEdit.EditError("Already a rest."); })());
            return true;
        }
        return false;
    }
}

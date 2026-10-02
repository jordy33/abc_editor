/*
 * Standalone shell around the YuE2 piano roll.
 *
 * This is `web/score_editor.js` from the ComfyUI custom node with the ComfyUI
 * integration removed. What changed:
 *
 *   import { app } from "../../scripts/app.js"   -> deleted
 *   import { api } from "../../scripts/api.js"   -> deleted
 *   api.fetchApi(path, {...})                    -> fetch(path, {...})
 *   node/widget source of truth                  -> a plain in-memory string
 *   node.size / setDirtyCanvas                   -> real DOM layout
 *   "Expand" moved the DOM into document.body    -> a CSS class toggle
 *   Import/Export used a hidden <input type=file> -> kept, plus native save
 *
 * The piano roll itself (`./piano_roll.js`) is byte-identical to the ComfyUI
 * version. Everything it touches is routed through the ScoreEditor shape it
 * already expected: `.data`, `.commit()`, `.busy`, `.root`, `.piano`.
 */

import { PianoRoll, el, button } from "./piano_roll.js";

const STORAGE_KEY = "fl-yue2-abc-editor/score";

class ScoreEditor {
    constructor() {
        this.revision = 0;
        this.undo = [];
        this.redo = [];
        this.root = el("div");
        this.root.className = "fl-yue2-editor";
        this.root.tabIndex = 0;
        this.content = el("div", null, this.root);
        this.content.className = "fl-yue2-content";

        // The piano roll reads `this.editor.root.focus(...)` for keyboard input.
        this.root.addEventListener("keydown", event => this.onKeyDown(event));

        const toolbar = el("div", null, this.content);
        toolbar.className = "row";
        this.pianoButton = button(toolbar, "Piano roll", () => this.showPiano());
        button(toolbar, "New blank score", () => this.commit(data => {
            data.roll.tracks = { Vocal: [], Ins: [] };
            data.roll.chords = [];
            data.roll.sections = [{ name: "verse", bars: 4 }];
        }), true);
        button(toolbar, "Advanced ABC", () => { this.roll.pause(); this.piano.hidden = true; this.raw.hidden = false; }, true);
        button(toolbar, "Import ABC", () => this.file.click(), true);
        button(toolbar, "Export ABC", () => this.export(), true);
        this.undoButton = button(toolbar, "Undo", () => this.restore(false), true);
        this.redoButton = button(toolbar, "Redo", () => this.restore(true), true);
        button(toolbar, "Expand / close", () => this.expand(!this.expanded), true);

        this.file = el("input", null, this.content);
        this.file.type = "file";
        this.file.accept = ".abc,.txt";
        this.file.hidden = true;
        this.file.onchange = async () => {
            const file = this.file.files[0];
            if (!file) return;
            if (file.size > 200000) { this.file.value = ""; return this.status("Maximum score file size is 200 KB.", true); }
            const text = await file.text();
            this.file.value = "";
            this.pushUndo();
            this.write(text);
            if (await this.validate()) { this.showPiano(); }
        };

        this.message = el("div", "Loading score…", this.content);
        this.message.className = "status";
        this.piano = el("div", null, this.content);
        this.roll = new PianoRoll(this);
        this.raw = el("textarea", null, this.content);
        this.raw.hidden = true;
        this.raw.spellcheck = false;
        this.raw.setAttribute("aria-label", "Advanced ABC source");
        this.raw.oninput = () => {
            this.revision++;
            this.roll.pause();
            this.pianoButton.disabled = true;
            this.write(this.raw.value);
            clearTimeout(this.timer);
            this.timer = setTimeout(() => this.validate(), 450);
        };
        el("p", "Preview plays a simple melody sound. This editor only reads and writes ABC notation — it does not generate audio.", this.content).className = "help";

        this.observer = new ResizeObserver(() => this.scheduleLayout());
        this.observer.observe(this.content);
        this.load();
    }

    /* ---------------------------------------------------------------- layout */

    scheduleLayout() {
        cancelAnimationFrame(this.layoutFrame);
        this.layoutFrame = requestAnimationFrame(() => {
            const width = this.content.clientWidth;
            if (!width || width === this.contentWidth) return;
            this.contentWidth = width;
            if (this.roll.scroll && this.data?.grid_available) this.roll.draw();
        });
    }

    expand(expanded) {
        if (expanded === !!this.expanded) return;
        this.expanded = expanded;
        this.root.classList.toggle("fl-yue2-expanded", expanded);
        document.body.classList.toggle("expanded-open", expanded);
        this.root.focus();
        this.scheduleLayout();
    }

    destroy() {
        this.expand(false);
        this.observer.disconnect();
        cancelAnimationFrame(this.layoutFrame);
        this.revision++;
        clearTimeout(this.timer);
        this.roll.stop();
    }

    /* -------------------------------------------------------------- keyboard */

    onKeyDown(event) {
        if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
        if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); this.roll.deleteNote(); }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); this.restore(event.shiftKey); }
        if (event.code === "Space") { event.preventDefault(); if (!event.repeat) this.roll.togglePlayback(); }
        if (event.key === "Escape") this.expand(false);
        if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) {
            event.preventDefault();
            this.roll.nudge(event.key, event.shiftKey);
        }
    }

    /* ----------------------------------------------------------------- state */

    status(text, error = false) {
        this.message.textContent = text;
        this.message.className = "status" + (error ? " error" : "");
    }

    write(text) {
        this.source = text;
        this.raw.value = text;
        try { localStorage.setItem(STORAGE_KEY, text); } catch { /* private mode */ }
    }

    pushUndo() {
        const value = typeof this.source === "string" ? this.source : this.raw.value;
        if (this.undo[this.undo.length - 1] === value) return;
        this.undo.push(value);
        if (this.undo.length > 50) this.undo.shift();
        this.redo = [];
    }

    async load() {
        this.roll.stop();
        this.undo = [];
        this.redo = [];
        let saved = null;
        try { saved = localStorage.getItem(STORAGE_KEY); } catch { /* private mode */ }
        if (saved) {
            this.source = saved;
        } else {
            // ComfyUI's widget always carried a starting score; fetch the same
            // default from the server so a fresh browser opens a valid score.
            try {
                const response = await fetch("/fl_yue2/score/default");
                this.source = (await response.json()).score_abc;
            } catch {
                this.source = "";
            }
        }
        this.raw.value = this.source;
        this.validate();
    }

    /* --------------------------------------------------------------- network */

    async request(path, body) {
        const response = await fetch(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        let result;
        try {
            result = await response.json();
        } catch {
            throw new Error(`The editor server did not return JSON (HTTP ${response.status}). Is server.py still running?`);
        }
        if (!response.ok) throw new Error(result.error || "Score validation failed.");
        return result;
    }

    async validate() {
        clearTimeout(this.timer);
        this.roll.pause();
        const revision = ++this.revision;
        try {
            const result = await this.request("/fl_yue2/score/validate", { score_abc: this.source });
            if (revision !== this.revision) return false;
            this.accept(result);
            return true;
        } catch (error) {
            if (revision === this.revision) {
                this.status(error.message, true);
                this.pianoButton.disabled = true;
                this.piano.hidden = true;
                this.raw.hidden = false;
            }
            return false;
        }
    }

    accept(result) {
        this.data = result;
        this.write(result.abc);
        this.pianoButton.disabled = !result.grid_available;
        this.status(`${result.bars} bars · ${result.bpm} BPM · about ${Math.round(result.seconds)} seconds`);
        this.undoButton.disabled = !this.undo.length;
        this.redoButton.disabled = !this.redo.length;
        if (result.grid_available) this.roll.render();
        else {
            this.piano.hidden = true;
            this.raw.hidden = false;
            this.status("Key or meter changes within the song: original notation preserved in Advanced ABC.");
        }
    }

    showPiano() {
        if (!this.data || this.pianoButton.disabled) return;
        this.piano.hidden = false;
        this.raw.hidden = true;
    }

    async commit(change) {
        if (this.busy) return;
        this.roll.pause();
        this.busy = true;
        const before = this.source;
        const draft = structuredClone(this.data);
        const revision = ++this.revision;
        try {
            change(draft);
            const result = await this.request("/fl_yue2/score/build", draft);
            if (revision !== this.revision) return;
            if (this.undo[this.undo.length - 1] !== before) this.undo.push(before);
            if (this.undo.length > 50) this.undo.shift();
            this.redo = [];
            this.accept(result);
            return true;
        } catch (error) {
            if (revision === this.revision) {
                this.roll.render();
                this.status(error.message, true);
            }
            return false;
        } finally {
            this.busy = false;
        }
    }

    async restore(redo) {
        const from = redo ? this.redo : this.undo;
        const to = redo ? this.undo : this.redo;
        if (!from.length || this.busy) return;
        this.roll.stop();
        to.push(this.source);
        this.write(from.pop());
        await this.validate();
    }

    async export() {
        if (!await this.validate()) return;
        // Prefer the native save panel, fall back to a download link.
        const name = "yue2-score.abc";
        if (window.showSaveFilePicker) {
            try {
                const handle = await window.showSaveFilePicker({
                    suggestedName: name,
                    types: [{ description: "ABC notation", accept: { "text/plain": [".abc"] } }],
                });
                const writable = await handle.createWritable();
                await writable.write(this.data.abc);
                await writable.close();
                this.status(`Saved ${handle.name}`);
                return;
            } catch (error) {
                if (error.name === "AbortError") return; // user cancelled
            }
        }
        const url = URL.createObjectURL(new Blob([this.data.abc], { type: "text/plain" }));
        const link = el("a");
        link.href = url;
        link.download = name;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
}

const editor = new ScoreEditor();
document.getElementById("editor").append(editor.root);
window.yue2Editor = editor;

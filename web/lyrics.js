/*
 * Display-only lyrics support for the Partitura view.
 *
 * The YuE2 dialect has no lyric lines — its validator rejects `w:` — and the
 * lyrics .txt carries whole lines per [Section] with no syllable↔note timing.
 * So everything here is visual guesswork layered on top of the score:
 *
 *   - `parseLyricSections` reads the [Intro]/[Verse]/… text file.
 *   - `syllabify` splits Spanish words into syllables (standard onset/coda and
 *     diphthong/hiatus rules; approximate by nature).
 *   - `buildLyricsDisplay` distributes each section's syllables across that
 *     section's Vocal notes and injects `w:` lines into an IN-MEMORY copy of
 *     the ABC for rendering. The real score text is never modified, and the
 *     returned insertion table lets the view map rendered char offsets back
 *     to true source offsets, so click-to-edit keeps working.
 *
 * The alignment is an honest approximation — YuE2 decides the real phrasing
 * at render time — which is why the view labels this mode "experimental".
 */

const VOWELS = "aeiouáéíóúü";
const STRONG = "aeoáéó";
const ACCENTED_WEAK = "íú";
const CLUSTERS = new Set(["pr", "br", "tr", "dr", "cr", "gr", "fr", "kr",
                          "pl", "bl", "cl", "gl", "fl", "kl", "ch", "ll", "rr"]);

export function syllabify(word) {
    const lower = word.toLowerCase();
    const n = word.length;
    const isVowel = (k) => k >= 0 && k < n && VOWELS.includes(lower[k]);
    const sylls = [];
    let i = 0;
    while (i < n) {
        const start = i;
        while (i < n && !isVowel(i)) i++;
        if (i >= n) {
            // Trailing consonants belong to the previous syllable.
            if (sylls.length) sylls[sylls.length - 1] += word.slice(start);
            else sylls.push(word.slice(start));
            break;
        }
        i++;  // first nucleus vowel
        while (i < n && isVowel(i)) {
            const a = lower[i - 1], b = lower[i];
            const hiatus = (STRONG.includes(a) && STRONG.includes(b))
                || ACCENTED_WEAK.includes(a) || ACCENTED_WEAK.includes(b);
            if (hiatus) break;
            i++;
        }
        let j = i;
        while (j < n && !isVowel(j)) j++;
        const consonants = j - i;
        let keep;  // coda consonants that stay with this syllable
        if (j >= n) keep = consonants;
        else if (consonants <= 1) keep = 0;
        else if (consonants === 2) keep = CLUSTERS.has(lower.slice(j - 2, j)) ? 0 : 1;
        else keep = CLUSTERS.has(lower.slice(j - 2, j)) ? consonants - 2 : consonants - 1;
        i += keep;
        sylls.push(word.slice(start, i));
    }
    return sylls.length ? sylls : [word];
}

/* [Tag] blocks -> [{tag, lines}] in file order; text before any tag is ignored. */
export function parseLyricSections(text) {
    const sections = [];
    let current = null;
    for (const raw of text.split(/\r?\n/)) {
        const line = raw.trim();
        const tag = /^\[([^\]]+)\]$/.exec(line);
        if (tag) {
            current = { tag: tag[1], lines: [] };
            sections.push(current);
            continue;
        }
        if (line && current) current.lines.push(line);
    }
    return sections;
}

/* One section's text -> flat syllable tokens ("com-", "par-", "ti-", "mos"). */
function sectionSyllables(lines) {
    const tokens = [];
    for (const line of lines) {
        for (const word of line.split(/\s+/)) {
            const clean = word.replace(/[|_*~\\]/g, "");
            if (!clean) continue;
            const sylls = syllabify(clean);
            sylls.forEach((syl, index) => {
                tokens.push(index < sylls.length - 1 ? syl + "-" : syl);
            });
        }
    }
    return tokens;
}

/* Build the render-only text with injected `w:` lines.
 *
 * `model` comes from abc_edit.analyze(source); `rollSections` is the
 * editor's data.roll.sections ({name, bars, start}); `lyricSections` is
 * parseLyricSections output, matched to rollSections by order.
 *
 * Returns {text, insertions: [{src, len}], warnings} where each insertion is
 * a whole line added at source offset `src` (so offsets map cleanly). */
export function buildLyricsDisplay(model, rollSections, lyricSections) {
    const source = model.source;
    const lineStart = [];
    let pos = 0;
    for (const line of source.split("\n")) { lineStart.push(pos); pos += line.length + 1; }
    const lineIndexOf = (offset) => {
        let lo = 0, hi = lineStart.length - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (lineStart[mid] <= offset) lo = mid; else hi = mid - 1;
        }
        return lo;
    };

    // Group the Vocal bars by source line, in order.
    const lines = [];  // [{lineIdx, bars: [bar]}]
    for (const bar of model.bars.Vocal) {
        const lineIdx = lineIndexOf(bar.charStart);
        if (!lines.length || lines[lines.length - 1].lineIdx !== lineIdx) {
            lines.push({ lineIdx, bars: [] });
        }
        lines[lines.length - 1].bars.push(bar);
    }

    // Per-section syllable queues, matched by order.
    const queues = rollSections.map((section, index) => {
        const lyric = lyricSections[index];
        return lyric ? sectionSyllables(lyric.lines) : [];
    });
    const sectionOfBar = (barIdx) => {
        for (let s = rollSections.length - 1; s >= 0; s--) {
            if (rollSections[s].start <= barIdx) return s;
        }
        return 0;
    };

    const insertions = [];
    for (const line of lines) {
        const tokens = [];
        let hasSyllable = false;
        for (const bar of line.bars) {
            const queue = queues[sectionOfBar(bar.index)];
            for (const ev of bar.events) {
                if (ev.kind !== "note") continue;  // rests take no syllable
                const pieces = Math.max(1, ev.pieces.length);
                for (let p = 0; p < pieces; p++) {
                    if (p === 0 && !ev.tieIn) {
                        const syl = queue.shift();
                        if (syl) { tokens.push(syl); hasSyllable = true; }
                        else tokens.push("*");
                    } else {
                        tokens.push("_");  // tied continuation holds the syllable
                    }
                }
            }
        }
        if (!hasSyllable) continue;
        const src = lineStart[line.lineIdx + 1] ?? source.length;
        insertions.push({ src, text: `w: ${tokens.join(" ")}\n` });
    }

    const warnings = [];
    queues.forEach((queue, index) => {
        if (queue.length) {
            warnings.push(`${rollSections[index].name}: ${queue.length} sílabas no caben en las notas`);
        }
    });

    insertions.sort((a, b) => a.src - b.src);
    let text = "";
    let cursor = 0;
    const table = [];
    for (const ins of insertions) {
        text += source.slice(cursor, ins.src) + ins.text;
        table.push({ src: ins.src, len: ins.text.length });
        cursor = ins.src;
    }
    text += source.slice(cursor);
    return { text, insertions: table, warnings };
}

/* Offset mapping between the rendered (injected) text and the true source. */
export function makeOffsetMap(insertions) {
    return {
        toSource(renderOffset) {
            let shift = 0;
            for (const ins of insertions) {
                const renderStart = ins.src + shift;
                if (renderOffset < renderStart) break;
                if (renderOffset < renderStart + ins.len) return null;  // inside a w: line
                shift += ins.len;
            }
            return renderOffset - shift;
        },
        toRender(sourceOffset) {
            let shift = 0;
            for (const ins of insertions) {
                if (ins.src > sourceOffset) break;
                shift += ins.len;
            }
            return sourceOffset + shift;
        },
    };
}

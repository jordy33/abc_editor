"""Drive the editor through the Chrome DevTools Protocol and assert real behaviour.

Run: .venv/bin/python tests/browser_check.py
Requires the editor server on :8199 and headless Chrome exposing :9222.
"""
import json, sys, time, base64, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.score import inspect_score, DEFAULT_SCORE

try:
    from websocket import create_connection  # websocket-client
except ImportError:
    print("need websocket-client (pip install websocket-client)"); sys.exit(2)

EDITOR_URL = "http://127.0.0.1:8199/"

def targets():
    return json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))

def new_tab(url):
    req = urllib.request.Request(f"http://127.0.0.1:9222/json/new?{url}", method="PUT")
    return json.load(urllib.request.urlopen(req))

class Session:
    def __init__(self, ws_url):
        self.ws = create_connection(ws_url, timeout=25)
        self.id = 0
    def send(self, method, **params):
        self.id += 1
        self.ws.send(json.dumps({"id": self.id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                if "error" in msg: raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
    def eval(self, expr, await_promise=False):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=await_promise, userGesture=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {r['exceptionDetails'].get('exception',{}).get('description') or r['exceptionDetails']}")
        return r["result"].get("value")

def main():
    tab = new_tab(EDITOR_URL)
    s = Session(tab["webSocketDebuggerUrl"])
    s.send("Runtime.enable")
    s.send("Page.enable")
    # Clear any stored score so we test the fresh-load path.
    s.eval("localStorage.clear()")
    s.send("Page.navigate", url=EDITOR_URL)
    time.sleep(4)

    failures = []
    def check(name, ok, detail=""):
        print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))
        if not ok: failures.append(name)

    print("\n== editor bootstrap ==")
    check("editor constructed", s.eval("!!window.yue2Editor"))
    check("roll constructed", s.eval("!!window.yue2Editor.roll"))
    data = s.eval("window.yue2Editor.data ? {bars:window.yue2Editor.data.bars,bpm:window.yue2Editor.data.bpm,grid:window.yue2Editor.data.grid_available,sections:window.yue2Editor.data.sections.length} : null")
    check("default score validated on load", data is not None, str(data))
    if data:
        check("8 bars loaded", data["bars"] == 8, str(data))
        check("grid available", data["grid"] is True, str(data))
        check("2 sections", data["sections"] == 2, str(data))

    print("\n== piano roll rendering ==")
    svg = s.eval("(()=>{const s=document.querySelector('.fl-yue2-editor svg'); if(!s) return null; return {w:s.getAttribute('width'),h:s.getAttribute('height'),rects:s.querySelectorAll('rect').length,texts:s.querySelectorAll('text').length,notes:s.querySelectorAll('[data-note]').length};})()")
    check("svg rendered", svg is not None, str(svg))
    if svg:
        check("svg has note rects", svg["notes"] > 0, str(svg))
        check("svg has keyboard+grid rects", svg["rects"] > 40, str(svg))
        check("svg has pitch labels", svg["texts"] > 20, str(svg))
    check("status shows bars/bpm", "bars" in (s.eval("document.querySelector('.fl-yue2-editor .status').textContent") or ""))
    check("no error status", "error" not in (s.eval("document.querySelector('.fl-yue2-editor .status').className") or ""))

    print("\n== chord strip above the timeline ==")
    strip = s.eval("""(()=>{
        const strip=document.querySelector('.fl-yue2-editor .chord-strip');
        const roll=document.querySelector('.fl-yue2-editor .roll');
        if(!strip||!roll) return null;
        const sr=strip.getBoundingClientRect(), rr=roll.getBoundingClientRect();
        const buttons=[...strip.querySelectorAll('.chord-strip-button')];
        return {
            buttons: buttons.length,
            above: sr.bottom <= rr.top + 2,
            labels: buttons.map(b=>b.querySelector('.chord-strip-number').textContent),
            widths: [...new Set(buttons.map(b=>Math.round(b.getBoundingClientRect().width)))].length,
            hasIcon: !!strip.querySelector('.chord-strip-label'),
        };
    })()""")
    check("chord strip rendered", strip is not None, str(strip))
    if strip:
        # The default score's first section is 4 bars, so the strip shows 4.
        check("one button per bar in the section", strip["buttons"] >= 4, str(strip))
        check("strip sits above the roll", strip["above"] is True, str(strip))
        check("buttons share one width (aligned to bars)", strip["widths"] == 1, str(strip))
        check("strip carries a chord icon", strip["hasIcon"] is True, str(strip))
        check("bars numbered 1..n", strip["labels"][:3] == ["1", "2", "3"], str(strip["labels"][:5]))

    print("\n== clicking a bar opens that bar only ==")
    perbar = s.eval("""(async()=>{
        const buttons=[...document.querySelectorAll('.chord-strip-button')];
        // Pick a bar that exists in this section (the default has 4).
        const btn=buttons[Math.min(2, buttons.length-1)];
        if(!btn) return null;
        const wanted=btn.querySelector('.chord-strip-number').textContent;
        btn.click();
        await new Promise(r=>setTimeout(r,1100));
        const m=document.querySelector('.fl-yue2-modal');
        if(!m) return null;
        const title=m.querySelector('.modal-header strong').textContent;
        const out={
            wanted: 'Bar '+wanted,
            title,
            bars: m.querySelectorAll('.chord-bar').length,
            cells: m.querySelectorAll('.chord-cell').length,
        };
        // Step to the next bar with the nav control.
        const next=[...m.querySelectorAll('.modal-nav button')].find(b=>b.textContent==='▶');
        next.click();
        await new Promise(r=>setTimeout(r,900));
        const m2=document.querySelector('.fl-yue2-modal');
        out.titleAfterStep = m2.querySelector('.modal-header strong').textContent;
        out.barsAfterStep = m2.querySelectorAll('.chord-bar').length;
        return out;
    })()""", await_promise=True)
    check("bar button opens a single-bar modal", bool(perbar and perbar["bars"] == 1), str(perbar))
    check("modal is titled with the clicked bar", bool(perbar and perbar["title"] == perbar["wanted"]), str(perbar))
    check("all beats of that bar shown", bool(perbar and perbar["cells"] >= 3), str(perbar))
    check("nav steps to the next bar", bool(perbar and perbar["titleAfterStep"].startswith("Bar ")
          and perbar["titleAfterStep"] != perbar["title"]), str(perbar))

    print("\n== the clear button is scoped to what you are editing ==")
    scoped = s.eval("""(async()=>{
        // Still open on a single bar from the previous step.
        const m0=document.querySelector('.fl-yue2-modal');
        if(m0) m0.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
        await new Promise(r=>setTimeout(r,400));

        // Toolbar entry point opens whole-section scope.
        [...document.querySelectorAll('.fl-yue2-editor .row button')].find(b=>b.textContent.includes('Chords')).click();
        await new Promise(r=>setTimeout(r,1100));
        const sectionBtn=[...document.querySelector('.fl-yue2-modal').querySelectorAll('.modal-footer button')][0];
        const sectionLabel=sectionBtn.textContent;

        // Switching to One bar rescopes it to a single bar.
        const oneBar=[...document.querySelectorAll('.fl-yue2-modal .modal-nav button')]
            .find(b=>b.textContent==='One bar');
        oneBar.click();
        await new Promise(r=>setTimeout(r,900));
        const barBtn=[...document.querySelector('.fl-yue2-modal').querySelectorAll('.modal-footer button')][0];
        return {sectionLabel, barLabel: barBtn.textContent, sectionTitle: sectionLabel.startsWith('Clear section'),
                barTitle: /^Clear bar \\d+$/.test(barBtn.textContent)};
    })()""", await_promise=True)
    check("section scope names the section", bool(scoped and scoped["sectionTitle"]), str(scoped))
    check("bar scope names the bar", bool(scoped and scoped["barTitle"]), str(scoped))

    print("\n== clearing a bar leaves the rest of the score alone ==")
    clear = s.eval("""(async()=>{
        const ed=window.yue2Editor, r=ed.roll;
        const bar=r.data.roll.bar_ticks, TPB=bar/4;
        // Put a chord on bar 2 beat 1 so there is something to clear.
        await ed.commit(d=>{ d.roll.chords.push({start:bar, symbol:'G'}); });
        await new Promise(x=>setTimeout(x,1500));
        const before=r.data.roll.chords.length;
        // Open bar 2 via the strip and clear it.
        const m0=document.querySelector('.fl-yue2-modal');
        if(m0) m0.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
        await new Promise(x=>setTimeout(x,400));
        [...document.querySelectorAll('.chord-strip-button')]
            .find(b=>b.querySelector('.chord-strip-number').textContent==='2').click();
        await new Promise(x=>setTimeout(x,1100));
        const btn=[...document.querySelector('.fl-yue2-modal').querySelectorAll('.modal-footer button')][0];
        btn.click();
        await new Promise(x=>setTimeout(x,2000));
        const after=r.data.roll.chords.length;
        const inBar2=r.data.roll.chords.filter(c=>c.start>=bar && c.start<2*bar).length;
        return {before, after, inBar2, elsewhere: r.data.roll.chords.filter(c=>c.start>=2*bar).length};
    })()""", await_promise=True)
    check("clearing a bar drops only that bar's chords", bool(clear and clear["inBar2"] == 0 and clear["after"] == clear["before"] - 1), str(clear))
    check("other bars untouched", bool(clear and clear["elsewhere"] > 0), str(clear))

    s.eval("""(()=>{const m=document.querySelector('.fl-yue2-modal'); if(m) m.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); return 1;})()""")

    print("\n== chord modal ==")
    chords = s.eval("""(async()=>{
        const btn=[...document.querySelectorAll('.fl-yue2-editor .row button')].find(b=>b.textContent.includes('Chords'));
        if(!btn) return null;
        btn.click();
        await new Promise(r=>setTimeout(r,900));
        const modal=document.querySelector('.fl-yue2-modal');
        if(!modal) return null;
        return {
            summary: modal.querySelector('.modal-summary').textContent,
            bars: modal.querySelectorAll('.chord-bar').length,
            cells: modal.querySelectorAll('.chord-cell').length,
            selects: modal.querySelectorAll('.chord-cell select').length,
        };
    })()""", await_promise=True)
    check("chord modal opens from toolbar", chords is not None, str(chords))
    if chords:
        check("one row per bar", chords["bars"] >= 4, str(chords))
        check("a cell per beat", chords["cells"] >= chords["bars"] * 3, str(chords))
        check("root/type/beat pickers per cell", chords["selects"] >= chords["cells"] * 3, str(chords))

    print("\n== modal keeps its place on edit ==")
    scroll = s.eval("""(async()=>{
        const body=document.querySelector('.fl-yue2-modal .modal-body');
        body.scrollTop=300;
        const before=body.scrollTop;
        // Edit a chord: set type then root on the first filled cell.
        const cell=[...document.querySelectorAll('.chord-cell')].find(c=>!c.querySelector('.chord-name').classList.contains('empty'));
        if(!cell) return {error:'no filled cell'};
        const sels=cell.querySelectorAll('select');
        sels[1].value='m'; sels[1].onchange();
        await new Promise(r=>setTimeout(r,1800));
        const body2=document.querySelector('.fl-yue2-modal .modal-body');
        return {before, after: body2 ? body2.scrollTop : -1, stillOpen: !!body2};
    })()""", await_promise=True)
    check("modal survives a chord edit", bool(scroll and scroll.get("stillOpen")), str(scroll))
    check("modal scroll preserved after edit", bool(scroll) and abs(scroll.get("after", -1) - scroll.get("before", 0)) < 5, str(scroll))

    s.eval("""(()=>{const m=document.querySelector('.fl-yue2-modal'); if(m) m.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); return 1;})()""")
    check("modal closes on Escape", s.eval("!document.querySelector('.fl-yue2-modal')") is True)

    print("\n== arrangement controls ==")
    arrange = s.eval("""(()=>{
        const root=[...document.querySelectorAll('.fl-yue2-editor details')].find(d=>d.querySelector('summary').textContent.includes('Arrange'));
        if(!root) return null; root.open=true;
        return {buttons:[...root.querySelectorAll('button')].map(b=>b.textContent)};
    })()""")
    check("arrange section present", arrange is not None, str(arrange))
    if arrange:
        check("section controls exist", any("Section" in b for b in arrange["buttons"]), str(arrange))

    print("\n== editing: add a note via commit() ==")
    before = s.eval("window.yue2Editor.data.roll.tracks.Ins.length")
    added = s.eval("""(async()=>{
        const ed=window.yue2Editor;
        const roll=ed.data.roll;
        // Locate a genuinely free gap: the default Ins melody is continuous,
        // so a hardcoded tick would (correctly) trip the overlap guard.
        const notes=[...roll.tracks.Ins].sort((a,b)=>a.start-b.start);
        let cur=0, gap=null;
        for(const n of notes){ if(n.start-cur>=256){ gap=[cur,n.start]; break; } cur=Math.max(cur,n.start+n.duration); }
        if(!gap && roll.total_ticks-cur>=256) gap=[cur,roll.total_ticks];
        if(!gap) return {ok:false, err:'no free gap in fixture'};
        const start=gap[0];
        const ok = await ed.commit(d=>{ d.roll.tracks.Ins.push({start, duration:256, pitch:60}); });
        return {ok, start, added: ed.data.roll.tracks.Ins.some(n=>n.pitch===60 && n.start===start),
                status:document.querySelector('.fl-yue2-editor .status').textContent};
    })()""", await_promise=True)
    check("commit() persists a new note via /build", bool(added and added["ok"]), str(added))
    after = s.eval("window.yue2Editor.data.roll.tracks.Ins.length")
    check("note count grew", after == before + 1, f"{before} -> {after}")
    check("ABC regenerated and re-parses cleanly", s.eval("window.yue2Editor.data.grid_available") is True
          and s.eval("window.yue2Editor.source").startswith("X:1"))

    print("\n== editing: monophonic overlap is rejected ==")
    overlap = s.eval("""(async()=>{
        const ed=window.yue2Editor;
        const note = ed.data.roll.tracks.Ins[0];
        const ok = await ed.commit(d=>{ d.roll.tracks.Ins.push({...note}); });
        return {ok, msg: document.querySelector('.fl-yue2-editor .status').textContent};
    })()""", await_promise=True)
    check("duplicate/overlapping note rejected", overlap and overlap["ok"] is False, str(overlap))
    check("overlap error explains why", overlap and "overlap" in overlap["msg"].lower(), str(overlap))

    print("\n== editing: tempo + key ==")
    tempo = s.eval("""(async()=>{const ed=window.yue2Editor; const ok=await ed.commit(d=>{d.bpm=120;}); return ok && ed.data.bpm===120;})()""", await_promise=True)
    check("tempo change round-trips", bool(tempo))
    check("ABC has Q:1/4=120", "Q:1/4=120" in (s.eval("window.yue2Editor.source") or ""))

    print("\n== undo / redo ==")
    undone = s.eval("""(async()=>{const ed=window.yue2Editor; await ed.restore(false); return ed.data.bpm;})()""", await_promise=True)
    check("undo restored previous tempo", undone == 88, f"bpm={undone}")
    redone = s.eval("""(async()=>{const ed=window.yue2Editor; await ed.restore(true); return ed.data.bpm;})()""", await_promise=True)
    check("redo re-applied tempo", redone == 120, f"bpm={redone}")

    print("\n== audition (WebAudio) ==")
    audio = s.eval("""(async()=>{ try { await window.yue2Editor.roll.audition(64); return 'ok'; } catch(e){ return 'err:'+e.message; } })()""", await_promise=True)
    check("audition() runs without error", audio == "ok", str(audio))

    print("\n== advanced ABC view ==")
    check("raw textarea holds source", s.eval("document.querySelector('.fl-yue2-editor textarea').value.length") > 50)
    inval = s.eval("""(async()=>{const ed=window.yue2Editor; ed.source='garbage'; const ok=await ed.validate(); return {ok, status:document.querySelector('.fl-yue2-editor .status').textContent};})()""", await_promise=True)
    check("invalid ABC surfaces error", inval and inval["ok"] is False, str(inval))
    check("error message is human-readable", inval and "Incomplete" in inval["status"], str(inval))

    print("\n== export path ==")
    exported = s.eval("""(async()=>{const ed=window.yue2Editor; await fetch('/fl_yue2/score/default').then(r=>r.json()).then(j=>{ed.source=j.score_abc; ed.raw.value=j.score_abc;}); const ok=await ed.validate(); return {ok, len: ed.data.abc.length};})()""", await_promise=True)
    check("restored valid score validates", exported and exported["ok"] is True, str(exported))

    print("\n== screenshots ==")
    shot = s.send("Page.captureScreenshot", format="png")
    out = Path(__file__).resolve().parent.parent / "docs" / "screenshot.png"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(base64.b64decode(shot["data"]))
    print(f"  saved {out} ({out.stat().st_size//1024} KB)")

    print("\n== console errors ==")
    errs = s.eval("window.__errs||[]")
    check("no uncaught page errors", not errs, str(errs))

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())

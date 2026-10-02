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

    print("\n== chord lane ==")
    chords = s.eval("""(()=>{
        const root=[...document.querySelectorAll('.fl-yue2-editor details')].find(d=>d.querySelector('summary').textContent.includes('Harmony'));
        if(!root) return null; root.open=true;
        return {summary:root.querySelector('summary').textContent, cards:root.querySelectorAll('.chords > div').length, selects:root.querySelectorAll('.chords select').length};
    })()""")
    check("harmony section present", chords is not None, str(chords))
    if chords:
        check("chord controls per bar", chords["cards"] >= 4, str(chords))

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

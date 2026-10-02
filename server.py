"""Local HTTP server for the standalone YuE2 ABC editor.

Replaces the two routes the ComfyUI custom node registers on PromptServer:

    POST /fl_yue2/score/validate   ->  app.score.inspect_score
    POST /fl_yue2/score/build      ->  app.score.build_score

The request/response contract is identical to the ComfyUI node's, so the
browser code only had to swap `api.fetchApi(path)` for `fetch(path)`. No
ComfyUI, no torch, no GPU: `abc_score.py` is standard-library only, so this
whole server is too.

Usage:
    python server.py                 # http://127.0.0.1:8188
    python server.py --port 9000     # different port
    python server.py --no-browser    # don't open a browser window
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.score import DEFAULT_SCORE, AbcError, build_score, inspect_score

WEB_ROOT = Path(__file__).resolve().parent / "web"
MAX_BODY = 8 * 1024 * 1024  # generous headroom over the 200k-char score limit


class EditorHandler(SimpleHTTPRequestHandler):
    """Static file server for `web/`, plus the two score endpoints."""

    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        # `directory` makes SimpleHTTPRequestHandler serve web/ rather than cwd.
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt, *args):
        # Quieter, single-line access log.
        print(f"  {self.address_string()} {fmt % args}")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        """Parse the request body, mirroring the ComfyUI route's error handling."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise AbcError("Malformed Content-Length header.")
        if length > MAX_BODY:
            raise AbcError("Request body is too large.")
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AbcError("Malformed JSON body.")
        if not isinstance(data, dict):
            raise AbcError("Expected a JSON object.")
        return data

    def _handle(self, action):
        """Run one score action and report failures the way the node does: 400."""
        try:
            data = self._read_json()
            if action is inspect_score:
                result = inspect_score(data.get("score_abc"))
            else:
                result = build_score(data)
            self._send_json(result)
        except AbcError as error:
            self._send_json({"error": str(error)}, status=400)
        except (KeyError, TypeError) as error:
            # A malformed piano-roll document, e.g. a missing "roll".
            self._send_json({"error": f"Invalid score document: {error}"}, status=400)
        except Exception as error:  # noqa: BLE001 - surface unexpected faults to the UI
            self._send_json({"error": f"Internal editor error: {error}"}, status=500)

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        if route == "/fl_yue2/score/validate":
            return self._handle(inspect_score)
        if route == "/fl_yue2/score/build":
            return self._handle(build_score)
        self._send_json({"error": f"Unknown endpoint {route}"}, status=404)

    def do_GET(self):
        # Client-side routes all fall back to index.html.
        route = self.path.split("?", 1)[0]
        if route == "/fl_yue2/score/default":
            # ComfyUI's STRING widget supplied the default score; here it comes
            # from the server so there is a single source of truth.
            return self._send_json({"score_abc": DEFAULT_SCORE})
        if route in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def end_headers(self):
        # The editor is a local dev tool; never let a stale bundle win.
        if self.path.endswith((".js", ".css", ".html")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description="YuE2 ABC editor (standalone, no ComfyUI).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8188, help="default 8188")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args()

    handler = partial(EditorHandler)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"
    print("\n  YuE2 ABC editor")
    print(f"  serving {WEB_ROOT}")
    print(f"  open    {url}")
    print("  stop    Ctrl+C\n")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - headless is fine
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

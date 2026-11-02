"""GET /api/report — relatório do último dia (daily_report + log). ?date=YYYY-MM-DD devolve só o log daquele dia.
Lido pela rotina do Claude via conector da Vercel (web_fetch_vercel_url), sem precisar do Mac."""
import json, os, sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import store  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        d = (q.get("date") or [None])[0]
        obj = store.get_json(store.log_key(d)) if d else store.get_json(store.K_REPORT)
        self.send_response(200 if obj else 404)
        self.send_header("content-type", "application/json; charset=utf-8"); self.send_header("cache-control", "private, no-store")
        self.end_headers(); self.wfile.write(json.dumps(obj or {"status": "not_found"}, ensure_ascii=False).encode())

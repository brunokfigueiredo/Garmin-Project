"""GET /api/cron — chamado pelo Vercel Cron (Authorization: Bearer $CRON_SECRET). ?to=YYYY-MM-DD para reprocessar um dia."""
import json, os, sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import runner  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        secret = os.environ.get("CRON_SECRET")
        if not secret or self.headers.get("authorization") != f"Bearer {secret}":
            self.send_response(401); self.end_headers(); return
        q = parse_qs(urlparse(self.path).query)
        try:
            res = runner.run(to=(q.get("to") or [None])[0])
        except Exception as e:
            res = {"status": "ERROR", "detail": f"{type(e).__name__}: {e}"[:300]}
        # O resumo vai para os logs de runtime (o Claude lê pelo conector da Vercel); sem tokens nem dados brutos.
        print("PIPELINE_RESULT " + json.dumps({k: res.get(k) for k in ("status", "to", "calls", "errors", "readiness", "alerts_today", "duration_s", "detail")}, ensure_ascii=False))
        body = json.dumps(res, ensure_ascii=False).encode()
        self.send_response(200 if res.get("status") == "done" else 500)
        self.send_header("content-type", "application/json; charset=utf-8"); self.end_headers(); self.wfile.write(body)

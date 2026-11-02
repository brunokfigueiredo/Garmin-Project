"""GET /api/data — data.json do painel (o projeto inteiro fica atrás da Vercel Authentication)."""
import os, sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import store  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        gz = store.get_gz(store.K_DATA)
        if not gz:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-encoding", "gzip")
        self.send_header("cache-control", "private, no-store")
        self.end_headers(); self.wfile.write(gz)

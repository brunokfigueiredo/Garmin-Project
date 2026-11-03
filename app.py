"""Entrypoint Python da Vercel (variável `app`, WSGI puro, sem framework).

Rotas:
  /             → public/index.html (o painel)
  /api/cron     → pipeline diário (Vercel Cron, Authorization: Bearer $CRON_SECRET; ?to=AAAA-MM-DD reprocessa um dia)
  /api/data     → data.json do painel
  /api/plan     → aba Plano (treinos da assessoria × realizado + projeção)
  /api/report   → relatório do último dia (?date=AAAA-MM-DD devolve o log daquele dia)
O projeto inteiro fica atrás da Vercel Authentication (All Deployments).
"""
import json, os, sys
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))


def _send(start_response, status, body, ctype="application/json; charset=utf-8", extra=()):
    if isinstance(body, str): body = body.encode()
    start_response(status, [("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "private, no-store"),
                            ("X-Robots-Tag", "noindex"), *extra])
    return [body]


def _json(start_response, code, obj):
    return _send(start_response, {200: "200 OK", 401: "401 Unauthorized", 404: "404 Not Found", 500: "500 Internal Server Error"}[code],
                 json.dumps(obj, ensure_ascii=False))


def app(environ, start_response):
    path = (environ.get("PATH_INFO") or "/").rstrip("/") or "/"
    q = parse_qs(environ.get("QUERY_STRING") or "")
    arg = lambda k: (q.get(k) or [None])[0]

    if path in ("/", "/index.html"):
        with open(os.path.join(HERE, "public", "index.html"), "rb") as f:
            return _send(start_response, "200 OK", f.read(), "text/html; charset=utf-8")

    if path == "/api/cron":
        secret = os.environ.get("CRON_SECRET")
        if not secret or environ.get("HTTP_AUTHORIZATION") != f"Bearer {secret}":
            return _json(start_response, 401, {"status": "unauthorized"})
        import runner
        try:
            res = runner.run(to=arg("to"))
        except Exception as e:
            res = {"status": "ERROR", "detail": f"{type(e).__name__}: {e}"[:300]}
        # Resumo nos logs de runtime (o Claude lê pelo conector da Vercel); sem tokens nem dados brutos.
        print("PIPELINE_RESULT " + json.dumps({k: res.get(k) for k in ("status", "to", "calls", "errors", "readiness", "alerts_today", "plan_workouts", "plan_error", "duration_s", "detail")}, ensure_ascii=False), flush=True)
        return _json(start_response, 200 if res.get("status") == "done" else 500, res)

    if path == "/api/data":
        import store
        gz = store.get_gz(store.K_DATA)
        if not gz: return _json(start_response, 404, {"status": "not_found"})
        return _send(start_response, "200 OK", gz, extra=(("Content-Encoding", "gzip"),))

    if path == "/api/plan":
        import store
        gz = store.get_gz(store.K_PLAN)
        if not gz: return _json(start_response, 404, {"status": "not_found"})
        return _send(start_response, "200 OK", gz, extra=(("Content-Encoding", "gzip"),))

    if path == "/api/report":
        import store
        d = arg("date")
        obj = store.get_json(store.log_key(d)) if d else store.get_json(store.K_REPORT)
        return _json(start_response, 200 if obj else 404, obj or {"status": "not_found"})

    return _json(start_response, 404, {"status": "not_found", "path": path})

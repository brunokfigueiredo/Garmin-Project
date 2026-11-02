"""Execução diária na Vercel: tokens → coleta (só GET) → daily.py → Redis."""
import json, os, sys, tempfile, time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pipeline"))
sys.path.insert(0, HERE)

import store  # noqa: E402
from collector import Collector  # noqa: E402

DAY = 86400


def today_sp():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def load_tokens():
    t = store.r().get(store.K_TOKENS)
    if t: return t, "redis"
    seed = os.environ.get("GARMIN_TOKENS_SEED")
    if seed: return seed.strip(), "env"
    return None, None


def run(to: str | None = None, dry: bool = False):
    from garminconnect import Garmin
    t_start = time.time()
    to = to or today_sp().isoformat()
    T = date.fromisoformat(to)
    cfg = {"DB": f"garmin_daily_{to}", "ACT_LIMIT": 50, "WELL_FROM": (T - timedelta(3)).isoformat(), "HIST_FROM": (T - timedelta(3)).isoformat(),
           "DET_FROM": (T - timedelta(7)).isoformat(), "TO": to, "EXPORT": f"garmin_daily_{to}.json"}
    if not store.r().set(store.K_LOCK, to, nx=True, ex=900):
        return {"status": "LOCKED", "to": to}
    try:
        tokens, src = load_tokens()
        if not tokens:
            return {"status": "NO_TOKENS", "to": to}
        g = Garmin()
        try:
            g.login(tokens)  # string JSON → loads(); renova o token DI se estiver perto de expirar
        except Exception as e:
            return {"status": "SESSION_EXPIRED", "to": to, "detail": type(e).__name__}
        status, raw = Collector(g.client, cfg, deadline_s=540).run()
        # tokens rotacionam: sempre gravar a versão atual (o refresh token antigo deixa de valer)
        try: store.r().set(store.K_TOKENS, g.client.dumps())
        except Exception: pass
        store.put_json(store.raw_key(to), raw, ex=30 * DAY)
        if status != "done":
            log = {"run_at": datetime.now(timezone.utc).isoformat(), "to": to, "status": status, "calls": raw["__calls"], "errors": raw["__errs"]}
            store.put_json(store.log_key(to), log, ex=400 * DAY)
            return log
        lote = store.get_bytes(store.K_LOTE)
        if not lote:
            return {"status": "NO_HISTORY", "to": to, "calls": raw["__calls"], "detail": "rode scripts/seed_history.py uma vez"}
        tmp = tempfile.mkdtemp()
        fl, fr, out = f"{tmp}/lote_completo.json", f"{tmp}/garmin_daily_{to}.json", f"{tmp}/saida"
        open(fl, "wb").write(lote)
        json.dump(raw, open(fr, "w"), ensure_ascii=False)
        import daily
        daily.main(fl, fr, to, out)
        log = json.load(open(f"{out}/log_{to}.json"))
        rep = json.load(open(f"{out}/derived/daily_report.json"))
        rep.pop("vdot_series", None)
        log["status"] = "done"; log["duration_s"] = round(time.time() - t_start, 1); log["tokens_from"] = src
        if not dry:
            store.r().set(store.K_LOTE_PREV, store.r().get(store.K_LOTE))
            store.put_bytes(store.K_LOTE, open(f"{out}/lote_completo.json", "rb").read())
            store.put_bytes(store.K_DATA, open(f"{out}/data.json", "rb").read())
            store.put_json(store.K_REPORT, {"report": rep, "log": log})
            store.put_json(store.log_key(to), log, ex=400 * DAY)
        return log
    finally:
        store.r().delete(store.K_LOCK)

"""Fase 5 — orquestrador diário (roda na nuvem).
Usage: python3 -I daily.py <full_batch.json> <daily_raw.json> <to YYYY-MM-DD> <out_dir>
1) normaliza o bruto diário (janela to−3..to); 2) faz upsert no histórico completo (chaves do esquema);
3) recalcula VO2max carregado; 4) roda o motor (Fase 3); 5) gera lote completo novo + data.json do painel + log."""
import json, sys, os, tempfile
from datetime import date, timedelta, datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import normalize, engine, build_data

KEYS = {"activity_cpet": "activity_id", "activities": "activity_id", "activity_details": "activity_id", "daily_wellness": "date", "daily_performance": "date", "gear": "gear_id", "races_and_goals": "race_id"}


def main(full, daily, to, out):
    os.makedirs(out, exist_ok=True)
    F = json.load(open(full)); U = F["upserts"]
    raw = json.load(open(daily))
    frm = (date.fromisoformat(to) - timedelta(3)).isoformat()
    tmp = tempfile.mkdtemp()
    rep = normalize.main(daily, tmp, frm, to, frm)
    N = {k: json.load(open(f"{tmp}/{k}.json")) for k in ["activities", "activity_details", "daily_wellness", "daily_performance", "gear", "activity_cpet"]}
    stats = {}
    for k, key in KEYS.items():
        if k not in N: continue
        cur = {r[key]: r for r in U.get(k, [])}; added = upd = 0
        for r in N[k]:
            if k == "daily_wellness" and all(v == "missing" for kk, v in r["coverage"].items() if kk != "weight"): continue
            if k == "daily_performance":
                r = {kk: v for kk, v in r.items() if v is not None or kk not in cur.get(r["date"], {})}
                r.pop("vo2max_is_carried", None)
                if r.get("vo2max") is not None and cur.get(r["date"], {}).get("vo2max") == r["vo2max"] and cur[r["date"]].get("vo2max_is_carried"): r.pop("vo2max")
            if k == "activities" and r["activity_id"] in cur and not r["has_details"]: r["has_details"] = cur[r["activity_id"]].get("has_details", False); r["gear_ids"] = r["gear_ids"] or cur[r["activity_id"]].get("gear_ids", [])
            if r[key] in cur: cur[r[key]] = {**cur[r[key]], **r}; upd += 1
            else: cur[r[key]] = r; added += 1
        U[k] = sorted(cur.values(), key=lambda x: str(x[key])); stats[k] = {"added": added, "updated": upd}
    # histórico do limiar do Garmin: novo ponto quando o valor do dia muda
    LTH = U.setdefault("lactate_threshold_history", [])
    pt = next((p for p in U["daily_performance"] if p["date"] == to), None)
    if pt and pt.get("lactate_threshold_hr"):
        last = LTH[-1] if LTH else {}
        if (last.get("lt_hr"), last.get("pace_s_per_km")) != (pt["lactate_threshold_hr"], pt.get("lactate_threshold_pace_s_per_km")):
            sp = pt.get("lactate_threshold_pace_s_per_km")
            LTH.append({"date": to, "lt_hr": pt["lactate_threshold_hr"], "lt_speed_mps": round(1000 / sp, 3) if sp else None, "pace_s_per_km": sp})
    # wellness: garantir linha para cada dia até 'to'
    have = {w["date"] for w in U["daily_wellness"]}
    d = date.fromisoformat(max(have))
    # VO2max carry-forward recalculado
    last = None
    for p in sorted(U["daily_performance"], key=lambda x: x["date"]):
        if p.get("vo2max") is not None and not p.get("vo2max_is_carried"): last = p["vo2max"]
        elif last is not None: p["vo2max"] = last; p["vo2max_is_carried"] = True
    nd = os.path.join(out, "norm"); os.makedirs(nd, exist_ok=True)
    for k in ["activities", "activity_details", "daily_wellness", "daily_performance", "gear", "races_and_goals", "activity_cpet", "lactate_threshold_history"]:
        json.dump(U.get(k, []), open(f"{nd}/{k}.json", "w"), ensure_ascii=False, separators=(",", ":"))
    dd = os.path.join(out, "derived")
    report, rows, alerts = engine.main(nd, dd, to)
    build_data.main(nd, dd, os.path.join(out, "data.json"))
    F["batch_id"] = f"full-{to}"; F["generated_at"] = datetime.now(timezone.utc).isoformat()
    F["meta"]["checkpoints"]["daily_wellness"]["last_date"] = to; F["meta"]["dataset_info"]["last_update"] = F["generated_at"]
    json.dump(F, open(os.path.join(out, "lote_completo.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    log = {"run_at": F["generated_at"], "to": to, "window_from": frm, "calls": raw.get("__calls"), "errors": raw.get("__errs", []), "merge": stats,
           "coverage_today": next((w["coverage"] for w in U["daily_wellness"] if w["date"] == to), None), "alerts_today": [a["message"] for a in report["alerts_today"]],
           "readiness": report["readiness"]["score"], "validation_issues": rep.get("validation_issue_count")}
    json.dump(log, open(os.path.join(out, f"log_{to}.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(log, ensure_ascii=False, indent=1))
    print("\n".join(report["summary"])); print("Recomendações:", *report["recommendations"], sep="\n- ")


if __name__ == "__main__":
    main(*sys.argv[1:])

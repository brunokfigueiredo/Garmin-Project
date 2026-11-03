"""Aba Plano: treinos da assessoria (calendário do Garmin) × realizado, mais a projeção de carga até a maratona.

Entrada: bruto do dia (raw|calendar|AAAA-MM, raw|workout|ID), histórico normalizado (activities, activity_details,
daily_performance) e o plano macro (plan_data/plano_2026_2027.json).
Saída: o JSON servido em /api/plan e um resumo para o relatório diário (treino de amanhã e avaliação do dia).
"""
import json, math, os, re
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
MACRO = os.path.join(HERE, "plan_data", "plano_2026_2027.json")
LV1, LV2 = 133, 162
QUALITY_PACE = 270  # janelas mais rápidas que 4:30/km contam como trecho de qualidade


# ---------- utilidades ----------
def fmt_p(s):
    return f"{int(s // 60)}:{int(round(s % 60)):02d}" if s else "–"


def _sp(v):  # m/s → s/km
    return round(1000 / v) if v else None


def vdot_from(dist_m, t_s):
    t = t_s / 60; v = dist_m / t
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v
    pct = 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)
    return vo2 / pct


# ---------- treinos da assessoria ----------
PACE_RX = re.compile(r"(\d{1,2}):(\d{2})/km(?:\s*\([^)]*\))?\s*a\s*(\d{1,2}):(\d{2})/km(?:\s*\([^)]*\))?")


def _dur(tok):
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", tok)
    if m: return f"{int(m.group(1)) * 60 + int(m.group(2))}'"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", tok)
    if m: return f"{int(m.group(1))}'" + (f"{m.group(2)}\"" if m.group(2) != "00" else "")
    return tok


def _tidy(l):
    l = re.sub(r"RITMO DE PROVA\s*\+?", "ritmo de prova", l)
    l = re.sub(r"-?\s*(\d+)''\s*mais lento do que o ritmo de meia", r'(meia +\1")', l)
    l = re.sub(r"-?\s*(\d+)''\s*mais r[aá]pido\w*\s*do que o ritmo de meia", r'(meia −\1")', l)
    l = re.sub(r"-?\s*no ritmo de meia", "(meia)", l)
    l = re.sub(r"(\d+)\s*x\s*\(", r"\1×(", l)
    l = re.sub(r"(\d)km\b", r"\1 km", l)
    l = re.sub(r"descanso\s+ativo\s+de\s+(\d{1,2}):(\d{2})", lambda m: f"pausa ativa {int(m.group(1))}'" + (f"{m.group(2)}\"" if m.group(2) != "00" else ""), l, flags=re.I)
    l = re.sub(r"\s*-\s*,", ",", l).replace(" - ", " ").replace("( ", "(").replace(" )", ")")
    l = re.sub(r"\(([^()]*?)\s*-\s*\)", r"(\1)", l)
    return re.sub(r"\s{2,}", " ", l).strip(" -")


def parse_description(desc):
    """Texto da assessoria → (resumo curto, alvos [{what, pace:[min,max]}])."""
    lines = [l.strip()[1:].strip() for l in (desc or "").splitlines() if l.strip().startswith("-")]
    short, targets = [], []
    for l in lines:
        paces = PACE_RX.search(l)
        label = PACE_RX.sub("", l)
        label = re.sub(r"\(\s*\d+(?:[.,]\d+)?\s*km/h\)", "", label)
        label = re.sub(r"\s+-\s*$", "", label).strip(" -")
        label = re.sub(r"\s{2,}", " ", label)
        label = re.sub(r"\s+-\s+(?=descanso)", ", ", label, flags=re.I)
        toks = label.split(" ", 1)
        label = (_dur(toks[0]) + (" " + toks[1] if len(toks) > 1 else "")).strip()
        label = _tidy(label)
        short.append(label)
        if paces:
            a = int(paces.group(1)) * 60 + int(paces.group(2)); b = int(paces.group(3)) * 60 + int(paces.group(4))
            targets.append({"what": label, "pace": sorted([a, b])})
    return " + ".join(short), targets


def expand_steps(steps):
    out = []
    for st in steps or []:
        if st.get("type") == "RepeatGroupDTO" or st.get("workoutSteps"):
            for _ in range(int(st.get("numberOfIterations") or 1)):
                out += expand_steps(st.get("workoutSteps"))
            continue
        tt = (st.get("targetType") or {}).get("workoutTargetTypeKey")
        pace = sorted([_sp(st.get("targetValueOne")), _sp(st.get("targetValueTwo"))]) if tt == "pace.zone" and st.get("targetValueOne") else None
        out.append({"kind": (st.get("stepType") or {}).get("stepTypeKey"), "end": (st.get("endCondition") or {}).get("conditionTypeKey"),
                    "value": st.get("endConditionValue"), "pace": pace})
    return out


def classify(km, text, steps, targets=()):
    t = (text or "").lower()
    fast = [s for s in steps if s.get("pace") and s["pace"][0] < QUALITY_PACE] or [{"end": None, "value": None, "pace": x["pace"]} for x in targets if x["pace"][0] < QUALITY_PACE]
    if "técnica" in t or "tecnica" in t: return "técnica"
    if fast and km >= 14: return "longo com ritmo"
    if fast and (any(s["end"] == "distance" and (s["value"] or 0) <= 2000 for s in fast) and len(fast) >= 3 or "×" in (text or "")): return "tiros"
    if fast: return "ritmo"
    if km >= 14: return "longo"
    return "rodagem"


def coach_workouts(raw):
    """Treinos agendados no bruto do dia (calendário + detalhe de cada treino)."""
    out = {}
    for k, v in raw.items():
        if not k.startswith("raw|calendar|") or not isinstance(v, dict): continue
        for it in v.get("calendarItems") or []:
            if it.get("itemType") != "workout" or not it.get("workoutId"): continue
            if it.get("sportTypeKey") not in (None, "running"): continue
            w = raw.get(f"raw|workout|{it['workoutId']}") or {}
            if not isinstance(w, dict) or w.get("__empty"): continue
            steps = expand_steps([s for seg in w.get("workoutSegments") or [] for s in seg.get("workoutSteps") or []])
            text, targets = parse_description(w.get("description"))
            km = round((w.get("estimatedDistanceInMeters") or 0) / 1000, 1)
            out[it["date"]] = {"date": it["date"], "workout_id": it["workoutId"], "schedule_id": it.get("id"), "name": w.get("workoutName") or it.get("title"),
                               "km": km, "min": round((w.get("estimatedDurationInSecs") or 0) / 60), "text": text or (w.get("workoutName") or ""),
                               "targets": targets, "steps": steps, "type": classify(km, text, steps, targets), "description": w.get("description")}
    return out


# ---------- avaliação do treino feito ----------
def evaluate(cw, acts, laps_by_act):
    """Compara o treino agendado com as corridas do mesmo dia."""
    if not acts: return None
    km = sum(a["distance_m"] or 0 for a in acts) / 1000
    dur = sum(a.get("moving_s") or a.get("duration_s") or 0 for a in acts)
    main = max(acts, key=lambda a: a["distance_m"] or 0)
    pace = round(dur / km) if km else None
    hr = main.get("hr_avg")
    ev = {"km": round(km, 1), "min": round(dur / 60), "pace": pace, "hr": hr, "activity_id": main["activity_id"]}
    ratio = (dur / 60) / cw["min"] if cw.get("min") else (km / cw["km"] if cw.get("km") else None)
    ev["volume_pct"] = round(ratio * 100) if ratio else None
    notes = []
    if ev["volume_pct"] is not None:
        notes.append(f"{ev['volume_pct']}% do tempo previsto" if cw.get("min") else f"{ev['volume_pct']}% da distância prevista")
    quality = [t for t in cw["targets"] if t["pace"][0] < QUALITY_PACE]
    easy = [t for t in cw["targets"] if t["pace"][0] >= QUALITY_PACE]
    laps = [l for l in laps_by_act.get(main["activity_id"], []) if (l.get("distance_m") or 0) >= 300 and l.get("pace_s_per_km")]
    if quality:
        res = []
        for t in quality:
            lo, hi = t["pace"]
            near = [l for l in laps if lo - 15 <= l["pace_s_per_km"] <= hi + 10]
            inside = [l for l in near if lo <= l["pace_s_per_km"] <= hi]
            if near:
                avg = sum(l["pace_s_per_km"] * l["distance_m"] for l in near) / sum(l["distance_m"] for l in near)
                res.append({"what": t["what"], "laps": len(near), "in_window": len(inside), "pace": round(avg),
                            "hr_end": near[-1].get("hr_avg")})
                notes.append(f"{t['what']}: {len(inside)}/{len(near)} voltas na janela, média {fmt_p(avg)}")
            else:
                res.append({"what": t["what"], "laps": 0, "in_window": 0})
                notes.append(f"{t['what']}: nenhuma volta perto de {fmt_p(lo)}–{fmt_p(hi)}")
        ev["quality"] = res
        n = sum(r["laps"] for r in res); i = sum(r["in_window"] for r in res)
        ev["in_window_pct"] = round(i / n * 100) if n else 0
    elif easy and pace:
        lo, hi = easy[0]["pace"]
        if pace > hi: notes.append(f"{pace - hi} s/km mais lento que a janela {fmt_p(lo)}–{fmt_p(hi)}")
        elif pace < lo: notes.append(f"{lo - pace} s/km mais rápido que a janela {fmt_p(lo)}–{fmt_p(hi)}")
        else: notes.append("pace dentro da janela")
        ev["in_window_pct"] = 100 if lo <= pace <= hi else 0
    if hr and cw["type"] in ("rodagem", "técnica", "longo"):
        notes.append(f"FC {hr} {'acima' if hr > LV1 else 'dentro'} do LV1 ({LV1})")
    ev["verdict"] = "; ".join(notes) + "." if notes else ""
    return ev


# ---------- montagem ----------
def build(raw, U, to, stored_planned):
    macro = json.load(open(MACRO))
    T = date.fromisoformat(to)
    # treinos da assessoria: guardados de dias anteriores + os lidos hoje (hoje vence)
    planned = dict(stored_planned or {})
    fresh = coach_workouts(raw)
    first_day = macro["weeks"][0]["start"]
    horizon_from = (T - timedelta(7)).isoformat()
    for d in [d for d, w in planned.items() if d >= horizon_from and d not in fresh]:
        planned.pop(d)  # removido do calendário pela assessoria
    planned.update({d: w for d, w in fresh.items() if d >= first_day})

    runs = [a for a in U.get("activities", []) if a.get("is_run") and a["date"] >= first_day]
    by_day = {}
    for a in runs: by_day.setdefault(a["date"], []).append(a)
    laps = {d["activity_id"]: d.get("laps") or [] for d in U.get("activity_details", []) if d.get("activity_id") in {a["activity_id"] for a in runs}}

    workouts = []
    for d in sorted(planned):
        w = {k: v for k, v in planned[d].items() if k not in ("steps", "description")}
        if d <= to:
            w["done"] = evaluate(planned[d], by_day.get(d, []), laps)
        workouts.append(w)

    # semanas reais
    weeks_act = {}
    for a in U.get("activities", []):
        if not a.get("is_run"): continue
        D = date.fromisoformat(a["date"]); m = (D - timedelta(D.weekday())).isoformat()
        if m < (date.fromisoformat(first_day) - timedelta(56)).isoformat(): continue
        x = weeks_act.setdefault(m, {"km": 0.0, "runs": 0, "long_km": 0.0})
        km = (a["distance_m"] or 0) / 1000; x["km"] = round(x["km"] + km, 1); x["runs"] += 1; x["long_km"] = round(max(x["long_km"], km), 1)

    # semanas do plano: assessoria quando houver treino publicado
    for w in macro["weeks"]:
        ws = [p for p in workouts if w["start"] <= p["date"] <= w["end"]]
        w["source"] = "assessoria" if ws else "projeção"
        if ws:
            w["km_projection"] = w["km"]
            w["km"] = round(sum(p["km"] for p in ws)); w["long_km"] = max(p["km"] for p in ws); w["coach_sessions"] = len(ws)

    # VDOT de hoje pela previsão de meia do Garmin
    perf = sorted([p for p in U.get("daily_performance", []) if p.get("race_pred_21k_s")], key=lambda p: p["date"])
    if perf:
        macro["reference"]["now_vdot"] = round(vdot_from(21097.5, perf[-1]["race_pred_21k_s"]), 1)
        macro["reference"]["now_hm_pred_s"] = perf[-1]["race_pred_21k_s"]

    macro["coach"] = {"source": "Calendário do Garmin (assessoria)", "read_at": to, "workouts": workouts,
                      "horizon_end": max([p["date"] for p in workouts if p["date"] >= to], default=None)}
    macro["actual"] = {"through": to, "weeks": weeks_act}
    macro.pop("weeks_note", None)

    # resumo para o relatório diário
    tmr = (T + timedelta(1)).isoformat()
    today_w = next((p for p in workouts if p["date"] == to), None)
    summary = {"tomorrow": next(({"date": p["date"], "text": p["text"], "km": p["km"], "min": p["min"], "type": p["type"]} for p in workouts if p["date"] == tmr), None),
               "today": ({"text": today_w["text"], "type": today_w["type"], "done": today_w.get("done")} if today_w else None),
               "horizon_end": macro["coach"]["horizon_end"]}
    wk = next((w for w in macro["weeks"] if w["start"] <= to <= w["end"]), None)
    if wk:
        summary["week"] = {"n": wk["n"], "planned_km": wk["km"], "source": wk["source"], "done_km": weeks_act.get(wk["start"], {}).get("km", 0)}
    stored = {d: planned[d] for d in planned}
    return macro, summary, stored

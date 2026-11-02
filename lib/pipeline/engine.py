"""Fase 3 — motor de análise. Lê o lote normalizado e calcula derived_metrics, alerts e relatórios.
Usage: python3 -I engine.py <norm_dir> <out_dir> [as_of YYYY-MM-DD]
Fórmulas documentadas em FORMULAS (exportado junto). Linguagem: insights de treino, não diagnóstico médico.
"""
import json, sys, os, math, hashlib, statistics as st
from datetime import date, timedelta

FORMULA_VERSION = "3.1.0"
HR_MAX, HR_REST, LT_HR = 183, 44, 167
GARMIN_Z_FLOORS = [92, 110, 128, 146, 165]   # zonas do relógio (método % FC máx) — só referência
# Base fisiológica: teste cardiopulmonar HCor, 13/02/2026 (protocolo rampa em esteira, 1% de inclinação)
CPET = {"date": "2026-02-13", "vo2max_mlkgmin": 57.4, "vo2max_lmin": 4.53, "hr_peak": 180, "hr_rest_clinic": 61,
        "lv1": {"hr": 133, "speed_kmh": 11.1, "vo2_mlkgmin": 32.4, "pct_vo2max": 57},
        "lv2": {"hr": 162, "speed_kmh": 16.3, "vo2_mlkgmin": 46.4, "pct_vo2max": 81},
        "o2_pulse_ml": 25, "ve_vco2_slope": 27.91, "oues": 3918, "weight_kg_implied": round(4.53 / 57.4 * 1000, 1)}
LV1, LV2 = CPET["lv1"]["hr"], CPET["lv2"]["hr"]
Z_FLOORS = [123, 133, 148, 162]                # zonas do teste: Z1 < LV1−10 · Z2 até LV1 · Z3 LV1–148 · Z4 148–LV2 · Z5 ≥ LV2
ZONE_NAMES = ["Z1 regenerativo (<123)", "Z2 aeróbico (123–132)", "Z3 acima de LV1 (133–147)", "Z4 sub-limiar (148–161)", "Z5 acima de LV2 (≥162)"]
EASY_HR_MAX = LV1 + 5                          # corrida "fácil": FC média ≤ LV1 + 5 bpm (a FC sobe ~5 bpm no estado estável vs rampa)
LONG_RUN_KM, LONG_RUN_MIN = 18, 90
SLEEP_NEED_S = 7.5 * 3600                      # necessidade fixa (a do Garmin, ~8,35 h, dispararia alerta em 83% dos dias)

FORMULAS = {
    "load_day": "Σ activityTrainingLoad (EPOC do Garmin) de todas as atividades do dia",
    "km_week_iso / time_week_iso_s": "Σ distância/tempo das corridas na semana ISO (seg–dom)",
    "km_wow_pct": "(km semana ISO anterior completa / km da semana antes dela − 1) × 100; alerta > +15%",
    "acwr": "carga 7 d / (carga 28 d / 4). Atenção > 1,3; perigo > 1,5; destreino < 0,8 (só se carga crônica > 0)",
    "ctl / atl / tsb": "EWMA da carga diária: CTL τ=42 d, ATL τ=7 d (k=1−e^(−1/τ)); TSB(d) = CTL(d−1) − ATL(d−1); tsb_pct_ctl = TSB/CTL×100 (a carga EPOC do Garmin não está na escala do TSS)",
    "monotony_7d / strain_7d": "Foster: monotonia = média(carga diária 7 d)/desvio-padrão; strain = carga semanal × monotonia",
    "zone_dist_28d": "zonas do teste cardiopulmonar (13/02/2026) a partir do stream de FC: baixa = abaixo de LV1 (< 133), moderada = entre LV1 e LV2 (133–161), alta = acima de LV2 (≥ 162); corridas de 28 d; meta ~80% abaixo de LV1",
    "cpet_bands": "pace nas bandas de FC do teste: mediana da velocidade em janelas de 30 s estáveis (FC ± 5 bpm de LV1 = 133 ou LV2 = 162, após 10 min, amplitude de FC ≤ 8, rampa ≤ 3%, ≥ 120 s por corrida); 28 d = média das corridas ponderada pelo tempo na banda",
    "cpet_baseline": "base do relógio = mesmas métricas nos 28 d terminados em 13/02/2026 (data do teste); evolução = 28 d atuais vs base",
    "long_run_share_7d_pct": "km de corridas ≥ 18 km ou ≥ 90 min / km da semana",
    "dynamics_zscores": "z = (valor da última corrida − média 30 d) / dp 30 d para cadência, GCT, balanço e oscilação; alerta |z| > 2",
    "hrv_7d_vs_60d_pct": "(média HRV noturna 7 d / média 60 d − 1) × 100; alerta se 7 d abaixo da faixa (baseline Garmin) ≥ 3 dias ou |Δ| > 15%",
    "rhr_vs_30d_median_bpm": "FC repouso do dia − mediana de 30 d (exclui o dia); alerta se ≥ +5 bpm por ≥ 2 dias seguidos",
    "sleep_debt_7d_s": "Σ max(0, 7,5 h − sono) nos últimos 7 dias; atenção > 5 h, perigo > 10 h; curto = < 6 h em 2 noites seguidas",
    "recovery_combo": "HRV abaixo da faixa ou −10% vs 60 d + FC repouso ≥ +5 + sono < 6 h ou score < 60 no mesmo dia → perigo",
    "ef_easy_28d": "eficiência aeróbica = velocidade (m/min) / FC média nas corridas fáceis (FC ≤ 138 = LV1 + 5, ≥ 5 km, sem rótulo VO2/limiar/anaeróbico), média 28 d",
    "decoupling_last_long_pct": "desacoplamento pace:FC da última corrida longa (10%–95% do tempo, em movimento): (EF 1ª metade − EF 2ª)/EF 1ª × 100; < 5% = boa base aeróbica",
    "vdot": "Daniels: VO2 = −4,60 + 0,182258v + 0,000104v² (v m/min); %máx = 0,8 + 0,1894393e^(−0,012778t) + 0,2989558e^(−0,1932605t); VDOT = VO2/%máx. VDOT atual = maior VDOT de corridas ≥ 5 km e ≥ 15 min nos últimos 120 dias (subestima se não houve esforço máximo)",
    "riegel": "t2 = t1 × (d2/d1)^1,06, a partir do melhor esforço de 120 d",
    "predictions": "garmin (Race Predictor; 22K via Riegel a partir da meia), vdot, riegel; blended = média das disponíveis; gap_s_per_km = (blended − meta)/km",
    "readiness_score": "0–100 = 25% forma (100 − |TSB%CTL − 10| × 1,6) + 25% HRV (100 dentro da faixa; −4 pts por 1% abaixo do limite inferior) + 20% sono 14 d (½ score médio + 50 × horas/7,5 h) + 15% consistência (semanas com ≥ 4 corridas nas últimas 6) + 15% previsão vs meta da próxima prova (100 se gap ≤ 0; 0 se ≥ +15 s/km)",
}


def D(s): return date.fromisoformat(s)
def iso(d): return d.isoformat()
def mean(x): x = [v for v in x if v is not None]; return sum(x) / len(x) if x else None
def sd(x): x = [v for v in x if v is not None]; return st.pstdev(x) if len(x) > 1 else None
def clamp(v, a=0, b=100): return max(a, min(b, v))
def fmt_t(s):
    if s is None: return "–"
    s = int(round(s)); h, m, ss = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m}:{ss:02d}"
def fmt_p(s): return "–" if s is None else f"{int(s // 60)}:{int(round(s % 60)):02d}/km"


def vdot_from(dist_m, t_s):
    t = t_s / 60; v = dist_m / t
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v
    pct = 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)
    return vo2 / pct


def time_from_vdot(vdot, dist_m):
    lo, hi = 60, 6 * 3600
    for _ in range(60):
        mid = (lo + hi) / 2
        if vdot_from(dist_m, mid) > vdot: lo = mid
        else: hi = mid
    return (lo + hi) / 2


def cpet_window(runs, C, W, P, LTH, end_day, days=28):
    """Métricas do relógio comparáveis ao teste, na janela de `days` dias terminada em end_day."""
    e = D(end_day); s = iso(e - timedelta(days - 1))
    rr = [a for a in runs if s <= a["date"] <= end_day]
    def band(k):
        pts = [(C[a["activity_id"]][k]["speed_med_mps"], C[a["activity_id"]][k]["secs"]) for a in rr
               if a["activity_id"] in C and C[a["activity_id"]][k]["speed_med_mps"]]
        tot = sum(w for _, w in pts)
        if tot < 300: return None
        v = sum(x * w for x, w in pts) / tot
        return {"pace_s_per_km": round(1000 / v, 1), "speed_kmh": round(v * 3.6, 2), "runs": len(pts), "secs": tot}
    ww = [W.get(iso(e - timedelta(x)), {}) for x in range(days)]
    easy = [a for a in rr if a["hr_avg"] and a["hr_avg"] <= EASY_HR_MAX and a["distance_m"] >= 5000 and a["te_label"] not in ("VO2MAX", "LACTATE_THRESHOLD", "ANAEROBIC_CAPACITY")]
    pe = P.get(end_day) or {}
    vo2 = pe.get("vo2max")
    if vo2 is None:
        prev = [P[d]["vo2max"] for d in sorted(P) if d <= end_day and P[d].get("vo2max") is not None]
        vo2 = prev[-1] if prev else None
    lt = [x for x in LTH if x["date"] <= end_day]
    lt = lt[-1] if lt else None
    rhr, hrv = mean([w.get("rhr_bpm") for w in ww]), mean([w.get("hrv_night_ms") for w in ww])
    ef = mean([(a["distance_m"] / (a["moving_s"] or a["duration_s"]) * 60) / a["hr_avg"] for a in easy])
    return {"from": s, "to": end_day, "lv1_band": band("lv1"), "lv2_band": band("lv2"), "vo2max_garmin": vo2,
            "lt_garmin": lt and {"date": lt["date"], "hr": lt["lt_hr"], "pace_s_per_km": lt.get("pace_s_per_km")},
            "rhr_avg": rhr and round(rhr, 1), "hrv_avg": hrv and round(hrv, 1), "ef_easy": ef and round(ef, 4),
            "km": round(sum(a["distance_m"] for a in rr) / 1000, 1), "runs": len(rr)}


def cpet_block(runs, C, W, P, LTH, rows, as_of):
    base = cpet_window(runs, C, W, P, LTH, CPET["date"]); cur = cpet_window(runs, C, W, P, LTH, as_of)
    def dlt(k, sub=None):
        b, c = base.get(k), cur.get(k)
        if sub: b, c = b and b.get(sub), c and c.get(sub)
        return None if b is None or c is None else round(c - b, 2)
    deltas = {"lv1_pace_s": dlt("lv1_band", "pace_s_per_km"), "lv2_pace_s": dlt("lv2_band", "pace_s_per_km"), "vo2max": dlt("vo2max_garmin"),
              "rhr_bpm": dlt("rhr_avg"), "hrv_ms": dlt("hrv_avg"), "ef_easy_pct": round((cur["ef_easy"] / base["ef_easy"] - 1) * 100, 1) if cur["ef_easy"] and base["ef_easy"] else None,
              "lt_pace_s": (cur["lt_garmin"]["pace_s_per_km"] - base["lt_garmin"]["pace_s_per_km"]) if cur["lt_garmin"] and base["lt_garmin"] and cur["lt_garmin"]["pace_s_per_km"] and base["lt_garmin"]["pace_s_per_km"] else None}
    series = []
    d = D(as_of)
    while iso(d) >= "2025-01-01":
        w = cpet_window(runs, C, W, P, LTH, iso(d))
        series.append({"date": iso(d), "lv1_pace_s": w["lv1_band"] and w["lv1_band"]["pace_s_per_km"], "lv2_pace_s": w["lv2_band"] and w["lv2_band"]["pace_s_per_km"],
                       "vo2max": w["vo2max_garmin"], "lt_pace_s": w["lt_garmin"] and w["lt_garmin"]["pace_s_per_km"], "lt_hr": w["lt_garmin"] and w["lt_garmin"]["hr"],
                       "rhr": w["rhr_avg"], "hrv": w["hrv_avg"], "ef_easy": w["ef_easy"], "km_28d": w["km"]})
        d -= timedelta(7)
    series.reverse()
    zd = rows[-1].get("zone_dist_28d")
    lines = []
    if cur["lv1_band"] and base["lv1_band"]:
        x = deltas["lv1_pace_s"]
        lines.append(f"Na FC de LV1 ({LV1} bpm) você corre a {fmt_p(cur['lv1_band']['pace_s_per_km'])} (28 d) vs {fmt_p(base['lv1_band']['pace_s_per_km'])} na época do teste: {abs(x):.0f} s/km {'mais rápido' if x < 0 else 'mais lento'}.")
    if cur["lv2_band"] and base["lv2_band"]:
        x = deltas["lv2_pace_s"]
        lines.append(f"Na FC de LV2 ({LV2} bpm): {fmt_p(cur['lv2_band']['pace_s_per_km'])} vs {fmt_p(base['lv2_band']['pace_s_per_km'])} na base ({abs(x):.0f} s/km {'mais rápido' if x < 0 else 'mais lento'}).")
    if cur["vo2max_garmin"] is not None:
        lines.append(f"VO2max do relógio {cur['vo2max_garmin']:.1f} vs {CPET['vo2max_mlkgmin']} medido no teste (relógio na data do teste: {base['vo2max_garmin']:.1f}).")
    if zd: lines.append(f"Distribuição 28 d pelas zonas do teste: {zd['below_lv1_pct']:.0f}% abaixo de LV1 · {zd['lv1_lv2_pct']:.0f}% entre LV1 e LV2 · {zd['above_lv2_pct']:.0f}% acima de LV2.")
    zones = [{"zone": i + 1, "name": ZONE_NAMES[i], "from_bpm": ([0] + Z_FLOORS)[i], "to_bpm": (Z_FLOORS + [None])[i] and (Z_FLOORS + [None])[i] - 1} for i in range(5)]
    return {"test": CPET, "zones": zones, "baseline_garmin": base, "current": cur, "deltas": deltas, "series": series, "lines": lines,
            "notes": ["Velocidades do teste vêm de rampa em esteira (+1 km/h por minuto): costumam ficar acima do pace sustentável na rua para a mesma FC. A comparação de evolução usa a base do próprio relógio em fev/2026.",
                      f"O teste implica {CPET['weight_kg_implied']} kg (4,53 L/min ÷ 57,4 mL/kg/min); o peso no Garmin é 72,8 kg, definido em 2024."]}


def main(nd, out, as_of=None):
    A = json.load(open(f"{nd}/activities.json")); DT = {d["activity_id"]: d for d in json.load(open(f"{nd}/activity_details.json"))}
    W = {w["date"]: w for w in json.load(open(f"{nd}/daily_wellness.json"))}
    P = {p["date"]: p for p in json.load(open(f"{nd}/daily_performance.json"))}
    G = json.load(open(f"{nd}/gear.json")); R = [r for r in json.load(open(f"{nd}/races_and_goals.json")) if r.get("status") != "cancelled"]  # provas canceladas saem da prontidão e das previsões
    C = {c["activity_id"]: c for c in (json.load(open(f"{nd}/activity_cpet.json")) if os.path.exists(f"{nd}/activity_cpet.json") else [])}
    LTH = json.load(open(f"{nd}/lactate_threshold_history.json")) if os.path.exists(f"{nd}/lactate_threshold_history.json") else []
    as_of = as_of or max(W)
    start = D("2023-01-01"); end = D(as_of); first_out = D(min(W))
    days = [iso(start + timedelta(i)) for i in range((end - start).days + 1)]
    runs = [a for a in A if a["is_run"] and a["date"] >= "2022-06-01" and a["pace_s_per_km"] and a["pace_s_per_km"] < 600]
    by_day_load, by_day_runs = {}, {}
    for a in A:
        if a["date"] >= "2022-06-01": by_day_load[a["date"]] = by_day_load.get(a["date"], 0) + (a["training_load"] or 0)
    for a in runs: by_day_runs.setdefault(a["date"], []).append(a)

    # ---------- séries diárias ----------
    ctl = atl = 0.0; k42, k7 = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)
    rows, prev = [], {"ctl": 0, "atl": 0}
    load_hist = []
    alerts = {}
    sleep_short_streak = rhr_streak = hrv_below_streak = 0

    def add_alert(day, typ, sev, msg, ev, subject=""):
        aid = hashlib.sha1(f"{day}|{typ}|{subject}".encode()).hexdigest()[:12]
        alerts[aid] = {"alert_id": aid, "date": day, "type": typ, "severity": sev, "message": msg, "evidence": ev, "subject": subject}

    for day in days:
        d = D(day); L = by_day_load.get(day, 0.0); load_hist.append(L)
        tsb = prev["ctl"] - prev["atl"]; prev_ctl = prev["ctl"]
        ctl = ctl + k42 * (L - ctl); atl = atl + k7 * (L - atl)
        prev = {"ctl": ctl, "atl": atl}
        if d < first_out: continue
        l7, l28 = sum(load_hist[-7:]), sum(load_hist[-28:])
        acwr = l7 / (l28 / 4) if l28 > 0 else None
        m7, s7 = mean(load_hist[-7:]), sd(load_hist[-7:])
        monotony = (m7 / s7) if s7 else None
        wk_start = d - timedelta(d.weekday())
        rw = [a for x in range(7) for a in by_day_runs.get(iso(wk_start + timedelta(x)), []) if wk_start + timedelta(x) <= d]
        km_week = sum(a["distance_m"] for a in rw) / 1000; time_week = sum(a["duration_s"] for a in rw)
        def km_iso_week(ws): return sum(a["distance_m"] for x in range(7) for a in by_day_runs.get(iso(ws + timedelta(x)), [])) / 1000
        pw1, pw2 = km_iso_week(wk_start - timedelta(7)), km_iso_week(wk_start - timedelta(14))
        wow = (pw1 / pw2 - 1) * 100 if pw2 > 5 else None
        r7 = [a for x in range(7) for a in by_day_runs.get(iso(d - timedelta(x)), [])]
        km7 = sum(a["distance_m"] for a in r7) / 1000
        long7 = sum(a["distance_m"] for a in r7 if a["distance_m"] >= LONG_RUN_KM * 1000 or a["duration_s"] >= LONG_RUN_MIN * 60) / 1000
        r28 = [a for x in range(28) for a in by_day_runs.get(iso(d - timedelta(x)), [])]
        zs = [0] * 5; ncp = 0
        for a in r28:
            c = C.get(a["activity_id"])
            if c: ncp += 1; zs = [x + y for x, y in zip(zs, c["zone_secs"])]
        zt = sum(zs)
        zone = {"below_lv1_pct": round((zs[0] + zs[1]) / zt * 100, 1), "lv1_lv2_pct": round((zs[2] + zs[3]) / zt * 100, 1), "above_lv2_pct": round(zs[4] / zt * 100, 1),
                "zones_pct": [round(x / zt * 100, 1) for x in zs], "runs_covered": ncp, "runs_total": len(r28), "basis": "cpet_2026-02-13"} if zt else None
        easy = [a for a in r28 if a["hr_avg"] and a["hr_avg"] < EASY_HR_MAX and a["distance_m"] >= 5000 and a["te_label"] not in ("VO2MAX", "LACTATE_THRESHOLD", "ANAEROBIC_CAPACITY")]
        ef = mean([(a["distance_m"] / (a["moving_s"] or a["duration_s"]) * 60) / a["hr_avg"] for a in easy])
        longs = sorted([a for a in runs if a["date"] <= day and a["distance_m"] >= LONG_RUN_KM * 1000 and (DT.get(a["activity_id"]) or {}).get("drift") and a["te_label"] in ("AEROBIC_BASE", "TEMPO", "RECOVERY")], key=lambda a: a["start_local"])
        last_long = longs[-1] if longs and (d - D(longs[-1]["date"])).days <= 21 else None
        # dinâmica: última corrida do dia vs 30 d
        dyn = None
        if by_day_runs.get(day):
            a = max(by_day_runs[day], key=lambda x: x["distance_m"])
            hist = [b for x in range(1, 31) for b in by_day_runs.get(iso(d - timedelta(x)), [])]
            dyn = {}
            for f in ["cadence_avg_spm", "gct_ms", "gct_balance_left_pct", "vert_osc_cm"]:
                mu, sg = mean([b[f] for b in hist]), sd([b[f] for b in hist])
                if a[f] is not None and mu is not None and sg and len([b for b in hist if b[f] is not None]) >= 6: dyn[f] = round((a[f] - mu) / sg, 2)
        # recuperação
        w = W.get(day, {})
        hrv_days = [W.get(iso(d - timedelta(x)), {}).get("hrv_night_ms") for x in range(60)]
        h7, h60 = mean(hrv_days[:7]), mean(hrv_days)
        hrv_pct = (h7 / h60 - 1) * 100 if h7 and h60 else None
        below = w.get("hrv_7d_ms") is not None and w.get("hrv_baseline_low_ms") is not None and w["hrv_7d_ms"] < w["hrv_baseline_low_ms"]
        hrv_below_streak = hrv_below_streak + 1 if below else 0
        rhr30 = [W.get(iso(d - timedelta(x)), {}).get("rhr_bpm") for x in range(1, 31)]
        med = st.median([v for v in rhr30 if v]) if any(rhr30) else None
        rhr_delta = (w["rhr_bpm"] - med) if w.get("rhr_bpm") and med else None
        rhr_streak = rhr_streak + 1 if rhr_delta is not None and rhr_delta >= 5 else 0
        sl = [W.get(iso(d - timedelta(x)), {}) for x in range(7)]
        debt = sum(max(0, SLEEP_NEED_S - s["sleep_s"]) for s in sl if s.get("sleep_s") is not None)
        short = w.get("sleep_s") is not None and w["sleep_s"] < 6 * 3600
        sleep_short_streak = sleep_short_streak + 1 if short else 0

        row = {"date": day, "load_day": round(L, 1), "km_7d": round(km7, 1), "km_week_iso": round(km_week, 1), "time_week_iso_s": int(time_week),
               "km_prev_week": round(pw1, 1), "km_wow_pct": round(wow, 1) if wow is not None else None,
               "acute_load_7d": round(l7), "chronic_load_28d": round(l28 / 4), "acwr": round(acwr, 2) if acwr else None,
               "ctl": round(ctl, 1), "atl": round(atl, 1), "tsb": round(tsb, 1), "tsb_pct_ctl": round(tsb / prev_ctl * 100, 1) if prev_ctl else None,
               "monotony_7d": round(monotony, 2) if monotony else None, "strain_7d": round(l7 * monotony) if monotony else None,
               "zone_dist_28d": zone, "long_run_share_7d_pct": round(long7 / km7 * 100, 1) if km7 else None,
               "hrv_7d_ms": round(h7, 1) if h7 else None, "hrv_60d_ms": round(h60, 1) if h60 else None, "hrv_7d_vs_60d_pct": round(hrv_pct, 1) if hrv_pct is not None else None,
               "hrv_below_band_days": hrv_below_streak, "rhr_vs_30d_median_bpm": rhr_delta, "rhr_elevated_days": rhr_streak,
               "sleep_debt_7d_s": int(debt), "short_sleep_streak": sleep_short_streak,
               "ef_easy_28d": round(ef, 4) if ef else None, "decoupling_last_long_pct": (DT[last_long["activity_id"]]["drift"]["pa_hr_decoupling_pct"] if last_long else None),
               "last_long_run": {"date": last_long["date"], "km": round(last_long["distance_m"] / 1000, 1)} if last_long else None,
               "dynamics_zscores": dyn or None, "formula_version": FORMULA_VERSION}
        rows.append(row)

        # ---------- alertas ----------
        if wow is not None and wow > 15 and d.weekday() == 0:
            add_alert(day, "volume_spike", "attention", f"Volume da semana passada subiu {wow:.0f}% ({pw2:.0f} → {pw1:.0f} km).", {"km_prev": round(pw1, 1), "km_before": round(pw2, 1), "wow_pct": round(wow, 1), "threshold": 15})
        if acwr and l28 / 4 > 150:
            if acwr > 1.5: add_alert(day, "acwr_high", "danger", f"ACWR {acwr:.2f}: carga aguda muito acima da crônica.", {"acwr": round(acwr, 2), "threshold": 1.5})
            elif acwr > 1.3: add_alert(day, "acwr_high", "attention", f"ACWR {acwr:.2f}: carga subindo rápido.", {"acwr": round(acwr, 2), "threshold": 1.3})
            elif acwr < 0.8: add_alert(day, "acwr_low", "info", f"ACWR {acwr:.2f}: carga recente bem abaixo do habitual (destreino ou polimento).", {"acwr": round(acwr, 2), "threshold": 0.8})
        if hrv_below_streak >= 3:
            add_alert(day, "hrv_drop", "attention", f"HRV 7 d ({w['hrv_7d_ms']} ms) abaixo da sua faixa ({w['hrv_baseline_low_ms']}–{w['hrv_baseline_high_ms']}) há {hrv_below_streak} dias.", {"hrv_7d": w["hrv_7d_ms"], "band_low": w["hrv_baseline_low_ms"], "days": hrv_below_streak})
        elif hrv_pct is not None and abs(hrv_pct) > 15:
            add_alert(day, "hrv_drop" if hrv_pct < 0 else "hrv_shift", "attention" if hrv_pct < 0 else "info", f"HRV 7 d {hrv_pct:+.0f}% vs média de 60 d.", {"hrv_7d": round(h7, 1), "hrv_60d": round(h60, 1), "pct": round(hrv_pct, 1), "threshold": 15})
        if rhr_streak >= 2:
            add_alert(day, "rhr_elevated", "attention", f"FC de repouso {w['rhr_bpm']} bpm (+{rhr_delta:.0f} vs mediana de 30 d) há {rhr_streak} dias.", {"rhr": w["rhr_bpm"], "median_30d": med, "days": rhr_streak, "threshold": 5})
        if sleep_short_streak >= 2:
            add_alert(day, "sleep_short", "attention", f"{sleep_short_streak} noites seguidas com menos de 6 h de sono.", {"sleep_h": round(w["sleep_s"] / 3600, 2), "streak": sleep_short_streak})
        if debt > 5 * 3600:
            add_alert(day, "sleep_debt", "attention" if debt < 10 * 3600 else "danger", f"Débito de sono de {debt / 3600:.1f} h nos últimos 7 dias.", {"debt_h": round(debt / 3600, 1), "threshold_h": 5})
        poor_sleep = (w.get("sleep_s") is not None and w["sleep_s"] < 6 * 3600) or (w.get("sleep_score") is not None and w["sleep_score"] < 60)
        hrv_low = below or (hrv_pct is not None and hrv_pct < -10)
        if hrv_low and rhr_delta is not None and rhr_delta >= 5 and poor_sleep:
            add_alert(day, "recovery_combo", "danger", "HRV baixa + FC de repouso elevada + sono ruim: sinal forte de recuperação incompleta (excesso de carga ou início de doença). Priorize descanso.", {"hrv_pct": hrv_pct and round(hrv_pct, 1), "rhr_delta": rhr_delta, "sleep_h": w.get("sleep_s") and round(w["sleep_s"] / 3600, 2), "sleep_score": w.get("sleep_score")})
        if dyn:
            for f, z in dyn.items():
                if abs(z) > 2: add_alert(day, "dynamics_shift", "info", f"Dinâmica de corrida: {f} com z = {z:+.1f} vs 30 d (pode refletir ritmo do treino, tênis ou fadiga).", {"field": f, "z": z}, subject=f)

    # ---------- dias de alerta consecutivos / médico ----------
    al = sorted(alerts.values(), key=lambda a: (a["date"], a["type"]))
    run_len = {}
    for a in al:
        k = (a["type"], a["subject"]); pd = run_len.get(k)
        if pd and (D(a["date"]) - D(pd[0])).days == 1: run_len[k] = (a["date"], pd[1] + 1, pd[2])
        else: run_len[k] = (a["date"], 1, a["date"])
        a["days_active"] = run_len[k][1]; a["first_seen"] = run_len[k][2]
        a["status"] = "open" if a["date"] == as_of else "resolved"
        a["suggest_doctor"] = a["type"] in ("rhr_elevated", "hrv_drop", "recovery_combo") and a["days_active"] >= 5
        if a["suggest_doctor"]: a["message"] += " Anomalia persistente há 5+ dias: vale procurar um médico."
    # alerta de tênis (estado atual)
    for g in G:
        if g["status"] == "ACTIVE" and (g.get("type") in (None, "SHOES")) and (g["km_garmin"] or 0) > 600:
            add_alert(as_of, "shoe_mileage", "attention" if g["km_garmin"] < 700 else "danger", f"Tênis {g['name']} com {g['km_garmin']:.0f} km.", {"km": g["km_garmin"], "threshold": 600}, subject=g["gear_id"])

    # ---------- performance & previsões ----------
    cut = iso(end - timedelta(120))
    cand = [a for a in runs if cut <= a["date"] <= as_of and a["distance_m"] >= 5000 and a["duration_s"] >= 900]
    best = max(cand, key=lambda a: vdot_from(a["distance_m"], a["moving_s"] or a["duration_s"])) if cand else None
    vdot_now = vdot_from(best["distance_m"], best["moving_s"] or best["duration_s"]) if best else None
    vdot_series = []
    for x in range(0, 365, 7):
        dd = end - timedelta(x); c = [a for a in runs if iso(dd - timedelta(120)) <= a["date"] <= iso(dd) and a["distance_m"] >= 5000 and a["duration_s"] >= 900]
        if c: vdot_series.append({"date": iso(dd), "vdot": round(max(vdot_from(a["distance_m"], a["moving_s"] or a["duration_s"]) for a in c), 1)})
    pnow = P.get(as_of) or P[max(P)]
    races_out = {}
    for r in R:
        dist = r["distance_m"]; g = None
        if pnow.get("race_pred_21k_s") and abs(dist - 21097.5) < 1500: g = pnow["race_pred_21k_s"] * (dist / 21097.5) ** 1.06
        elif pnow.get("race_pred_42k_s") and dist > 40000: g = pnow["race_pred_42k_s"]
        v = time_from_vdot(vdot_now, dist) if vdot_now else None
        rg = (best["moving_s"] or best["duration_s"]) * (dist / best["distance_m"]) ** 1.06 if best else None
        parts = [x for x in [g, v, rg] if x]; bl = sum(parts) / len(parts) if parts else None
        km = dist / 1000
        races_out[r["race_id"]] = {"name": r["name"], "date": r.get("date"), "target_s": r["target_s"], "garmin_s": g and round(g), "vdot_s": v and round(v), "riegel_s": rg and round(rg),
                                   "blended_s": bl and round(bl), "gap_s_per_km": bl and round((bl - r["target_s"]) / km, 1), "target_pace": fmt_p(r["target_s"] / km), "blended_pace": bl and fmt_p(bl / km),
                                   "days_to_race": (D(r["date"]) - end).days if r.get("date") else None}

    # ---------- prontidão ----------
    today = rows[-1]; w = W.get(as_of, {})
    nxt = min([r for r in R if r.get("date") and r["date"] >= as_of], key=lambda r: r["date"], default=None)
    tsb_s = clamp(100 - abs(today["tsb_pct_ctl"] - 10) * 1.6) if today["tsb_pct_ctl"] is not None else None
    hrv_s = None
    if w.get("hrv_7d_ms") and w.get("hrv_baseline_low_ms"):
        lo, hi = w["hrv_baseline_low_ms"], w["hrv_baseline_high_ms"] or w["hrv_baseline_low_ms"] * 1.3
        hrv_s = 100 if lo <= w["hrv_7d_ms"] <= hi * 1.1 else clamp(100 - abs(w["hrv_7d_ms"] - lo) / lo * 400)
    s14 = [W.get(iso(end - timedelta(x)), {}) for x in range(14)]
    sc = mean([s.get("sleep_score") for s in s14]); hr_ratio = mean([min(1.0, s["sleep_s"] / SLEEP_NEED_S) for s in s14 if s.get("sleep_s")])
    sleep_s = round(0.5 * sc + 50 * hr_ratio) if sc and hr_ratio else None
    wk = []
    for x in range(6):
        ws = end - timedelta(end.weekday()) - timedelta(7 * (x + 1))
        wk.append(sum(1 for y in range(7) for _ in by_day_runs.get(iso(ws + timedelta(y)), [])))
    cons_s = round(sum(min(1, n / 4) for n in wk) / 6 * 100)
    pred_s = None
    if nxt and races_out[nxt["race_id"]]["gap_s_per_km"] is not None:
        gp = races_out[nxt["race_id"]]["gap_s_per_km"]; pred_s = round(clamp(100 - max(0, gp) * (100 / 15)))
    comps = {"tsb": tsb_s and round(tsb_s), "hrv": hrv_s and round(hrv_s), "sleep_14d": sleep_s, "consistency_6w": cons_s, "prediction_vs_goal": pred_s}
    wts = {"tsb": .25, "hrv": .25, "sleep_14d": .20, "consistency_6w": .15, "prediction_vs_goal": .15}
    av = {k: v for k, v in comps.items() if v is not None}
    readiness = round(sum(av[k] * wts[k] for k in av) / sum(wts[k] for k in av)) if av else None
    today["readiness_score"] = readiness; today["readiness_components"] = comps
    today["vdot_current"] = vdot_now and round(vdot_now, 1); today["vdot_source"] = best and {"activity_id": best["activity_id"], "date": best["date"], "km": round(best["distance_m"] / 1000, 2), "time": fmt_t(best["moving_s"] or best["duration_s"])}
    today["predictions"] = races_out

    # ---------- textos ----------
    open_al = [a for a in alerts.values() if a["date"] == as_of]
    sev_rank = {"danger": 0, "attention": 1, "info": 2}
    open_al.sort(key=lambda a: sev_rank[a["severity"]])
    lr = next((by_day_runs[iso(end - timedelta(x))] for x in range(8) if by_day_runs.get(iso(end - timedelta(x)))), [])
    summary = []
    if lr:
        a = max(lr, key=lambda x: x["distance_m"]); summary.append(f"Última corrida ({a['date']}): {a['distance_m'] / 1000:.1f} km a {fmt_p(a['pace_s_per_km'])}, FC {a['hr_avg']}, carga {a['training_load']:.0f} ({(a['te_label'] or '').replace('_', ' ').lower()}).")
    summary.append(f"Semana ISO: {today['km_week_iso']:.0f} km até agora (semana passada {today['km_prev_week']:.0f} km). ACWR {today['acwr']}, forma (TSB) {today['tsb']:+.0f} ({today['tsb_pct_ctl']:+.0f}% do CTL), fitness (CTL) {today['ctl']:.0f}.")
    if w: summary.append(f"Recuperação: sono {w['sleep_s'] / 3600:.1f} h (score {w['sleep_score']}), HRV {w['hrv_night_ms']} ms ({ {"BALANCED": "equilibrada", "UNBALANCED": "desequilibrada", "LOW": "baixa", "POOR": "ruim"}.get(w['hrv_status'], "–") }), FC de repouso {w['rhr_bpm']} bpm.")
    if nxt:
        ro = races_out[nxt["race_id"]]; summary.append(f"{nxt['name']} em {ro['days_to_race']} dias: previsão combinada {fmt_t(ro['blended_s'])} ({ro['blended_pace']}) vs meta {fmt_t(nxt['target_s'])} ({ro['target_pace']}), gap {ro['gap_s_per_km']:+.1f} s/km.")
    summary.append(f"Prontidão para prova: {readiness}/100.")
    recs = []
    if any(a["severity"] == "danger" for a in open_al): recs.append("Troque o treino de qualidade de amanhã por regenerativo (30–40 min em Z1–Z2) ou descanso.")
    elif any(a["type"] in ("hrv_drop", "rhr_elevated", "sleep_short", "sleep_debt") for a in open_al): recs.append("Mantenha o treino de amanhã leve e priorize 8 h de sono antes de voltar à intensidade.")
    if today["acwr"] and today["acwr"] > 1.3: recs.append("Segure o volume desta semana: ACWR acima de 1,3.")
    if nxt and races_out[nxt["race_id"]]["days_to_race"] is not None and 0 < races_out[nxt["race_id"]]["days_to_race"] <= 14:
        recs.append(f"Faltam {races_out[nxt['race_id']]['days_to_race']} dias para {nxt['name']}: polimento. Reduza o volume ~30–40% e mantenha toques curtos no pace-alvo ({races_out[nxt['race_id']]['target_pace']}).")
    if zone and zone["below_lv1_pct"] < 70: recs.append(f"Só {zone['below_lv1_pct']:.0f}% do tempo abaixo de LV1 nos últimos 28 d ({zone['lv1_lv2_pct']:.0f}% entre LV1 e LV2): deixe os fáceis abaixo de {LV1} bpm.")
    if not recs: recs.append("Sem sinais de alerta: siga o plano previsto para amanhã.")
    report = {"date": as_of, "summary": summary[:5], "recommendations": recs[:3], "alerts_today": open_al, "readiness": {"score": readiness, "components": comps, "weights": wts}}
    # semanal (último domingo) e mensal (último dia 1)
    def period_summary(a, b, label):
        rr = [x for x in runs if a <= x["date"] <= b]; ww = [W[x] for x in W if a <= x <= b]
        return {"period": label, "from": a, "to": b, "runs": len(rr), "km": round(sum(x["distance_m"] for x in rr) / 1000, 1), "time_h": round(sum(x["duration_s"] for x in rr) / 3600, 1),
                "load": round(sum(by_day_load.get(x, 0) for x in W if a <= x <= b)), "long_run_km": round(max([x["distance_m"] for x in rr], default=0) / 1000, 1),
                "sleep_avg_h": mean([x["sleep_s"] / 3600 for x in ww if x.get("sleep_s")]) and round(mean([x["sleep_s"] / 3600 for x in ww if x.get("sleep_s")]), 2),
                "hrv_avg": mean([x.get("hrv_night_ms") for x in ww]) and round(mean([x.get("hrv_night_ms") for x in ww]), 1),
                "rhr_avg": mean([x.get("rhr_bpm") for x in ww]) and round(mean([x.get("rhr_bpm") for x in ww]), 1),
                "alerts": sum(1 for x in alerts.values() if a <= x["date"] <= b)}
    sun = end - timedelta((end.weekday() + 1) % 7)
    report["weekly"] = period_summary(iso(sun - timedelta(6)), iso(sun), "semana")
    m1 = end.replace(day=1); pm_end = m1 - timedelta(1)
    report["monthly"] = period_summary(iso(pm_end.replace(day=1)), iso(pm_end), "mês")
    report["vdot_series"] = vdot_series[::-1]

    report["cpet"] = cpet_block(runs, C, W, P, LTH, rows, as_of)

    os.makedirs(out, exist_ok=True)
    json.dump(rows, open(f"{out}/derived_metrics.json", "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(sorted(alerts.values(), key=lambda a: a["date"]), open(f"{out}/alerts.json", "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(report, open(f"{out}/daily_report.json", "w"), ensure_ascii=False, indent=1)
    json.dump({"formula_version": FORMULA_VERSION, "formulas": FORMULAS, "constants": {"HR_MAX": HR_MAX, "HR_REST": HR_REST, "LT_HR": LT_HR, "zone_floors": Z_FLOORS, "zone_names": ZONE_NAMES, "garmin_zone_floors": GARMIN_Z_FLOORS, "easy_hr_max": EASY_HR_MAX, "cpet": CPET}}, open(f"{out}/formulas.json", "w"), ensure_ascii=False, indent=1)
    return report, rows, alerts


if __name__ == "__main__":
    rep, rows, al = main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:5000])
    from collections import Counter
    print("alerts by type/severity:", Counter((a["type"], a["severity"]) for a in al.values()))

"""Normalize a Garmin backfill export ({key: raw}) into the Phase-1 schema + coverage/validation report.
Usage: python3 -I normalize.py <export.json> <out_dir>
"""
import json, sys, os, statistics
from datetime import date, datetime, timedelta, timezone

SCHEMA_VERSION = "1.1.0"
WELL_FROM, TO = "2025-04-06", "2026-10-06"


def drange(a, b):
    d, e = date.fromisoformat(a), date.fromisoformat(b)
    while d <= e:
        yield d.isoformat(); d += timedelta(days=1)


def r(x, n=1):
    return None if x is None else round(x, n)


def main(src, out, well_from=None, to=None, perf_from="2023-01-01"):
    global WELL_FROM, TO
    WELL_FROM = well_from or WELL_FROM; TO = to or TO
    raw = json.load(open(src))
    fetched = raw.get("__exported_at", datetime.now(timezone.utc).isoformat())
    by = lambda p: [v for k, v in sorted(raw.items()) if k.startswith(p) and not (isinstance(v, dict) and v.get("__empty"))]
    os.makedirs(out, exist_ok=True)
    issues = []

    # ---------- activities ----------
    dets = {v["activity_id"]: v for k, v in raw.items() if k.startswith("det|")}
    acts = []
    for a in raw["raw|activities"]:
        aid = a["activityId"]; d = dets.get(aid); s = (d or {}).get("summary") or {}
        spd = a.get("averageSpeed"); gap = s.get("avgGradeAdjustedSpeed") or a.get("avgGradeAdjustedSpeed")
        rpe = s.get("directWorkoutRpe", a.get("directWorkoutRpe"))
        acts.append({
            "activity_id": aid, "date": a["startTimeLocal"][:10], "start_local": a["startTimeLocal"], "start_gmt": a.get("startTimeGMT"),
            "name": a.get("activityName"), "sport": a.get("activityType"), "is_run": "running" in (a.get("activityType") or ""),
            "event_type": (d or {}).get("event_type") or a.get("eventType"),
            "distance_m": a.get("distance"), "duration_s": a.get("duration"), "moving_s": a.get("movingDuration"), "elapsed_s": a.get("elapsedDuration"),
            "pace_s_per_km": r(1000 / spd, 1) if spd else None, "gap_s_per_km": r(1000 / gap, 1) if gap else None,
            "hr_avg": a.get("averageHR") or None, "hr_max": a.get("maxHR") or None,
            "training_load": r(a.get("activityTrainingLoad"), 1), "aerobic_te": a.get("aerobicTrainingEffect"), "anaerobic_te": a.get("anaerobicTrainingEffect"),
            "te_label": s.get("trainingEffectLabel") or a.get("trainingEffectLabel"),
            "cadence_avg_spm": r(a.get("averageRunningCadenceInStepsPerMinute")), "stride_m": r(a["avgStrideLength"] / 100, 3) if a.get("avgStrideLength") else None,
            "gct_ms": r(a.get("avgGroundContactTime")), "gct_balance_left_pct": r(a.get("avgGroundContactBalance"), 2),
            "vert_osc_cm": r(a.get("avgVerticalOscillation"), 2), "vert_ratio_pct": r(a.get("avgVerticalRatio"), 2), "power_avg_w": a.get("avgPower"),
            "elev_gain_m": a.get("elevationGain"), "elev_loss_m": a.get("elevationLoss"), "calories": a.get("calories"), "vo2max_activity": a.get("vO2MaxValue"),
            "rpe": round(rpe / 10) if rpe else None, "feel": s.get("directWorkoutFeel", a.get("directWorkoutFeel")),
            "gear_ids": [g["uuid"] for g in (d or {}).get("gear", [])], "is_pr": bool(a.get("isPR")), "device_id": a.get("deviceId"),
            "has_details": d is not None, "source": "activitylist-service", "fetched_at": fetched, "revision": 1,
        })
    acts.sort(key=lambda x: x["start_local"])

    # ---------- activity_details ----------
    details = []
    for aid, d in dets.items():
        st = d.get("streams") or {}; s = d.get("summary") or {}
        laps = [{
            "lap_index": l.get("lapIndex"), "distance_m": l.get("distance"), "duration_s": l.get("duration"),
            "pace_s_per_km": r(1000 / l["averageSpeed"], 1) if l.get("averageSpeed") else None,
            "gap_s_per_km": r(1000 / l["avgGradeAdjustedSpeed"], 1) if l.get("avgGradeAdjustedSpeed") else None,
            "hr_avg": l.get("averageHR"), "hr_max": l.get("maxHR"), "cadence_spm": r(l.get("averageRunCadence")), "gct_ms": r(l.get("groundContactTime")),
            "vert_osc_cm": r(l.get("verticalOscillation"), 2), "stride_m": r(l["strideLength"] / 100, 3) if l.get("strideLength") else None,
            "power_w": l.get("averagePower"), "elev_gain_m": l.get("elevationGain"), "intensity_type": l.get("intensityType")} for l in d.get("laps", [])]
        kms = [dict(k, pace_s_per_km=k["duration_s"]) for k in st.get("km_splits", [])]
        details.append({
            "activity_id": aid,
            "hr_zones": [{"zone": z.get("zoneNumber"), "low_bpm": z.get("zoneLowBoundary"), "secs": z.get("secsInZone")} for z in d.get("hr_zones", [])],
            "laps": laps, "km_splits": kms,
            "dynamics_summary": {"cadence_spm": s.get("averageRunCadence"), "gct_ms": s.get("groundContactTime"), "gct_balance_left_pct": s.get("groundContactBalanceLeft"),
                                 "vert_osc_cm": s.get("verticalOscillation"), "vert_ratio_pct": s.get("verticalRatio"), "stride_m": r(s["strideLength"] / 100, 3) if s.get("strideLength") else None},
            "drift": st.get("drift"), "performance_condition": st.get("performance_condition"),
            "respiration_avg": s.get("avgRespirationRate"), "weather": None, "streams_ref": None, "n_points": st.get("n_points"),
            "source": "activity-service", "fetched_at": d.get("fetched_at"), "revision": 1,
        })

    # ---------- activity_cpet (zonas do teste cardiopulmonar, calculadas no coletor a partir do stream de FC) ----------
    date_of = {x["activity_id"]: x["date"] for x in acts}
    cpet = [{"activity_id": aid, "date": date_of.get(aid), **{k: (d.get("streams") or {})["cpet"][k] for k in ("zone_secs", "lv1", "lv2")},
             "source": "activity-service/details", "revision": 1}
            for aid, d in dets.items() if (d.get("streams") or {}).get("cpet")]

    # ---------- daily_wellness ----------
    W = {dt: {"date": dt} for dt in drange(WELL_FROM, TO)}
    for blk in by("raw|sleep|"):
        for it in blk.get("individualStats", []):
            dt = it.get("calendarDate"); v = it.get("values") or {}
            if dt not in W: continue
            W[dt].update({
                "sleep_s": v.get("totalSleepTimeInSeconds"), "sleep_deep_s": v.get("deepTime"), "sleep_light_s": v.get("lightTime"), "sleep_rem_s": v.get("remTime"),
                "sleep_awake_s": v.get("awakeTime"), "sleep_score": v.get("sleepScore"),
                "sleep_need_s": (v["sleepNeed"] * 60 if isinstance(v.get("sleepNeed"), (int, float)) and v["sleepNeed"] < 2000 else v.get("sleepNeed")),
                "sleep_start_ms": v.get("localSleepStartTimeInMillis"), "sleep_end_ms": v.get("localSleepEndTimeInMillis"),
                "rhr_bpm": v.get("restingHeartRate"), "resp_sleep_avg": v.get("respiration"), "spo2_sleep_avg": v.get("spO2"),
                "skin_temp_dev_c": v.get("skinTempC"), "bb_sleep_change": v.get("bodyBatteryChange"),
                "_hrv_sleep": v.get("avgOvernightHrv"), "_hrv7_sleep": v.get("hrv7dAverage")})
    for blk in by("raw|hrv|"):
        for h in blk.get("hrvSummaries", []):
            dt = h.get("calendarDate"); b = h.get("baseline") or {}
            if dt not in W: W.setdefault("__hrv_hist", []).append(h); continue
            W[dt].update({"hrv_night_ms": h.get("lastNightAvg"), "hrv_7d_ms": h.get("weeklyAvg"), "hrv_baseline_low_ms": b.get("balancedLow"),
                          "hrv_baseline_high_ms": b.get("balancedUpper"), "hrv_status": h.get("status")})
    hrv_hist = W.pop("__hrv_hist", [])
    for blk in by("raw|rhr|"):
        for m in (blk.get("allMetrics", {}).get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE") or []):
            dt = m.get("calendarDate")
            if dt in W and W[dt].get("rhr_bpm") is None and m.get("value"): W[dt]["rhr_bpm"] = int(m["value"])
    for blk in by("raw|stress|"):
        for it in blk:
            dt = it.get("calendarDate"); v = it.get("values") or {}
            if dt in W: W[dt]["stress_avg"] = v.get("overallStressLevel") if (v.get("overallStressLevel") or -1) >= 0 else None
    for blk in by("raw|steps|"):
        for it in blk:
            if it.get("calendarDate") in W: W[it["calendarDate"]]["steps"] = it.get("totalSteps")
    for blk in by("raw|im|"):
        for it in blk:
            if it.get("calendarDate") in W:
                W[it["calendarDate"]].update({"intensity_min_moderate": it.get("moderateValue"), "intensity_min_vigorous": it.get("vigorousValue")})
    for blk in by("raw|bb|"):
        for it in blk:
            dt = it.get("date")
            if dt not in W: continue
            desc = [x.get("bodyBatteryValueDescriptorKey") for x in it.get("bodyBatteryValueDescriptorDTOList") or []]
            li = desc.index("bodyBatteryLevel") if "bodyBatteryLevel" in desc else (1 if desc else None)
            lv = [p[li] for p in it.get("bodyBatteryValuesArray") or [] if li is not None and len(p) > li and isinstance(p[li], (int, float))]
            W[dt].update({"bb_max": max(lv) if lv else None, "bb_min": min(lv) if lv else None, "bb_charged": it.get("charged"), "bb_drained": it.get("drained")})
    for dt, w in W.items():  # fill HRV from sleep stats when hrv-service missing
        if w.get("hrv_night_ms") is None and w.get("_hrv_sleep"): w["hrv_night_ms"] = w["_hrv_sleep"]
        if w.get("hrv_7d_ms") is None and w.get("_hrv7_sleep"): w["hrv_7d_ms"] = w["_hrv7_sleep"]
        w.pop("_hrv_sleep", None); w.pop("_hrv7_sleep", None)
        for k in ["sleep_s", "sleep_deep_s", "sleep_light_s", "sleep_rem_s", "sleep_awake_s", "sleep_score", "hrv_night_ms", "hrv_7d_ms", "hrv_baseline_low_ms", "hrv_baseline_high_ms",
                  "hrv_status", "rhr_bpm", "stress_avg", "bb_max", "bb_min", "bb_charged", "bb_drained", "resp_sleep_avg", "spo2_sleep_avg", "steps", "intensity_min_moderate", "intensity_min_vigorous"]:
            w.setdefault(k, None)
        w["weight_kg"] = None
        cov = lambda *ks: "ok" if all(w.get(k) is not None for k in ks) else ("partial" if any(w.get(k) is not None for k in ks) else "missing")
        w["coverage"] = {"sleep": cov("sleep_s", "sleep_score"), "hrv": cov("hrv_night_ms"), "rhr": cov("rhr_bpm"), "stress": cov("stress_avg"),
                         "body_battery": ("ok" if w.get("bb_max") is not None else "partial") if w.get("bb_charged") is not None else "missing", "steps": cov("steps"), "weight": "missing"}
        w.update({"source": ["sleep-service/stats", "hrv-service", "usersummary-service/stats", "bodyBattery/reports", "userstats-service"], "fetched_at": fetched, "revision": 1})
    wellness = [W[k] for k in sorted(W)]

    # ---------- daily_performance (2023→) ----------
    P = {}
    for blk in by("raw|maxmet|"):
        for m in blk:
            g = m.get("generic") or {}
            if g.get("calendarDate"): P.setdefault(g["calendarDate"], {})["vo2max"] = g.get("vo2MaxPreciseValue") or g.get("vo2MaxValue"); P[g["calendarDate"]]["fitness_age"] = g.get("fitnessAge")
    for blk in by("raw|racepred|"):
        for p in blk:
            dt = p.get("calendarDate")
            if dt: P.setdefault(dt, {}).update({"race_pred_5k_s": p.get("time5K"), "race_pred_10k_s": p.get("time10K"), "race_pred_21k_s": p.get("timeHalfMarathon"), "race_pred_42k_s": p.get("timeMarathon")})
    perf, last_vo2 = [], None
    for dt in drange(perf_from, TO):
        p = P.get(dt, {}); row = {"date": dt, **{k: p.get(k) for k in ["race_pred_5k_s", "race_pred_10k_s", "race_pred_21k_s", "race_pred_42k_s", "fitness_age"]}}
        if p.get("vo2max") is not None: last_vo2 = p["vo2max"]; row.update(vo2max=last_vo2, vo2max_is_carried=False)
        else: row.update(vo2max=last_vo2, vo2max_is_carried=last_vo2 is not None)
        row.update({"training_status": None, "garmin_acute_load": None, "garmin_chronic_load": None, "garmin_acwr": None, "load_focus": None,
                    "lactate_threshold_hr": None, "lactate_threshold_pace_s_per_km": None, "training_readiness": None, "endurance_score": None, "hill_score": None})
        row["coverage"] = {"vo2max": "ok" if not row["vo2max_is_carried"] and row["vo2max"] else ("partial" if row["vo2max"] else "missing"),
                           "race_predictor": "ok" if row["race_pred_21k_s"] else "missing", "training_status": "missing",
                           "training_readiness": "not_supported", "endurance_score": "not_supported", "hill_score": "not_supported"}
        row.update({"source": ["metrics-service/maxmet", "metrics-service/racepredictions"], "fetched_at": fetched, "revision": 1})
        perf.append(row)
    ts = raw.get(f"raw|tstatus|{TO}") or {}
    lts = (ts.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
    if lts:
        t0 = list(lts.values())[0]; a = t0.get("acuteTrainingLoadDTO") or {}
        tlb = list(((ts.get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap") or {}).values())
        tgt = next((p for p in perf if p["date"] == t0.get("calendarDate")), perf[-1])
        tgt.update({"training_status": t0.get("trainingStatusFeedbackPhrase") or t0.get("trainingStatus"), "garmin_acute_load": a.get("dailyTrainingLoadAcute"),
                    "garmin_chronic_load": a.get("dailyTrainingLoadChronic"), "garmin_acwr": a.get("dailyAcuteChronicWorkloadRatio")})
        if tlb: x = tlb[0]; tgt["load_focus"] = {"aerobic_low": x.get("monthlyLoadAerobicLow"), "aerobic_high": x.get("monthlyLoadAerobicHigh"), "anaerobic": x.get("monthlyLoadAnaerobic"),
                                                 "targets": {k: x.get(k) for k in x if "Target" in k}}
        tgt["coverage"]["training_status"] = "ok"
    lt = raw.get(f"raw|lactate|{TO}") or []
    lt = lt if isinstance(lt, list) else [lt]
    lt_hr = next((x.get("hearRate") or x.get("heartRate") for x in lt if (x.get("hearRate") or x.get("heartRate"))), None)
    lt_sp = next((x.get("speed") for x in lt if x.get("speed")), None)
    if lt_hr: perf[-1]["lactate_threshold_hr"] = lt_hr
    if lt_sp: perf[-1]["lactate_threshold_pace_s_per_km"] = r(1000 / (lt_sp * 10 if lt_sp < 1.5 else lt_sp), 1)
    fa = raw.get(f"raw|fitnessage|{TO}") or {}
    if fa.get("fitnessAge"): perf[-1]["fitness_age"] = r(fa["fitnessAge"], 1)
    hz = raw.get(f"raw|hrzones|{TO}") or []
    hrz = next((z for z in hz if z.get("sport") == "DEFAULT"), hz[0] if hz else {})

    # ---------- gear ----------
    gl = raw.get(f"raw|gear|{TO}") or []
    gl = gl if isinstance(gl, list) else gl.get("gearList", [])
    km_comp = {}
    for a in acts:
        for g in a["gear_ids"]: km_comp[g] = km_comp.get(g, 0) + (a["distance_m"] or 0) / 1000
    gear = [{"gear_id": g.get("uuid"), "name": g.get("displayName") or g.get("customMakeModel") or " ".join(filter(None, [g.get("brand"), g.get("model")])) or g.get("name"),
             "type": g.get("gearType") or g.get("gearTypeName"), "status": g.get("status") or g.get("gearStatusName"), "first_use": (g.get("firstUseDate") or g.get("dateBegin") or "")[:10] or None,
             "km_garmin": r((g.get("distanceUsedMeters") or 0) / 1000), "km_computed_18m": r(km_comp.get(g.get("uuid"), 0)),
             "max_km": r((g.get("maxUsageDistanceMeters") or 0) / 1000) or None} for g in gl]

    # ---------- races_and_goals ----------
    here = os.path.dirname(os.path.abspath(__file__))
    sp = next(p for p in [os.path.join(here, "fase1_esquema.json"), os.path.join(here, "..", "fase1_esquema.json")] if os.path.exists(p))
    schema = json.load(open(sp))
    races = schema["collections"]["races_and_goals"]["seed"]

    # ---------- validation ----------
    def bad(rows, field, lo, hi, label):
        for x in rows:
            v = x.get(field)
            if v is not None and not (lo <= v <= hi): issues.append({"collection": label, "key": x.get("date") or x.get("activity_id"), "field": field, "value": v, "rule": f"[{lo},{hi}]"})
    bad(acts, "hr_avg", 30, 220, "activities"); bad(acts, "hr_max", 30, 230, "activities")
    bad([a for a in acts if a["is_run"]], "pace_s_per_km", 150, 900, "activities")
    bad(wellness, "rhr_bpm", 30, 100, "daily_wellness"); bad(wellness, "sleep_s", 0, 14 * 3600, "daily_wellness")
    bad(wellness, "hrv_night_ms", 10, 250, "daily_wellness"); bad(wellness, "stress_avg", 0, 100, "daily_wellness")
    for d in details:
        for z in d["hr_zones"]: pass
    dup = len(acts) - len({a["activity_id"] for a in acts})
    if dup: issues.append({"collection": "activities", "rule": "duplicate activity_id", "count": dup})

    # ---------- coverage report ----------
    def cov_by_month(rows, block):
        out = {}
        for w in rows:
            m = w["date"][:7]; c = w["coverage"][block]; o = out.setdefault(m, {"ok": 0, "partial": 0, "missing": 0, "not_supported": 0}); o[c] += 1
        return out
    runs = [a for a in acts if a["is_run"]]
    report = {
        "schema_version": SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "export_fetched_at": fetched,
        "activities": {"total": len(acts), "runs": len(runs), "first": acts[0]["date"], "last": acts[-1]["date"],
                       "by_year": {y: {"all": sum(a["date"][:4] == y for a in acts), "runs": sum(a["date"][:4] == y for a in runs), "run_km": round(sum((a["distance_m"] or 0) for a in runs if a["date"][:4] == y) / 1000)} for y in sorted({a["date"][:4] for a in acts})},
                       "runs_with_training_load_18m": f'{sum(1 for a in runs if a["date"] >= WELL_FROM and a["training_load"] is not None)}/{sum(1 for a in runs if a["date"] >= WELL_FROM)}',
                       "runs_with_gear_18m": f'{sum(1 for a in runs if a["date"] >= WELL_FROM and a["gear_ids"])}/{sum(1 for a in runs if a["date"] >= WELL_FROM)}'},
        "activity_details": {"total": len(details), "with_km_splits": sum(1 for d in details if d["km_splits"]), "with_drift": sum(1 for d in details if d["drift"]),
                             "with_hr_zones": sum(1 for d in details if d["hr_zones"]), "with_perf_condition": sum(1 for d in details if d["performance_condition"])},
        "daily_wellness": {"days": len(wellness), "range": [wellness[0]["date"], wellness[-1]["date"]],
                           "by_block": {b: {c: sum(w["coverage"][b] == c for w in wellness) for c in ["ok", "partial", "missing"]} for b in ["sleep", "hrv", "rhr", "stress", "body_battery", "steps"]},
                           "missing_days": {b: [w["date"] for w in wellness if w["coverage"][b] == "missing"] for b in ["sleep", "hrv", "rhr"]},
                           "by_month": {b: cov_by_month(wellness, b) for b in ["sleep", "hrv", "rhr"]}},
        "daily_performance": {"days": len(perf), "vo2max_updates": sum(1 for p in perf if p["coverage"]["vo2max"] == "ok"), "race_pred_days": sum(1 for p in perf if p["race_pred_21k_s"]),
                              "first_race_pred": next((p["date"] for p in perf if p["race_pred_21k_s"]), None)},
        "hrv_history_pre_window_days": len(hrv_hist),
        "gear": {"items": len(gear)}, "hr_zones_config": hrz, "validation_issues": issues[:200], "validation_issue_count": len(issues),
        "errors_in_export": raw.get("__errs", []),
    }

    dump = lambda n, o: json.dump(o, open(os.path.join(out, n), "w"), ensure_ascii=False, separators=(",", ":"))
    dump("activities.json", acts); dump("activity_details.json", details); dump("daily_wellness.json", wellness)
    dump("daily_performance.json", perf); dump("gear.json", gear); dump("races_and_goals.json", races); dump("hrv_history.json", hrv_hist); dump("activity_cpet.json", cpet)
    json.dump(report, open(os.path.join(out, "coverage_report.json"), "w"), ensure_ascii=False, indent=1)
    meta = {"checkpoints": {"activities": {"last_activity_id": acts[-1]["activity_id"], "last_date": acts[-1]["date"]}, "daily_wellness": {"last_date": TO},
                            "daily_performance": {"last_date": TO}, "activity_details": {"done_ids": len(details)}},
            "dataset_info": {"schema_version": SCHEMA_VERSION, "athlete_tz": "America/Sao_Paulo", "devices": ["Forerunner 255 Music", "fēnix 5S", "HRM 600"], "last_update": fetched}}
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in report.items() if k not in ("validation_issues", "daily_wellness")}, ensure_ascii=False)[:3000])
    return report


if __name__ == "__main__":
    main(*sys.argv[1:])

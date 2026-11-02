"""Monta o data.json do dashboard a partir do lote normalizado (Fase 2) + derivados (Fase 3).
Usage: python3 -I build_data.py <norm_dir> <derived_dir> <out_json> [window_from YYYY-MM-DD]
Mesmo formato que a área de importação do dashboard aceita (state completo, chaves por coleção)."""
import json, sys, os
from datetime import datetime, timezone

PICK = {
    "activities": ["activity_id", "date", "start_local", "name", "sport", "distance_m", "duration_s", "pace_s_per_km", "gap_s_per_km", "hr_avg", "hr_max", "training_load", "te_label", "aerobic_te", "cadence_avg_spm", "gct_ms", "vert_osc_cm", "elev_gain_m", "gear_ids", "event_type"],
    "daily_wellness": ["date", "sleep_s", "sleep_score", "sleep_deep_s", "sleep_rem_s", "hrv_night_ms", "hrv_7d_ms", "hrv_baseline_low_ms", "hrv_baseline_high_ms", "hrv_status", "rhr_bpm", "stress_avg", "bb_charged", "bb_drained", "steps", "coverage"],
    "daily_performance": ["date", "vo2max", "vo2max_is_carried", "race_pred_5k_s", "race_pred_10k_s", "race_pred_21k_s", "race_pred_42k_s", "training_status", "garmin_acwr", "lactate_threshold_hr", "lactate_threshold_pace_s_per_km", "fitness_age"],
    "derived_metrics": ["date", "zone_dist_28d", "load_day", "km_7d", "km_week_iso", "km_prev_week", "km_wow_pct", "acwr", "ctl", "atl", "tsb", "tsb_pct_ctl", "monotony_7d", "strain_7d", "hrv_7d_vs_60d_pct", "rhr_vs_30d_median_bpm", "sleep_debt_7d_s", "ef_easy_28d", "decoupling_last_long_pct", "readiness_score", "readiness_components", "vdot_current", "vdot_source", "predictions"],
    "alerts": ["alert_id", "date", "type", "severity", "message", "evidence", "days_active", "first_seen", "status", "suggest_doctor", "subject"],
    "gear": ["gear_id", "name", "type", "status", "km_garmin", "max_km"],
    "races_and_goals": ["race_id", "name", "date", "date_window", "distance_m", "goal_type", "target_s", "target_range_s", "target_equivalent_half_s", "target_pace_s_per_km", "priority", "status", "result_s", "notes"],
}
KEYS = {"activities": "activity_id", "daily_wellness": "date", "daily_performance": "date", "derived_metrics": "date", "alerts": "alert_id", "gear": "gear_id", "races_and_goals": "race_id"}


def pick(rows, ks): return [{k: r.get(k) for k in ks if k in r} for r in rows]


def main(nd, dd, out, wfrom="2025-04-06"):
    L = lambda p: json.load(open(p))
    acts = [a for a in L(f"{nd}/activities.json") if a["is_run"] and a["date"] >= wfrom]
    der = L(f"{dd}/derived_metrics.json")
    # só o último dia carrega readiness/predictions completos; demais ficam leves
    state = {
        "schema_version": "1.1.0", "formula_version": L(f"{dd}/formulas.json")["formula_version"],
        "last_update": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_through": der[-1]["date"],
        "collections": {
            "activities": pick(acts, PICK["activities"]),
            "daily_wellness": pick([w for w in L(f"{nd}/daily_wellness.json") if w["date"] >= "2023-01-01"], PICK["daily_wellness"]),
            "lactate_threshold_history": L(f"{nd}/lactate_threshold_history.json") if os.path.exists(f"{nd}/lactate_threshold_history.json") else [],
            "daily_performance": pick([p for p in L(f"{nd}/daily_performance.json") if p["date"] >= "2025-01-01"], PICK["daily_performance"]),
            "derived_metrics": pick(der, PICK["derived_metrics"]),
            "alerts": pick(L(f"{dd}/alerts.json"), PICK["alerts"]),
            "gear": pick(L(f"{nd}/gear.json"), PICK["gear"]),
            "races_and_goals": pick([r for r in L(f"{nd}/races_and_goals.json") if r.get("status") != "cancelled"], PICK["races_and_goals"]),
        },
        "report": L(f"{dd}/daily_report.json"),
        "formulas": L(f"{dd}/formulas.json"),
        "import_log": [{"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "backfill", "counts": {}}],
    }
    for k, v in state["collections"].items(): state["import_log"][0]["counts"][k] = len(v)
    json.dump(state, open(out, "w"), ensure_ascii=False, separators=(",", ":"))
    print({k: len(v) for k, v in state["collections"].items()})


if __name__ == "__main__":
    main(*sys.argv[1:])

"""Coletor diário do Garmin Connect, porte em Python do scripts/collector.js.

Somente leitura: só GETs em connectapi.garmin.com com o token DI (Bearer) do garminconnect.
Gera o mesmo objeto bruto que a aba do Chrome exportava (chaves raw|..., det|..., __errs, __calls, __cfg),
para que daily.py / normalize.py funcionem sem mudança.

Ritmo: >= 1,5 s entre chamadas, 30 s de pausa a cada 100. Em 403/429 o coletor PARA (HALTED_403/429):
numa função serverless não vale esperar 10 min; a janela de 3 dias do daily.py recupera o dia seguinte.
"""
import re, time
from datetime import date, timedelta, datetime, timezone

from garminconnect.exceptions import (GarminConnectAuthenticationError, GarminConnectConnectionError,
                                      GarminConnectNotFoundError, GarminConnectTooManyRequestsError)

U = "4b103eb8-e642-44bf-a011-54cbc87a5715"
ACT_KEYS = ['activityId', 'activityName', 'startTimeLocal', 'startTimeGMT', 'activityType', 'eventType', 'distance', 'duration', 'elapsedDuration', 'movingDuration', 'elevationGain', 'elevationLoss', 'averageSpeed', 'maxSpeed', 'calories', 'averageHR', 'maxHR', 'averageRunningCadenceInStepsPerMinute', 'maxRunningCadenceInStepsPerMinute', 'steps', 'avgPower', 'maxPower', 'normPower', 'aerobicTrainingEffect', 'anaerobicTrainingEffect', 'trainingEffectLabel', 'activityTrainingLoad', 'avgVerticalOscillation', 'avgGroundContactTime', 'avgStrideLength', 'vO2MaxValue', 'avgVerticalRatio', 'avgGroundContactBalance', 'deviceId', 'isPR', 'minElevation', 'maxElevation', 'avgGradeAdjustedSpeed', 'avgRespirationRate', 'hrTimeInZone_1', 'hrTimeInZone_2', 'hrTimeInZone_3', 'hrTimeInZone_4', 'hrTimeInZone_5', 'manufacturer', 'lapCount', 'moderateIntensityMinutes', 'vigorousIntensityMinutes', 'differenceBodyBattery', 'directWorkoutRpe', 'directWorkoutFeel']
# Zonas do teste cardiopulmonar (13/02/2026): pisos 123/133/148/162 bpm; bandas ±5 bpm em LV1 (133) e LV2 (162).
FLOORS = [123, 133, 148, 162]
LV1, LV2 = 133, 162


class Halt(Exception):
    def __init__(self, status):
        super().__init__(status); self.status = status


def _status(err):
    m = re.search(r"\b([45]\d\d)\b", str(err))
    return int(m.group(1)) if m else None


class Collector:
    def __init__(self, client, cfg, deadline_s=600):
        self.c = client  # garminconnect Garmin().client
        self.cfg = cfg
        self.n = 0; self.errs = []; self.gap = 1.5; self.last = 0.0; self.rows = {}
        self.t0 = time.time(); self.deadline = deadline_s

    # ---------- HTTP ----------
    def get(self, path, params=None):
        w = self.gap - (time.time() - self.last)
        if w > 0: time.sleep(w)
        if self.n > 0 and self.n % 100 == 0: time.sleep(30)
        if time.time() - self.t0 > self.deadline: raise Halt("DEADLINE")
        self.last = time.time(); self.n += 1
        try:
            # _run_request (garminconnect 0.3.20, versão fixada) devolve a resposta crua: 204 vira None, como no coletor JS.
            r = self.c._run_request("GET", path, params=params) if params else self.c._run_request("GET", path)
            return None if getattr(r, "status_code", 200) == 204 else r.json()
        except GarminConnectAuthenticationError:
            raise Halt("SESSION_EXPIRED")
        except GarminConnectTooManyRequestsError:
            self.errs.append([path, 429]); raise Halt("HALTED_429")
        except GarminConnectNotFoundError:
            self.errs.append([path, 404]); return None
        except GarminConnectConnectionError as e:
            st = getattr(getattr(e, "response", None), "status_code", None) or _status(e)
            if st == 401: raise Halt("SESSION_EXPIRED")
            if st in (403, 429): self.errs.append([path, st]); raise Halt(f"HALTED_{st}")
            self.errs.append([path, st or "net"]); return None
        except Exception as e:  # rede/timeout
            self.errs.append([path, _status(e) or "net"]); return None

    def step(self, key, path, params=None):
        if key in self.rows: return
        j = self.get(path, params)
        self.rows[key] = {"__empty": True} if j is None else j

    # ---------- blocos ----------
    @staticmethod
    def _windows(frm, to, ln):
        out, s = [], date.fromisoformat(frm); t = date.fromisoformat(to)
        while s <= t:
            e = min(t, s + timedelta(ln - 1)); out.append((s.isoformat(), e.isoformat())); s = e + timedelta(1)
        return out

    def activities(self):
        p = self.get("/activitylist-service/activities/search/activities", {"limit": self.cfg["ACT_LIMIT"], "start": 0}) or []
        out = []
        for a in p:
            r = {}
            for k in ACT_KEYS:
                if k in a: r[k] = (a[k].get("typeKey") if isinstance(a[k], dict) else a[k]) if k in ("activityType", "eventType") else a[k]
            out.append(r)
        self.rows["raw|activities"] = out

    def wellness(self):
        C = self.cfg
        for s, e in self._windows(C["WELL_FROM"], C["TO"], 28):
            self.step(f"raw|sleep|{s}", f"/sleep-service/stats/sleep/daily/{s}/{e}")
            self.step(f"raw|stress|{s}", f"/usersummary-service/stats/stress/daily/{s}/{e}")
            self.step(f"raw|bb|{s}", "/wellness-service/wellness/bodyBattery/reports/daily", {"startDate": s, "endDate": e})
            self.step(f"raw|steps|{s}", f"/usersummary-service/stats/steps/daily/{s}/{e}")
            self.step(f"raw|im|{s}", f"/usersummary-service/stats/im/daily/{s}/{e}")
        for s, e in self._windows(C["HIST_FROM"], C["TO"], 365):
            self.step(f"raw|hrv|{s}", f"/hrv-service/hrv/daily/{s}/{e}")
            self.step(f"raw|rhr|{s}", f"/userstats-service/wellness/daily/{U}", {"fromDate": s, "toDate": e, "metricId": 60})

    def performance(self):
        C = self.cfg
        for s, e in self._windows(C["HIST_FROM"], C["TO"], 365):
            self.step(f"raw|maxmet|{s}", f"/metrics-service/metrics/maxmet/daily/{s}/{e}")
            self.step(f"raw|racepred|{s}", f"/metrics-service/metrics/racepredictions/daily/{U}", {"fromCalendarDate": s, "toCalendarDate": e})
        t = C["TO"]
        self.step(f"raw|tstatus|{t}", f"/metrics-service/metrics/trainingstatus/aggregated/{t}")
        self.step(f"raw|lactate|{t}", "/biometric-service/biometric/latestLactateThreshold")
        self.step(f"raw|fitnessage|{t}", f"/fitnessage-service/fitnessage/{t}")
        self.step(f"raw|hrzones|{t}", "/biometric-service/heartRateZones/")
        self.step(f"raw|gear|{t}", "/gear-service/gear/v2/list", [("start", 0), ("limit", 100), ("gearStatuses", "ACTIVE"), ("gearStatuses", "RETIRED"), ("sortOrder", "firstUseDate_desc")])
        self.step(f"raw|prs|{t}", f"/personalrecord-service/personalrecord/prs/{U}", {"includeHistory": "true"})

    def details(self):
        C = self.cfg
        acts = self.rows.get("raw|activities") or []
        runs = [a for a in acts if "running" in (a.get("activityType") or "") and (
            (a.get("startTimeLocal") or "") >= C["DET_FROM"] or ((a.get("startTimeLocal") or "") >= C["HIST_FROM"] and (a.get("eventType") == "race" or (a.get("distance") or 0) >= 25000)))]
        runs.sort(key=lambda a: a["startTimeLocal"], reverse=True)
        for a in runs:
            i = a["activityId"]
            if f"det|{i}" in self.rows: continue
            sm = self.get(f"/activity-service/activity/{i}")
            sp = self.get(f"/activity-service/activity/{i}/splits")
            zn = self.get(f"/activity-service/activity/{i}/hrTimeInZones")
            dt = self.get(f"/activity-service/activity/{i}/details", {"maxChartSize": 20000, "maxPolylineSize": 0, "maxHeatMapSize": 0})
            gr = self.get(f"/gear-service/activity/v2/{i}")
            laps = []
            for l in (sp or {}).get("lapDTOs") or []:
                c = dict(l); c.pop("lengthDTOs", None); c.pop("connectIQMeasurement", None); laps.append(c)
            self.rows[f"det|{i}"] = {
                "activity_id": i, "summary": (sm or {}).get("summaryDTO"), "event_type": ((sm or {}).get("eventTypeDTO") or {}).get("typeKey"),
                "laps": laps, "hr_zones": zn or [], "streams": process_streams(dt),
                "gear": [{"uuid": g.get("uuid"), "name": g.get("displayName") or g.get("customMakeModel") or " ".join(x for x in [g.get("brand"), g.get("model")] if x) or g.get("name")} for g in gr] if isinstance(gr, list) else [],
                "fetched_at": datetime.now(timezone.utc).isoformat()}

    def run(self):
        status = "done"
        try:
            self.activities(); self.wellness(); self.performance(); self.details()
        except Halt as h:
            status = h.status
        out = {"__exported_at": datetime.now(timezone.utc).isoformat(), "__errs": self.errs, "__calls": self.n, "__cfg": self.cfg, "__status": status}
        out.update(self.rows)
        return status, out


def _mean(a): return sum(a) / len(a)


def process_streams(d):
    """Porte fiel de processStreams() do collector.js."""
    if not d or not d.get("metricDescriptors") or not d.get("activityDetailMetrics"): return None
    ix = {m["key"]: m["metricsIndex"] for m in d["metricDescriptors"]}
    pts = d["activityDetailMetrics"]
    col = lambda k: None if k not in ix else [(p.get("metrics") or [])[ix[k]] if len(p.get("metrics") or []) > ix[k] else None for p in pts]
    dist, t = col("sumDistance"), col("sumDuration") or col("sumElapsedDuration")
    hr, spd, el, pc = col("directHeartRate"), col("directSpeed"), col("directElevation"), col("directPerformanceCondition")
    out = {"n_points": len(pts)}
    if dist and t:
        ks, k, i0, t0 = [], 1, 0, 0.0
        for i in range(1, len(dist)):
            if dist[i] is None or t[i] is None: continue
            while dist[i] >= k * 1000 and dist[i - 1] is not None:
                f = (k * 1000 - dist[i - 1]) / ((dist[i] - dist[i - 1]) or 1); tk = t[i - 1] + f * (t[i] - t[i - 1])
                seg = [x for x in (hr[i0:i + 1] if hr else []) if x and x > 0]
                ks.append({"km": k, "duration_s": round(tk - t0, 1), "hr_avg": round(_mean(seg), 1) if seg else None,
                           "elev_delta_m": round(el[i] - el[i0], 1) if el and el[i] is not None and el[i0] is not None else None})
                t0 = tk; i0 = i; k += 1
        out["km_splits"] = ks
    if hr and spd and t:
        T = t[-1] or 0; P = []
        for i in range(len(t)):
            if t[i] is not None and 0.10 * T <= t[i] <= 0.95 * T and (hr[i] or 0) > 60 and (spd[i] or 0) > 1.5: P.append((t[i], hr[i], spd[i]))
        if len(P) > 60:
            mid = P[0][0] + (P[-1][0] - P[0][0]) / 2
            A = [p for p in P if p[0] < mid]; B = [p for p in P if p[0] >= mid]
            h = lambda arr, j: _mean([p[j] for p in arr])
            ef1, ef2 = h(A, 2) / h(A, 1), h(B, 2) / h(B, 1)
            out["drift"] = {"pa_hr_decoupling_pct": round((ef1 - ef2) / ef1 * 100, 2), "hr_drift_bpm": round(h(B, 1) - h(A, 1), 1),
                            "ef1": round(ef1 * 60, 4), "ef2": round(ef2 * 60, 4), "computed_on": "moving_10to95pct"}
    if hr and t:
        zs = [0.0] * 5
        for i in range(1, len(t)):
            if t[i] is None or t[i - 1] is None: continue
            dt = t[i] - t[i - 1]
            if 0 < dt <= 10 and (hr[i] or 0) > 0: zs[sum(1 for f in FLOORS if hr[i] >= f)] += dt
        bands = {"lv1": [], "lv2": []}
        if spd:
            j = 0
            for i in range(len(t)):
                if t[i] is None: continue
                while j < i and (t[j] is None or t[i] - t[j] > 30): j += 1
                if t[i] - (t[0] or 0) < 600 or i - j < 10: continue
                H = [x for x in hr[j:i + 1] if x and x > 0]; S = [x for x in spd[j:i + 1] if x is not None]
                if len(H) < 10 or len(S) < 10: continue
                hm, sm = _mean(H), _mean(S)
                if sm < 2 or max(H) - min(H) > 8: continue
                if el and el[i] is not None and el[j] is not None:
                    dd = sm * (t[i] - t[j])
                    if dd > 0 and abs(el[i] - el[j]) / dd > 0.03: continue
                if abs(hm - LV1) <= 5: bands["lv1"].append(sm)
                if abs(hm - LV2) <= 5: bands["lv2"].append(sm)

        def med(a):
            b = sorted(a); m = len(b) >> 1
            return b[m] if len(b) % 2 else (b[m - 1] + b[m]) / 2
        bo = lambda a: {"secs": len(a), "speed_med_mps": round(med(a), 3) if len(a) >= 120 else None}
        out["cpet"] = {"zone_secs": [round(z) for z in zs], "lv1": bo(bands["lv1"]), "lv2": bo(bands["lv2"])}
    if pc:
        v = [x for x in pc if x is not None]
        if v: out["performance_condition"] = {"start": v[min(len(v) - 1, 360)], "min": min(v), "end": v[-1]}
    return out

#!/usr/bin/env python3
"""
fetch_and_build.py — Station Colmar-Mittelharth
- Lit les fichiers Excel historiques (2025.xlsx, etc.)
- Accumule les données journalières dans docs/history.json
- Génère les pages HTML avec sélecteur d'année
"""

import os, json, math, datetime, requests, sys
from collections import defaultdict
from pathlib import Path
from hiking_score import fetch_hiking_forecasts, build_hiking_report

APP_KEY = os.environ["ECOWITT_APP_KEY"]
API_KEY = os.environ["ECOWITT_API_KEY"]
MAC     = os.environ["ECOWITT_MAC"]

BASE_URL  = "https://api.ecowitt.net/api/v3"
CALL_BACK = "outdoor,indoor,rainfall,wind,pressure,solar_and_uvi"
HIST_FILE     = Path("docs/history.json")   # accumulation journalière
LIVE_FILE     = Path("docs/live.json")      # données temps réel
HOURLY_FILE   = Path("docs/hourly.json")    # données horaires (24 dernières heures)
FORECAST_FILE = Path("docs/forecast.json")  # prévisions Open-Meteo
HOURLY_FC_FILE = Path("docs/hourly_forecast.json")  # prévisions horaires (pour la durée des alertes)

# Coordonnées de Colmar
LAT, LON = 48.08, 7.36

# ── Helpers ───────────────────────────────────────────────────────────────────
def api_get(endpoint, params):
    params.update({"application_key": APP_KEY, "api_key": API_KEY, "mac": MAC})
    r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fv(x):
    if x is None or x == '-': return None
    try: return float(x)
    except: return None

# ── 1. Données temps réel ─────────────────────────────────────────────────────
def fetch_realtime():
    print("Fetching realtime data...")
    data = api_get("device/real_time", {
        "call_back": CALL_BACK,
        "temp_unitid": 1, "pressure_unitid": 3,
        "wind_speed_unitid": 7, "rainfall_unitid": 12,
        "solar_irradiance_unitid": 16,
    })
    d = data.get("data", {})
    def safe(*keys):
        obj = d
        for k in keys:
            if not isinstance(obj, dict): return None
            obj = obj.get(k)
        if obj is None: return None
        try: return float(obj) if not isinstance(obj, dict) else None
        except: return None

    live = {
        "updated_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2))).strftime("%d/%m/%Y %H:%M"),
        "temp":         safe("outdoor","temperature","value"),
        "temp_feels":   safe("outdoor","feels_like","value"),
        "hum":          safe("outdoor","humidity","value"),
        "dew":          safe("outdoor","dew_point","value"),
        "pressure":     safe("pressure","relative","value"),
        "wind_speed":   safe("wind","wind_speed","value"),
        "wind_gust":    safe("wind","wind_gust","value"),
        "wind_dir":     safe("wind","wind_direction","value"),
        "rain_rate":    safe("rainfall","rain_rate","value"),
        "rain_daily":   safe("rainfall","daily","value"),
        "rain_monthly": safe("rainfall","monthly","value"),
        "rain_yearly":  safe("rainfall","yearly","value"),
        "solar":        safe("solar_and_uvi","solar","value"),
        "uvi":          safe("solar_and_uvi","uvi","value"),
        "temp_in":      safe("indoor","temperature","value"),
        "hum_in":       safe("indoor","humidity","value"),
    }
    LIVE_FILE.write_text(json.dumps(live, ensure_ascii=False, indent=2))
    print(f"  → Temp: {live['temp']}°C, Hum: {live['hum']}%, Pluie: {live['rain_daily']}mm")
    return live

# ── 1b. Accumulation horaire (24h glissantes) ─────────────────────────────────
def update_hourly(live):
    """Garde les 24 dernières mesures horaires pour le mini graphique."""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2))).strftime("%Y-%m-%d %H:%M")
    if HOURLY_FILE.exists():
        hourly = json.loads(HOURLY_FILE.read_text())
    else:
        hourly = []

    hourly.append({
        "time": now,
        "temp": live.get("temp"),
        "hum":  live.get("hum"),
        "pres": live.get("pressure"),
        "rain": live.get("rain_daily"),
        "solar": live.get("solar"),
    })
    # Garder seulement les 24 dernières heures (24 points si run horaire)
    hourly = hourly[-24:]
    HOURLY_FILE.write_text(json.dumps(hourly, ensure_ascii=False))
    print(f"  → Historique horaire : {len(hourly)} points")
    return hourly

# ── 1c. Prévisions Open-Meteo (gratuit, sans clé) ────────────────────────────
def fetch_forecast():
    """
    Récupère les prévisions journalières (7 jours) et horaires depuis
    Open-Meteo. Les données horaires servent à estimer la plage horaire /
    durée des alertes météo (compute_alerts).
    Retourne (forecast_journalier, forecast_horaire).
    """
    print("Fetching forecast...")
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={LAT}&longitude={LON}"
            f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,"
            f"weathercode,windspeed_10m_max"
            f"&hourly=temperature_2m,precipitation_probability,precipitation,"
            f"windspeed_10m,windgusts_10m,weathercode"
            f"&timezone=Europe%2FParis"
            f"&forecast_days=7"
        )
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily", {})
        forecast = []
        dates = daily.get("time", [])
        for i, date in enumerate(dates):
            forecast.append({
                "date":   date,
                "max_t":  daily.get("temperature_2m_max", [None]*8)[i],
                "min_t":  daily.get("temperature_2m_min", [None]*8)[i],
                "rain":   daily.get("precipitation_sum",  [None]*8)[i],
                "code":   daily.get("weathercode",        [None]*8)[i],
                "wind":   daily.get("windspeed_10m_max",  [None]*8)[i],
            })
        FORECAST_FILE.write_text(json.dumps(forecast, ensure_ascii=False))

        hourly_data = data.get("hourly", {})
        h_times  = hourly_data.get("time", [])
        h_temp   = hourly_data.get("temperature_2m", [])
        h_rproba = hourly_data.get("precipitation_probability", [])
        h_rain   = hourly_data.get("precipitation", [])
        h_wind   = hourly_data.get("windspeed_10m", [])
        h_gust   = hourly_data.get("windgusts_10m", [])
        h_code   = hourly_data.get("weathercode", [])
        hourly_forecast = []
        for i, t in enumerate(h_times):
            hourly_forecast.append({
                "time":       t,
                "temp":       h_temp[i]   if i < len(h_temp)   else None,
                "rain_proba": h_rproba[i] if i < len(h_rproba) else None,
                "rain_mm":    h_rain[i]   if i < len(h_rain)   else None,
                "wind":       h_wind[i]   if i < len(h_wind)   else None,
                "gust":       h_gust[i]   if i < len(h_gust)   else None,
                "code":       h_code[i]   if i < len(h_code)   else None,
            })
        HOURLY_FC_FILE.write_text(json.dumps(hourly_forecast, ensure_ascii=False))

        print(f"  → {len(forecast)} jours de prévisions, {len(hourly_forecast)} heures")
        return forecast, hourly_forecast
    except Exception as e:
        print(f"  → Erreur prévisions: {e}")
        return [], []

# ── 2. Lecture Excel ──────────────────────────────────────────────────────────
def read_excel_files():
    """Lit tous les .xlsx et retourne dict {date_str: données}."""
    try:
        import openpyxl
    except ImportError:
        print("  → openpyxl non disponible")
        return {}

    all_data = {}
    xlsx_files = sorted(Path(".").glob("*.xlsx")) + sorted(Path(".").glob("**/*.xlsx"))
    xlsx_files = list(dict.fromkeys(xlsx_files))

    for xlsx in xlsx_files:
        if 'docs' in str(xlsx): continue
        print(f"  → Lecture {xlsx.name}...")
        try:
            wb = openpyxl.load_workbook(xlsx, read_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            count = 0
            for row in rows[2:]:
                if row[0] is None: continue
                d = str(row[0])[:10]
                if len(d) < 10: continue
                all_data[d] = {
                    "date":      d,
                    "month":     int(d[5:7]),
                    "day":       int(d[8:10]),
                    "year":      int(d[:4]),
                    "avg":       fv(row[1]),
                    "lo":        fv(row[2]),
                    "hi":        fv(row[3]),
                    "hum":       fv(row[6]),
                    "rain":      fv(row[21]),
                    "solar":     fv(row[18]),
                    "pres":      fv(row[32]),
                    "wind":      fv(row[28]),
                    "wind_gust": fv(row[29]),
                    "rain_rate_max": fv(row[20]),
                }
                count += 1
            print(f"     {count} jours lus")
        except Exception as e:
            print(f"  → Erreur {xlsx.name}: {e}")
    return all_data

# ── Calcul lever/coucher du soleil (algorithme NOAA) ─────────────────────────
def sun_times(lat=48.08, lon=7.36):
    import math
    date = datetime.date.today()
    y, m, d = date.year, date.month, date.day

    # Jour julien
    JD = 367*y - int(7*(y+int((m+9)/12))/4) + int(275*m/9) + d + 1721013.5
    T = (JD - 2451545.0) / 36525.0

    # Anomalie moyenne
    M = (357.52911 + T*(35999.05029 - 0.0001537*T)) % 360
    # Centre de l'équation
    C = (1.914602 - T*(0.004817 + 0.000014*T))*math.sin(math.radians(M))
    C += (0.019993 - 0.000101*T)*math.sin(math.radians(2*M))
    C += 0.000289*math.sin(math.radians(3*M))
    L0 = 280.46646 + T*36000.76983
    theta = (L0 + C) % 360

    # Obliquité et déclinaison
    e = 23.439291 - T*0.013004
    decl = math.degrees(math.asin(math.sin(math.radians(e))*math.sin(math.radians(theta))))

    # Équation du temps (minutes)
    epsilon = math.radians(e)
    y2 = math.tan(epsilon/2)**2
    L0r, Mr = math.radians(L0), math.radians(M)
    eot = 4*math.degrees(y2*math.sin(2*L0r) - 2*0.016708634*math.sin(Mr)
          + 4*0.016708634*y2*math.sin(Mr)*math.cos(2*L0r)
          - 0.5*y2**2*math.sin(4*L0r) - 1.25*0.016708634**2*math.sin(2*Mr))

    # Angle horaire au lever (dépression 0.833° pour réfraction)
    lat_r = math.radians(lat)
    decl_r = math.radians(decl)
    cos_ha = (math.cos(math.radians(90.833)) - math.sin(lat_r)*math.sin(decl_r)) / (math.cos(lat_r)*math.cos(decl_r))
    if cos_ha > 1 or cos_ha < -1:
        return None, None, 0
    ha = math.degrees(math.acos(cos_ha))

    # Lever/coucher UTC → heure locale Paris
    dst = 2 if 3 < date.month < 11 else 1
    sunrise = 720 - 4*(lon + ha) - eot + dst*60
    sunset  = 720 - 4*(lon - ha) - eot + dst*60
    day_len = int(sunset - sunrise)

    def fmt(mn):
        h, mi = divmod(int(mn) % 1440, 60)
        return f"{h:02d}:{mi:02d}"

    return fmt(sunrise), fmt(sunset), day_len

# ── Phase de lune (algorithme synodique simplifié) ────────────────────────────
def moon_phase_info(dt=None):
    """Retourne (emoji, nom_fr) pour la phase de lune à la date donnée."""
    if dt is None:
        dt = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2)))
    known_new_moon = datetime.datetime(2000, 1, 6, 18, 14, tzinfo=datetime.timezone.utc)
    synodic = 29.530588861
    days = (dt.astimezone(datetime.timezone.utc) - known_new_moon).total_seconds() / 86400
    phase = (days % synodic) / synodic
    steps = [
        (0.0625, "🌑", "Nouvelle Lune"),
        (0.1875, "🌒", "Premier Croissant"),
        (0.3125, "🌓", "Premier Quartier"),
        (0.4375, "🌔", "Lune Gibbeuse Croissante"),
        (0.5625, "🌕", "Pleine Lune"),
        (0.6875, "🌖", "Lune Gibbeuse Décroissante"),
        (0.8125, "🌗", "Dernier Quartier"),
        (0.9375, "🌘", "Dernier Croissant"),
    ]
    for edge, emoji, name in steps:
        if phase < edge:
            return emoji, name
    return "🌑", "Nouvelle Lune"

# ── Calcul des records de la station ─────────────────────────────────────────
def get_records(hist_dict):
    """Retourne les records absolus de la station."""
    records = {
        "max_t": {"val": None, "date": None},
        "min_t": {"val": None, "date": None},
        "max_rain": {"val": None, "date": None},
        "max_rain_rate": {"val": None, "date": None},
        "max_wind": {"val": None, "date": None},
    }
    for date_str, d in hist_dict.items():
        hi = d.get("hi")
        lo = d.get("lo")
        rain = d.get("rain")
        rain_rate = d.get("rain_rate_max")
        wind = d.get("wind")
        if hi is not None and (records["max_t"]["val"] is None or hi > records["max_t"]["val"]):
            records["max_t"] = {"val": hi, "date": date_str}
        if lo is not None and (records["min_t"]["val"] is None or lo < records["min_t"]["val"]):
            records["min_t"] = {"val": lo, "date": date_str}
        if rain is not None and (records["max_rain"]["val"] is None or rain > records["max_rain"]["val"]):
            records["max_rain"] = {"val": rain, "date": date_str}
        if rain_rate is not None and (records["max_rain_rate"]["val"] is None or rain_rate > records["max_rain_rate"]["val"]):
            records["max_rain_rate"] = {"val": rain_rate, "date": date_str}
        gust = d.get("wind_gust") or d.get("wind")
        if gust is not None and (records["max_wind"]["val"] is None or gust > records["max_wind"]["val"]):
            records["max_wind"] = {"val": gust, "date": date_str}
    return records


def update_history(live):
    """Ajoute/met à jour la journée d'aujourd'hui dans history.json."""
    today = datetime.date.today().strftime("%Y-%m-%d")

    # Charger historique existant
    if HIST_FILE.exists():
        hist = json.loads(HIST_FILE.read_text())
    else:
        hist = {}

    # Données Excel
    excel = read_excel_files()
    for date_str, d in excel.items():
        if date_str not in hist:
            hist[date_str] = d
        elif hist[date_str].get("rain_rate_max") is None and d.get("rain_rate_max") is not None:
            # Complète les jours déjà présents avec ce champ ajouté après coup,
            # sans toucher au reste de leurs données déjà enregistrées.
            hist[date_str]["rain_rate_max"] = d["rain_rate_max"]

    # Données d'aujourd'hui depuis l'API temps réel
    t = live.get("temp")
    existing = hist.get(today, {})
    rain_rate_now = live.get("rain_rate")
    hist[today] = {
        "date":  today,
        "month": int(today[5:7]),
        "day":   int(today[8:10]),
        "year":  int(today[:4]),
        "avg":   t,
        "hi":    max(existing.get("hi") or t or 0, t or 0) if t else existing.get("hi"),
        "lo":    min(existing.get("lo") or t or 0, t or 0) if t else existing.get("lo"),
        "hum":   live.get("hum"),
        "rain":      live.get("rain_daily"),
        "rain_rate_max": max(existing.get("rain_rate_max") or 0, rain_rate_now or 0),
        "solar":     live.get("solar"),
        "pres":      live.get("pressure"),
        "wind":      live.get("wind_speed"),
        "wind_gust": live.get("wind_gust"),
    }

    HIST_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2))
    print(f"  → Historique : {len(hist)} jours")
    return hist

# ── 4. Agrégation par année ───────────────────────────────────────────────────
# ── Jours de canicule (critère officiel Haut-Rhin) ────────────────────────────
def compute_canicule_days(daily, tolerance=1.0):
    """
    Critère officiel Haut-Rhin (préfecture / Météo-France) : la nuit ne
    descend pas sous 19°C ET le jour atteint 35°C ou plus, pendant au moins
    3 jours et 3 nuits consécutifs.

    Une marge de tolérance (par défaut 1°C) est appliquée aux deux seuils
    pour absorber l'incertitude de mesure d'une station personnelle : un
    capteur peut légitimement s'écarter de ±1°C d'une mesure de référence,
    donc une nuit à 18,2°C est traitée comme atteignant le seuil de 19°C.
    Seuils effectifs : max ≥ (35 - tolerance)°C et min ≥ (19 - tolerance)°C.

    Retourne {mois: nb_jours} pour les jours qui appartiennent à un épisode
    qualifiant (dates calendaires réellement consécutives, pas juste des
    lignes consécutives dans les données).
    """
    max_threshold = 35 - tolerance
    min_threshold = 19 - tolerance

    qualifying = sorted(
        d["date"] for d in daily
        if (d.get("hi") is not None and d["hi"] >= max_threshold)
        and (d.get("lo") is not None and d["lo"] >= min_threshold)
    )

    canicule_dates = set()
    i = 0
    n = len(qualifying)
    while i < n:
        j = i
        while j + 1 < n:
            d1 = datetime.datetime.strptime(qualifying[j], "%Y-%m-%d")
            d2 = datetime.datetime.strptime(qualifying[j + 1], "%Y-%m-%d")
            if (d2 - d1).days == 1:
                j += 1
            else:
                break
        if (j - i + 1) >= 3:
            canicule_dates.update(qualifying[i:j + 1])
        i = j + 1

    by_month = {m: 0 for m in range(1, 13)}
    for date_str in canicule_dates:
        by_month[int(date_str[5:7])] += 1
    return by_month


def aggregate_by_year(hist_dict):
    """Retourne {year: {monthly, daily, kpi, gel, chaud, pluie, heatmap}}."""
    years = sorted(set(d["year"] for d in hist_dict.values() if d.get("year")))
    result = {}

    mn = ["Jan","Fév","Mar","Avr","Mai","Juin","Juil","Août","Sep","Oct","Nov","Déc"]

    for year in years:
        daily = sorted(
            [d for d in hist_dict.values() if d.get("year") == year],
            key=lambda x: x["date"]
        )

        # Agrégats mensuels
        monthly = {}
        for m in range(1, 13):
            md = [d for d in daily if d["month"] == m]
            t   = [d["avg"]  for d in md if d.get("avg")  is not None]
            hi  = [d["hi"]   for d in md if d.get("hi")   is not None]
            lo  = [d["lo"]   for d in md if d.get("lo")   is not None]
            r   = [d["rain"] for d in md if d.get("rain") is not None]
            s   = [d["solar"]for d in md if d.get("solar") is not None]
            h   = [d["hum"]  for d in md if d.get("hum")  is not None]
            p   = [d["pres"] for d in md if d.get("pres") is not None]

            avg_t = round(sum(t)/len(t),1) if t else None
            hum_a = round(sum(h)/len(h),1) if h else None

            # Humidex
            humidex = None
            if avg_t is not None and hum_a is not None:
                e = (hum_a/100)*6.105*math.exp(17.27*avg_t/(237.7+avg_t))
                humidex = round(avg_t + 0.33*e - 4, 1)

            monthly[m] = {
                "name":     mn[m-1],
                "avg_t":    avg_t,
                "max_t":    round(max(hi),1) if hi else None,
                "min_t":    round(min(lo),1) if lo else None,
                "rain":     round(sum(r),1)  if r  else None,
                "solar":    round(sum(s)/len(s),1) if s else None,
                "hum":      hum_a,
                "pressure": round(sum(p)/len(p),1) if p else None,
                "humidex":  humidex,
                "n":        len(md),
            }

        # Jours remarquables
        gel   = {m: sum(1 for d in daily if d["month"]==m and (d.get("lo") or 0) < 0)   for m in range(1,13)}
        chaud = {m: sum(1 for d in daily if d["month"]==m and (d.get("hi") or 0) >= 30) for m in range(1,13)}
        pluie = {m: sum(1 for d in daily if d["month"]==m and (d.get("rain") or 0) > 1) for m in range(1,13)}
        nuits_trop = {m: sum(1 for d in daily if d["month"]==m and (d.get("lo") or 0) >= 20) for m in range(1,13)}
        canicule = compute_canicule_days(daily)

        # Heatmap [mois 0-11][jour 0-30]
        heatmap = []
        for m in range(1,13):
            row = []
            for day in range(1,32):
                found = next((d["avg"] for d in daily if d["month"]==m and d["day"]==day), None)
                row.append(found)
            heatmap.append(row)

        # KPI
        all_hi   = [d["hi"]   for d in daily if d.get("hi")   is not None]
        all_lo   = [d["lo"]   for d in daily if d.get("lo")   is not None]
        all_rain = [d["rain"] for d in daily if d.get("rain") is not None]

        result[year] = {
            "daily":   daily,
            "monthly": monthly,
            "heatmap": heatmap,
            "gel":     gel,
            "chaud":   chaud,
            "pluie":   pluie,
            "nuits_trop": nuits_trop,
            "canicule": canicule,
            "max_abs": round(max(all_hi),1)     if all_hi   else None,
            "min_abs": round(min(all_lo),1)      if all_lo   else None,
            "rain_total": round(sum(all_rain),1) if all_rain else 0,
            "n_days":  len(daily),
        }
        print(f"  → {year}: {len(daily)} jours, max={result[year]['max_abs']}°C, pluie={result[year]['rain_total']}mm")

    return years, result

# ── 4b. Alertes météo ─────────────────────────────────────────────────────────
def _find_condition_window(hourly_fc, cond, now, limit_hours=168, value_fn=None):
    """
    Cherche, dans les prévisions horaires à venir (jusqu'à limit_hours),
    le premier bloc continu d'heures qui vérifie cond(heure). Retourne
    (start_dt, end_dt, points) ou None si aucune heure ne correspond.

    Si value_fn est fourni, points contient [(dt, valeur), ...] pour
    chaque heure du bloc — utilisé pour afficher le détail horaire de
    l'alerte (ex: température ou rafale heure par heure).
    """
    if not hourly_fc:
        return None
    upcoming = []
    for h in hourly_fc:
        t = h.get("time")
        if not t:
            continue
        try:
            dt = datetime.datetime.fromisoformat(t)
        except ValueError:
            continue
        if dt < now - datetime.timedelta(hours=1) or dt > now + datetime.timedelta(hours=limit_hours):
            continue
        upcoming.append((dt, h))
    upcoming.sort(key=lambda x: x[0])

    start = end = None
    points = []
    for dt, h in upcoming:
        if cond(h):
            if start is None:
                start = dt
            end = dt
            if value_fn is not None:
                points.append((dt, value_fn(h)))
        elif start is not None:
            break  # le bloc continu est terminé
    if start is None:
        return None
    return start, end, points


def _hours_left_today(now):
    """
    Nombre d'heures avant minuit (arrondi au supérieur, minimum 1) — utilisé
    pour cantonner l'alerte pluie/orage du jour (⛈️ Orage) à la seule
    journée en cours, au lieu de la limite par défaut de 7 jours de
    _find_condition_window.
    """
    midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, math.ceil((midnight - now).total_seconds() / 3600))


_JOURS_ABBR_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]  # indexé sur date.weekday()


def _format_alert_window(window, now):
    """Formate (start_dt, end_dt) en 'jusqu'à HHhMM' ou 'HHhMM – HHhMM'."""
    if not window:
        return ""
    start, end = window

    def fmt(dt):
        if dt.date() == now.date():
            return dt.strftime("%Hh%M")
        # strftime('%a') dépend de la locale du système (anglais sur les
        # runners GitHub Actions) : on formate donc le jour nous-mêmes
        # pour rester en français, cohérent avec le reste du site.
        return f"{_JOURS_ABBR_FR[dt.weekday()]} {dt.strftime('%Hh%M')}"

    start_lbl = fmt(start)
    end_lbl   = fmt(end)
    if start <= now:
        return f"jusqu'à {end_lbl}"
    return f"{start_lbl} – {end_lbl}"


def _fallback_today_window(hourly_fc, now, value_fn):
    """
    Dernier repli quand aucune heure ne vérifie exactement la condition
    recherchée — par exemple un code météo horaire (pluie forte, etc.) qui
    ne reprend pas le code journalier agrégé annonçant un orage. Plutôt que
    de n'afficher aucun détail, on renvoie les heures d'aujourd'hui avec
    leur valeur, pour laisser voir la tendance de la journée.
    """
    if not hourly_fc:
        return None
    today = now.date()
    todays = []
    for h in hourly_fc:
        t = h.get("time")
        if not t:
            continue
        try:
            dt = datetime.datetime.fromisoformat(t)
        except ValueError:
            continue
        if dt.date() != today or dt < now - datetime.timedelta(hours=1):
            continue
        todays.append((dt, h))
    todays.sort(key=lambda x: x[0])
    if not todays:
        return None
    points = [(dt, value_fn(h)) for dt, h in todays]
    return todays[0][0], todays[-1][0], points


def _format_hourly_detail(points, unit="", dec=0, max_points=5):
    """
    Formate une liste [(dt, valeur), ...] en ligne compacte du type
    '14h 36° · 16h 37° · 18h 35°'. Sous-échantillonne si le créneau est
    long, pour ne pas surcharger l'encart, tout en gardant systématiquement
    la première et la dernière heure du bloc.
    """
    if not points:
        return ""
    pts = [(dt, v) for dt, v in points if v is not None]
    if not pts:
        return ""
    if len(pts) > max_points:
        step = len(pts) / max_points
        sampled = [pts[int(i * step)] for i in range(max_points - 1)]
        sampled.append(pts[-1])  # toujours garder la dernière heure
        pts = sampled
    return " · ".join(f"{dt.strftime('%Hh')} {v:.{dec}f}{unit}" for dt, v in pts)


def compute_alerts(live, forecast, hourly_fc=None):
    """
    Détecte les alertes météo actives à afficher à côté de la température.
    Combine les mesures en direct (temp, vent, pluie) et les prévisions
    Open-Meteo du jour / des prochains jours (orage, neige, canicule à venir).
    Quand des prévisions horaires (hourly_fc) sont fournies, chaque alerte
    reçoit en plus une estimation de sa plage horaire (window / window_label)
    et un détail heure par heure de la grandeur concernée (hourly_detail /
    detail_label), calculés en cherchant le premier bloc continu d'heures à
    venir qui vérifie la même condition que l'alerte.

    L'alerte ⛈️ Orage (pluie/orage du jour) est volontairement cantonnée à
    la journée en cours (voir _hours_left_today) : elle ne doit annoncer
    qu'un risque du jour même, pas un créneau à plusieurs jours d'écart.

    Retourne une liste de dicts {icon, label, level, kind, window_label,
    detail_label} triée par gravité.
    """
    alerts = []
    temp      = live.get("temp")
    wind_gust = live.get("wind_gust")
    rain_rate = live.get("rain_rate")
    hourly_fc = hourly_fc or []
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2))).replace(tzinfo=None)

    def add_alert(icon, label, level, kind, w, detail_unit="", detail_dec=0):
        window = (w[0], w[1]) if w else None
        detail = w[2] if w else None
        alerts.append({
            "icon": icon, "label": label, "level": level, "kind": kind,
            "window": window, "hourly_detail": detail,
            "detail_unit": detail_unit, "detail_dec": detail_dec,
        })

    # Canicule / forte chaleur (mesure en direct)
    if temp is not None and temp >= 35:
        w = _find_condition_window(hourly_fc, lambda h: h.get("temp") is not None and h["temp"] >= 35, now,
                                    value_fn=lambda h: h.get("temp"))
        add_alert("🌡️", "Canicule", "danger", "canicule", w, " °C", 1)
    elif temp is not None and temp >= 32:
        w = _find_condition_window(hourly_fc, lambda h: h.get("temp") is not None and h["temp"] >= 32, now,
                                    value_fn=lambda h: h.get("temp"))
        add_alert("🌡️", "Forte chaleur", "warning", "canicule", w, " °C", 1)

    # Grand froid / gel (mesure en direct)
    if temp is not None and temp <= -5:
        w = _find_condition_window(hourly_fc, lambda h: h.get("temp") is not None and h["temp"] <= -5, now,
                                    value_fn=lambda h: h.get("temp"))
        add_alert("🥶", "Grand froid", "danger", "gel", w, " °C", 1)
    elif temp is not None and temp < 0:
        w = _find_condition_window(hourly_fc, lambda h: h.get("temp") is not None and h["temp"] < 0, now,
                                    value_fn=lambda h: h.get("temp"))
        add_alert("❄️", "Gel", "info", "gel", w, " °C", 1)

    # Vent (rafales en direct)
    if wind_gust is not None and wind_gust >= 90:
        w = _find_condition_window(hourly_fc, lambda h: h.get("gust") is not None and h["gust"] >= 90, now,
                                    value_fn=lambda h: h.get("gust"))
        add_alert("💨", "Vent violent", "danger", "vent", w, " km/h", 0)
    elif wind_gust is not None and wind_gust >= 60:
        w = _find_condition_window(hourly_fc, lambda h: h.get("gust") is not None and h["gust"] >= 60, now,
                                    value_fn=lambda h: h.get("gust"))
        add_alert("💨", "Vent fort", "warning", "vent", w, " km/h", 0)

    # Pluie intense (mesure en direct) — seuil horaire approximatif (mm/h)
    if rain_rate is not None and rain_rate >= 15:
        w = _find_condition_window(hourly_fc, lambda h: h.get("rain_mm") is not None and h["rain_mm"] >= 3, now,
                                    value_fn=lambda h: h.get("rain_mm"))
        add_alert("🌧️", "Pluie intense", "warning", "pluie", w, " mm/h", 1)

    # Conditions du jour d'après les prévisions (code WMO Open-Meteo)
    today_fc = forecast[0] if forecast else None
    if today_fc:
        code = today_fc.get("code")
        if code in (95, 96, 99):
            rain_proba_fn = lambda h: h.get("rain_proba")
            # Alerte orage/pluie cantonnée à la journée en cours uniquement.
            today_limit = _hours_left_today(now)
            w = _find_condition_window(hourly_fc, lambda h: h.get("code") in (95, 96, 99), now,
                                        limit_hours=today_limit, value_fn=rain_proba_fn)
            if w is None:
                # Repli : le code horaire ne reprend pas toujours exactement
                # le code journalier agrégé (ex: orage annoncé au niveau du
                # jour mais restitué en averses fortes heure par heure).
                w = _find_condition_window(hourly_fc, lambda h: h.get("code") in (80, 81, 82, 95, 96, 99), now,
                                            limit_hours=today_limit, value_fn=rain_proba_fn)
            if w is None:
                # Dernier repli : on affiche la tendance de pluie du jour
                # plutôt que rien, pour toujours donner un détail utile.
                w = _fallback_today_window(hourly_fc, now, rain_proba_fn)
            add_alert("⛈️", "Orage", "danger", "orage", w, "% pluie", 0)
        elif code in (71, 73, 75, 77, 85, 86):
            temp_fn = lambda h: h.get("temp")
            w = _find_condition_window(hourly_fc, lambda h: h.get("code") in (71, 73, 75, 77, 85, 86), now,
                                        value_fn=temp_fn)
            if w is None:
                w = _fallback_today_window(hourly_fc, now, temp_fn)
            add_alert("🌨️", "Neige", "info", "neige", w, " °C", 1)

    # Canicule à venir dans les 3 prochains jours (si pas déjà en cours)
    if not any(a["kind"] == "canicule" and a["level"] == "danger" for a in alerts):
        upcoming_hot = next((d for d in forecast[:3] if d.get("max_t") is not None and d["max_t"] >= 35), None)
        if upcoming_hot:
            dt = datetime.datetime.strptime(upcoming_hot["date"], "%Y-%m-%d")
            w = _find_condition_window(hourly_fc, lambda h: h.get("temp") is not None and h["temp"] >= 35, now,
                                        value_fn=lambda h: h.get("temp"))
            add_alert("🌡️", f"Canicule prévue {dt.strftime('%d/%m')}", "warning", "canicule", w, " °C", 1)

    # Ordre de gravité : danger > warning > info
    order = {"danger": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: order.get(a["level"], 3))

    for a in alerts:
        a["window_label"] = _format_alert_window(a.get("window"), now)
        a["detail_label"] = _format_hourly_detail(
            a.get("hourly_detail"), unit=a.get("detail_unit", ""), dec=a.get("detail_dec", 0)
        )

    return alerts


def next_rain_forecast(hourly_fc, now, threshold=50):
    """
    Cherche, dans les prévisions horaires à venir, la première heure où la
    probabilité de pluie (rain_proba) atteint le seuil donné (50% par
    défaut). Retourne une chaîne prête à afficher, ex. "Pluie prévue à
    21h00" ou "Pluie prévue Ven à 14h00" si c'est un autre jour, ou un
    message par défaut si rien n'est prévu dans les prochaines 168h
    (limite des prévisions horaires Open-Meteo, 7 jours).
    """
    for h in hourly_fc or []:
        t = h.get("time")
        if not t:
            continue
        try:
            dt = datetime.datetime.fromisoformat(t)
        except ValueError:
            continue
        if dt <= now:
            continue
        proba = h.get("rain_proba")
        if proba is not None and proba >= threshold:
            if dt.date() == now.date():
                return f"Pluie prévue à {dt.strftime('%Hh%M')}"
            return f"Pluie prévue {_JOURS_ABBR_FR[dt.weekday()]} à {dt.strftime('%Hh%M')}"
    return "Pas de pluie prévue"

# ── 5. Génération HTML ────────────────────────────────────────────────────────
def build_index(live, hourly, forecast, hourly_fc=None, records=None, hiking_html=""):
    def val(v, unit="", dec=1):
        if v is None: return "—"
        return f"{v:.{dec}f}{unit}"

    def wind_dir_str(deg):
        if deg is None: return "—"
        dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSO","SO","OSO","O","ONO","NO","NNO"]
        return dirs[round(deg/22.5) % 16]

    # Lever/coucher du soleil
    sunrise, sunset, day_len = sun_times()
    day_h, day_m = divmod(day_len, 60)
    day_str = f"{day_h}h{day_m:02d}min"

    # Phase de lune
    moon_emoji, moon_name = moon_phase_info()

    # Records
    rec = records or {}
    def rec_fmt(key, unit):
        r = rec.get(key, {})
        if not r or r.get("val") is None: return "—", "—"
        d = r["date"]
        dt = datetime.datetime.strptime(d, "%Y-%m-%d")
        return f"{r['val']:.1f}{unit}", dt.strftime("%d/%m/%Y")
    rec_max_t, rec_max_t_date = rec_fmt("max_t", " °C")
    rec_min_t, rec_min_t_date = rec_fmt("min_t", " °C")
    rec_rain,  rec_rain_date  = rec_fmt("max_rain", " mm")
    rec_rain_rate, rec_rain_rate_date = rec_fmt("max_rain_rate", " mm/h")
    rec_wind,  rec_wind_date  = rec_fmt("max_wind", " km/h")

    # Pluie prévue aujourd'hui (cumul journalier Open-Meteo)
    today_fc = forecast[0] if forecast else None
    rain_forecast_today = today_fc.get("rain") if today_fc else None
    if rain_forecast_today is None:
        rain_forecast_str = "—"
    elif rain_forecast_today < 0.1:
        rain_forecast_str = "0 mm"
    else:
        rain_forecast_str = f"{rain_forecast_today:.1f} mm"

    def weather_icon(solar, rain_rate, hum, temp):
        """Icône météo dynamique basée sur les capteurs."""
        if solar is None: solar = 0
        if rain_rate is None: rain_rate = 0
        if hum is None: hum = 50
        if temp is None: temp = 15
        if rain_rate > 2:   return "🌧️", "Pluie"
        if rain_rate > 0.1: return "🌦️", "Averses"
        if solar > 500:     return "☀️", "Ensoleillé"
        if solar > 200:     return "⛅", "Partiellement nuageux"
        if solar > 50:      return "🌤️", "Peu nuageux"
        if hum > 90:        return "🌫️", "Brouillard"
        if temp < 0:        return "❄️", "Gel"
        return "☁️", "Nuageux"

    def trend_arrow(hourly):
        """Tendance température sur les 3 dernières heures."""
        if len(hourly) < 3: return "", ""
        temps = [h["temp"] for h in hourly[-3:] if h.get("temp") is not None]
        if len(temps) < 2: return "", ""
        diff = temps[-1] - temps[0]
        if diff > 0.5:   return "↑", f"+{diff:.1f}°C en 3h"
        if diff < -0.5:  return "↓", f"{diff:.1f}°C en 3h"
        return "→", "Stable"

    icon, condition = weather_icon(live.get("solar"), live.get("rain_rate"), live.get("hum"), live.get("temp"))
    arrow, trend_txt = trend_arrow(hourly)

    # Prochaine pluie prévue (affichée à côté de la tendance de température)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2))).replace(tzinfo=None)
    rain_forecast_txt = next_rain_forecast(hourly_fc, now)

    # Alertes météo (orage, canicule, neige, vent, gel...) à côté de la température
    alerts = compute_alerts(live, forecast, hourly_fc)
    # Couleur de vigilance (jaune/orange/rouge, façon Météo-France) selon la
    # gravité de l'alerte (level), indépendamment de son type.
    vigilance_class_map = {
        "danger":  "vigilance-rouge",
        "warning": "vigilance-orange",
        "info":    "vigilance-jaune",
    }
    def _render_hero_alert(a):
        cls = vigilance_class_map.get(a["level"], "vigilance-jaune")
        sub = f'{a["window_label"]} · Restez vigilant' if a.get("window_label") else "Restez vigilant"
        detail_html = ""
        if a.get("detail_label"):
            detail_html = f'<div class="hero-alert-detail">{a["detail_label"]}</div>'
        return (
            f'<div class="hero-alert-box {cls}">'
            f'<div class="hero-alert-title">{a["icon"]} {a["label"]}</div>'
            f'<div class="hero-alert-sub">{sub}</div>'
            f'{detail_html}'
            f'</div>'
        )
    hero_alerts_html = "".join(_render_hero_alert(a) for a in alerts)
    if hero_alerts_html:
        hero_alerts_html = f'<div class="hero-alerts">{hero_alerts_html}</div>'

    # Records du jour (min/max depuis les données horaires d'AUJOURD'HUI seulement)
    today_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2))).strftime("%Y-%m-%d")
    today_temps = [h["temp"] for h in hourly if h.get("temp") is not None and h.get("time","").startswith(today_str)]
    if not today_temps:  # fallback si pas encore de données aujourd'hui
        today_temps = [h["temp"] for h in hourly if h.get("temp") is not None]
    today_max = f"{max(today_temps):.1f} °C" if today_temps else "—"
    today_min = f"{min(today_temps):.1f} °C" if today_temps else "—"

    # Données horaires JS
    hourly_js = json.dumps(hourly)

    # Prévisions JS
    forecast_js = json.dumps(forecast)
    hourly_forecast_js = json.dumps(hourly_fc or [])

    # Codes météo WMO → icône
    wmo_icons = {
        0:"☀️",                    # Ciel dégagé
        1:"🌤️",                    # Principalement clair
        2:"⛅",                     # Partiellement nuageux
        3:"☁️",                    # Couvert
        45:"🌫️",48:"🌫️",           # Brouillard
        51:"🌦️",53:"🌦️",55:"🌦️",  # Bruine légère à dense
        56:"🌧️",57:"🌧️",          # Bruine verglaçante
        61:"🌦️",63:"🌧️",65:"🌧️",  # Pluie légère → forte
        66:"🌨️",67:"🌨️",          # Pluie verglaçante
        71:"🌨️",73:"🌨️",75:"❄️",  # Neige légère → forte
        77:"❄️",                   # Grains de neige
        80:"🌦️",81:"🌧️",82:"⛈️",  # Averses légères → violentes
        85:"🌨️",86:"❄️",          # Averses de neige
        95:"⛈️",96:"⛈️",99:"⛈️",  # Orages
    }
    wmo_js = json.dumps(wmo_icons)

    # Noms des jours
    jours = ["Dim","Lun","Mar","Mer","Jeu","Ven","Sam"]

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="3600">
<title>Météo Colmar-Mittelharth</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#f5f5f3;--surface:#fff;--surface-muted:#f0efec;
  --text:#0b0b0b;--text-secondary:#52514e;--text-muted:#898781;
  --border:rgba(11,11,11,.10);--radius:8px;
  --accent:#2a78d6;--accent-bg:#e6f1fb;--accent-border:rgba(42,120,214,.3);
  --grid:#e1e0d9;
}}
@media(prefers-color-scheme:dark){{
  :root{{--bg:#111110;--surface:#1e1e1c;--surface-muted:#252523;
    --text:#fff;--text-secondary:#c3c2b7;--text-muted:#898781;
    --border:rgba(255,255,255,.10);--grid:#2c2c2a;
    --accent:#3987e5;--accent-bg:rgba(57,135,229,.12);--accent-border:rgba(57,135,229,.4);}}
}}
html[data-theme="dark"]{{
  --bg:#111110;--surface:#1e1e1c;--surface-muted:#252523;
  --text:#fff;--text-secondary:#c3c2b7;--text-muted:#898781;
  --border:rgba(255,255,255,.10);--grid:#2c2c2a;
  --accent:#3987e5;--accent-bg:rgba(57,135,229,.12);--accent-border:rgba(57,135,229,.4);
}}
html[data-theme="light"]{{
  --bg:#f5f5f3;--surface:#fff;--surface-muted:#f0efec;
  --text:#0b0b0b;--text-secondary:#52514e;--text-muted:#898781;
  --border:rgba(11,11,11,.10);--accent:#2a78d6;--accent-bg:#e6f1fb;--accent-border:rgba(42,120,214,.3);
  --grid:#e1e0d9;
}}
.theme-toggle{{background:var(--surface-muted);border:0.5px solid var(--border);border-radius:99px;width:34px;height:34px;font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--text)}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:1.5rem 1rem;min-height:100vh}}
.container{{max-width:920px;margin:0 auto}}
header{{margin-bottom:1.5rem;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:8px}}
header h1{{font-size:20px;font-weight:500}}
header p{{font-size:13px;color:var(--text-muted)}}
.updated{{font-size:12px;color:var(--text-muted);background:var(--surface-muted);padding:4px 10px;border-radius:99px;border:0.5px solid var(--border)}}
.nav-row{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:1.5rem}}
.live-clock{{font-size:19px;font-weight:700;color:var(--text);font-variant-numeric:tabular-nums;background:var(--surface-muted);padding:9px 20px;border-radius:99px;border:0.5px solid var(--border);white-space:nowrap}}
nav{{display:flex;gap:8px;flex-wrap:wrap}}
nav a{{font-size:13px;padding:6px 14px;border-radius:var(--radius);border:0.5px solid var(--border);background:var(--surface-muted);color:var(--text-secondary);text-decoration:none}}
nav a.active{{background:var(--accent-bg);color:var(--accent);border-color:var(--accent-border)}}

/* Alertes */
.alert{{border-radius:var(--radius);padding:12px 16px;margin-bottom:1rem;font-size:14px;font-weight:500;border:0.5px solid}}
.alert-canicule{{background:rgba(216,90,48,0.12);color:#d85a30;border-color:rgba(216,90,48,0.3)}}
.alert-gel{{background:rgba(42,120,214,0.12);color:#2a78d6;border-color:rgba(42,120,214,0.3)}}
.alert-orage{{background:rgba(216,30,30,0.12);color:#d81e1e;border-color:rgba(216,30,30,0.3)}}
.alert-vent{{background:rgba(130,60,180,0.12);color:#8a3cb4;border-color:rgba(130,60,180,0.3)}}

/* Couleurs de vigilance (Météo-France : jaune / orange / rouge) selon la
   gravité de l'alerte (level), indépendamment de son type (kind) */
.vigilance-jaune{{background:rgba(237,193,0,0.16);color:#8a6d00;border-color:rgba(237,193,0,0.45)}}
.vigilance-orange{{background:rgba(237,140,0,0.16);color:#c26a00;border-color:rgba(237,140,0,0.45)}}
.vigilance-rouge{{background:rgba(216,30,30,0.16);color:#d81e1e;border-color:rgba(216,30,30,0.45)}}
@media(prefers-color-scheme:dark){{
  .vigilance-jaune{{color:#edc100}}
  .vigilance-orange{{color:#ed8c00}}
  .vigilance-rouge{{color:#ff5c5c}}
}}

/* Encart de vigilance dans le hero, à droite de la température */
.hero-alerts{{display:flex;flex-direction:column;gap:10px;margin-left:auto;flex-shrink:0;max-width:320px}}
.hero-alert-box{{display:flex;flex-direction:column;gap:4px;padding:16px 22px;border-radius:var(--radius);border:0.5px solid}}
.hero-alert-title{{font-size:16px;font-weight:600;white-space:nowrap}}
.hero-alert-sub{{font-size:13px;font-weight:400;opacity:.85}}
.hero-alert-detail{{font-size:12.5px;font-weight:400;opacity:.7;margin-top:2px;font-variant-numeric:tabular-nums}}
@media(max-width:600px){{.hero-alerts{{margin-left:0;width:100%;max-width:none}} .hero-alert-title{{white-space:normal}}}}

/* Hero météo */
.hero{{background:var(--surface);border-radius:16px;border:0.5px solid var(--border);padding:1.5rem;margin-bottom:1.5rem}}
.hero-top{{display:flex;gap:1.5rem;align-items:center;flex-wrap:wrap;margin-bottom:1.5rem}}
.hero-icon{{font-size:64px;line-height:1;flex-shrink:0}}
.hero-main{{flex:1;min-width:160px}}
.hero-temp{{font-size:52px;font-weight:300;line-height:1;margin-bottom:4px;display:flex;align-items:baseline;gap:14px}}
.hero-rain-today{{font-size:24px;font-weight:600;color:var(--text-secondary);display:flex;align-items:center;gap:6px}}
.hero-cond{{font-size:16px;color:var(--text-secondary);margin-bottom:8px}}
.hero-trend{{font-size:13px;color:var(--text-muted);display:flex;align-items:center;gap:6px;flex-wrap:wrap}}
.trend-up{{color:#d85a30}} .trend-down{{color:#2a78d6}} .trend-stable{{color:var(--text-muted)}}
.hero-records{{display:flex;gap:16px;font-size:13px;margin-top:8px}}
.hero-records span{{color:var(--text-muted)}} .hero-records b{{color:var(--text)}}
.hero-chart{{width:100%;height:180px;position:relative}}
@media(max-width:600px){{.hero-chart{{height:150px}}}}

/* Grille de cartes */
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:1.5rem}}
.card{{background:var(--surface);border-radius:var(--radius);border:0.5px solid var(--border);padding:1rem 1.25rem}}
.card-icon{{font-size:20px;margin-bottom:6px}}
.card-label{{font-size:12px;color:var(--text-muted);margin-bottom:4px}}
.card-value{{font-size:24px;font-weight:500;line-height:1}}
.card-sub{{font-size:12px;color:var(--text-muted);margin-top:4px}}

/* Prévisions 7 jours / heure par heure (un seul bloc, vue basculable) */
.hourly-scroll{{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px;scrollbar-width:thin}}
.hourly-scroll::-webkit-scrollbar{{height:6px}}
.hourly-scroll::-webkit-scrollbar-thumb{{background:var(--border);border-radius:99px}}
.hourly-item{{flex:0 0 auto;min-width:64px;text-align:center;padding:12px 8px;border-radius:var(--radius);background:var(--surface-muted);cursor:pointer;border:1.5px solid transparent;transition:border-color .15s}}
.hourly-item:hover{{border-color:var(--accent-border)}}
.hourly-item.now{{background:var(--accent-bg);border-color:var(--accent-border)}}
.hourly-hour{{font-size:12px;color:var(--text-secondary);font-weight:600;margin-bottom:6px}}
.hourly-icon{{font-size:24px;margin:4px 0;line-height:1}}
.hourly-temp{{font-size:15px;font-weight:600}}
.hourly-rain{{font-size:11px;color:var(--accent);margin-top:4px;font-weight:500}}

/* Prévisions */
.forecast{{background:var(--surface);border-radius:12px;border:0.5px solid var(--border);padding:1.5rem;margin-bottom:1.5rem}}
.forecast-header{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:1.25rem}}
.forecast-title{{font-size:14px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em}}
.forecast-back{{font-size:12px;font-weight:500;padding:5px 14px;border-radius:99px;border:0.5px solid var(--accent-border);background:var(--accent-bg);color:var(--accent);cursor:pointer;font-family:inherit}}
.forecast-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:12px}}
@media(max-width:600px){{.forecast-grid{{grid-template-columns:repeat(4,1fr)}}}}
.forecast-day{{text-align:center;padding:16px 6px;border-radius:var(--radius);background:var(--surface-muted);cursor:pointer;border:1.5px solid transparent;transition:border-color .15s}}
.forecast-day:hover{{border-color:var(--accent-border)}}
.forecast-day-name{{font-size:15px;color:var(--text-secondary);margin-bottom:8px;font-weight:600}}
.forecast-icon{{font-size:36px;margin:8px 0;line-height:1}}
.forecast-max{{font-size:19px;font-weight:700;color:#d85a30}}
.forecast-min{{font-size:16px;color:#2a78d6;margin-top:2px}}
.forecast-rain{{font-size:14px;color:var(--text-muted);margin-top:6px;font-weight:500}}

/* Sections */
.section{{background:var(--surface);border-radius:12px;border:0.5px solid var(--border);padding:1.5rem;margin-bottom:1rem}}
.section-title{{font-size:12px;font-weight:500;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:1rem}}
.hero .detail-grid{{margin-top:1.25rem}}
.detail-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}}
.detail-item{{padding:10px 12px;background:var(--surface-muted);border-radius:var(--radius)}}
.detail-label{{font-size:11px;color:var(--text-muted);margin-bottom:2px}}
.detail-value{{font-size:16px;font-weight:500}}
footer{{text-align:center;font-size:12px;color:var(--text-muted);margin-top:2rem;padding-top:1rem;border-top:0.5px solid var(--border)}}

/* Indice rando */
.hiking-index{{background:var(--surface);border-radius:12px;border:0.5px solid var(--border);padding:1.5rem;margin-bottom:1.5rem}}
.hiking-index h2{{font-size:16px;font-weight:500;margin-bottom:6px}}
.hiking-intro{{font-size:12px;color:var(--text-muted);margin-bottom:1rem;line-height:1.5}}
.hiking-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}}
.hiking-card{{background:var(--surface-muted);border-radius:var(--radius);padding:1rem 1.1rem;border:0.5px solid var(--border)}}
.hiking-card h3{{font-size:14px;font-weight:500;margin-bottom:6px}}
.hiking-card .alt{{color:var(--text-muted);font-weight:400;font-size:12px}}
.hiking-score{{font-size:14px;font-weight:500;margin-bottom:4px}}
.hiking-window{{font-size:12px;color:var(--text-secondary);margin-bottom:4px}}
.hiking-note{{font-size:11px;color:var(--text-muted);line-height:1.4}}
.hiking-best{{display:flex;align-items:center;gap:14px;background:var(--accent-bg);border:0.5px solid var(--accent-border);border-radius:var(--radius);padding:12px 16px;margin-bottom:.75rem}}
.hiking-best--unknown{{background:var(--surface-muted);border-color:var(--border)}}
.hiking-best-emoji{{font-size:28px;line-height:1;flex-shrink:0}}
.hiking-best-title{{font-size:14px;color:var(--text)}}
.hiking-best-title b{{color:var(--accent)}}
.hiking-best-sub{{font-size:12px;color:var(--text-secondary);margin-top:2px}}
.hiking-details summary{{cursor:pointer;font-size:13px;color:var(--accent);font-weight:500;list-style:none;padding:4px 0;user-select:none}}
.hiking-details summary::-webkit-details-marker{{display:none}}
.hiking-details summary::before{{content:'▸ ';display:inline-block;transition:transform .15s}}
.hiking-details[open] summary::before{{transform:rotate(90deg)}}
.hiking-details .hiking-intro{{margin-top:.75rem}}
.hiking-details .hiking-grid{{margin-top:.75rem}}
</style>
</head>
<body>
<div class="container">

<header>
  <div>
    <h1>🌤 Météo Colmar-Mittelharth</h1>
    <p>Station personnelle · Colmar (68) · Alsace</p>
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <span class="updated">Mis à jour : {live['updated_at']}</span>
    <button class="theme-toggle" id="themeToggle" title="Basculer mode clair/sombre">🌙</button>
  </div>
</header>

<div class="nav-row">
  <nav>
    <a href="index.html" class="active">⚡ En direct</a>
    <a href="dashboard.html">📊 Historique</a>
    <a href="climate.html">🌍 Climatologie</a>
  </nav>
  <span class="live-clock" id="liveClock">--:-- · --/--</span>
</div>

<!-- Hero -->
<div class="hero">
  <div class="hero-top">
    <div class="hero-icon">{icon}</div>
    <div class="hero-main">
      <div class="hero-temp">{val(live['temp'])} °C <span class="hero-rain-today">🌧 {val(live['rain_daily'],' mm')}</span> <span class="hero-rain-today">☀️ UV {val(live['uvi'],'',0)}</span></div>
      <div class="hero-cond">{condition} · Ressenti {val(live['temp_feels'])} °C</div>
      <div class="hero-trend">
        <span class="trend-{'up' if arrow == '↑' else 'down' if arrow == '↓' else 'stable'}">{arrow}</span>
        {trend_txt} · 🌧 {rain_forecast_txt}
      </div>
      <div class="hero-records">
        <span>Aujourd'hui · Max <b>{today_max}</b> · Min <b>{today_min}</b></span>
        <span>{moon_emoji} {moon_name}</span>
      </div>
    </div>
    {hero_alerts_html}
  </div>
  <div class="detail-grid">
    <div class="detail-item"><div class="detail-label">🌅 Lever</div><div class="detail-value">{sunrise or '—'}</div></div>
    <div class="detail-item"><div class="detail-label">🌇 Coucher</div><div class="detail-value">{sunset or '—'}</div></div>
    <div class="detail-item"><div class="detail-label">⏱ Durée du jour</div><div class="detail-value">{day_str}</div></div>
  </div>
</div>

<!-- Prévisions 7 jours ⇄ heure par heure (basculable) -->
<div class="forecast">
  <div class="forecast-header">
    <div class="forecast-title" id="forecastTitle">Prévisions 7 jours</div>
    <button class="forecast-back" id="forecastBack" style="display:none" onclick="showDaysView()">← 7 jours</button>
  </div>
  <div class="forecast-grid" id="forecastGrid"></div>
</div>

<!-- Graphique température 24h -->
<div class="hero">
  <div class="hero-chart">
    <canvas id="miniChart"></canvas>
  </div>
</div>

<!-- Cartes de données -->
<div class="grid">
  <div class="card">
    <div class="card-icon">💧</div>
    <div class="card-label">Humidité</div>
    <div class="card-value">{val(live['hum'],' %',0)}</div>
    <div class="card-sub">Point de rosée : {val(live['dew'],' °C')}</div>
  </div>
  <div class="card">
    <div class="card-icon">📊</div>
    <div class="card-label">Pression</div>
    <div class="card-value">{val(live['pressure'],' hPa',0)}</div>
  </div>
  <div class="card">
    <div class="card-icon">💨</div>
    <div class="card-label">Vent</div>
    <div class="card-value">{val(live['wind_speed'],' km/h',0)}</div>
    <div class="card-sub">Rafale : {val(live['wind_gust'],' km/h',0)} · {wind_dir_str(live['wind_dir'])}</div>
  </div>
  <div class="card">
    <div class="card-icon">🌧</div>
    <div class="card-label">Pluie aujourd'hui</div>
    <div class="card-value">{val(live['rain_daily'],' mm')}</div>
    <div class="card-sub">Taux : {val(live['rain_rate'],' mm/h')}</div>
  </div>
  <div class="card">
    <div class="card-icon">☀</div>
    <div class="card-label">Rayonnement</div>
    <div class="card-value">{val(live['solar'],' W/m²',0)}</div>
    <div class="card-sub">Indice UV : {val(live['uvi'],'',0)}</div>
  </div>
</div>

<div class="section">
  <div class="section-title">Précipitations cumulées</div>
  <div class="detail-grid">
    <div class="detail-item"><div class="detail-label">Aujourd'hui</div><div class="detail-value">{val(live['rain_daily'],' mm')}</div></div>
    <div class="detail-item"><div class="detail-label">Ce mois</div><div class="detail-value">{val(live['rain_monthly'],' mm')}</div></div>
    <div class="detail-item"><div class="detail-label">Cette année</div><div class="detail-value">{val(live['rain_yearly'],' mm')}</div></div>
  </div>
</div>

{hiking_html}

<div class="section">
  <div class="section-title">🏆 Records de la station (depuis jan. 2025)</div>
  <div class="detail-grid">
    <div class="detail-item" style="border-left:3px solid #d85a30">
      <div class="detail-label">Température max</div>
      <div class="detail-value" style="color:#d85a30">{rec_max_t}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:2px">{rec_max_t_date}</div>
    </div>
    <div class="detail-item" style="border-left:3px solid #2a78d6">
      <div class="detail-label">Température min</div>
      <div class="detail-value" style="color:#2a78d6">{rec_min_t}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:2px">{rec_min_t_date}</div>
    </div>
    <div class="detail-item" style="border-left:3px solid #1baf7a">
      <div class="detail-label">Pluie max (jour)</div>
      <div class="detail-value" style="color:#1baf7a">{rec_rain}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:2px">{rec_rain_date}</div>
    </div>
    <div class="detail-item" style="border-left:3px solid #2a78d6">
      <div class="detail-label">Averse la plus intense</div>
      <div class="detail-value" style="color:#2a78d6">{rec_rain_rate}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:2px">{rec_rain_rate_date}</div>
    </div>
    <div class="detail-item" style="border-left:3px solid #eda100">
      <div class="detail-label">Rafale max</div>
      <div class="detail-value" style="color:#eda100">{rec_wind}</div>
      <div style="font-size:11px;color:var(--text-muted);margin-top:2px">{rec_wind_date}</div>
    </div>
  </div>
</div>

<footer>
  Station météo personnelle · Colmar-Mittelharth · Alsace · <a href="https://open-meteo.com" style="color:var(--accent)">Prévisions Open-Meteo</a><br>
  <img src="https://mittelharth.goatcounter.com/counter/TOTAL.svg?style=flat" alt="visites" style="margin-top:8px;vertical-align:middle;opacity:0.7">
</footer>
</div>
<script data-goatcounter="https://mittelharth.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>

<script>
const hourly   = {hourly_js};
const forecast = {forecast_js};
const hourlyForecast = {hourly_forecast_js};
const WMO      = {wmo_js};
const JOURS    = {json.dumps(jours)};

// ── Fêtes du jour (calendrier traditionnel français) ─────────────────────────
const FETES = {{
  "01-01":"Jour de l'An","01-02":"Saint Basile","01-03":"Sainte Geneviève","01-04":"Saint Odilon","01-05":"Saint Édouard",
  "01-06":"Saint Mélaine","01-07":"Saint Raymond","01-08":"Saint Lucien","01-09":"Sainte Alix","01-10":"Saint Guillaume",
  "01-11":"Saint Paulin","01-12":"Sainte Tatiana","01-13":"Sainte Yvette","01-14":"Sainte Nina","01-15":"Saint Rémi",
  "01-16":"Saint Marcel","01-17":"Sainte Roseline","01-18":"Sainte Prisca","01-19":"Saint Marius","01-20":"Saint Sébastien",
  "01-21":"Sainte Agnès","01-22":"Saint Vincent","01-23":"Saint Barnard","01-24":"Saint François de Sales","01-25":"Conversion de Paul",
  "01-26":"Sainte Paule","01-27":"Sainte Angèle","01-28":"Saint Thomas d'Aquin","01-29":"Saint Gildas","01-30":"Sainte Martine",
  "01-31":"Sainte Marcelle","02-01":"Sainte Ella","02-02":"Présentation","02-03":"Saint Blaise","02-04":"Sainte Véronique",
  "02-05":"Sainte Agathe","02-06":"Saint Gaston","02-07":"Sainte Eugénie","02-08":"Sainte Jacqueline","02-09":"Sainte Apolline",
  "02-10":"Saint Arnaud","02-11":"Notre-Dame de Lourdes","02-12":"Saint Félix","02-13":"Sainte Béatrice","02-14":"Saint Valentin",
  "02-15":"Saint Claude","02-16":"Sainte Julienne","02-17":"Saint Alexis","02-18":"Sainte Bernadette","02-19":"Saint Gabin",
  "02-20":"Sainte Aimée","02-21":"Saint Pierre-Damien","02-22":"Sainte Isabelle","02-23":"Saint Lazare","02-24":"Saint Modeste",
  "02-25":"Saint Roméo","02-26":"Saint Nestor","02-27":"Sainte Honorine","02-28":"Saint Romain","02-29":"Saint Auguste",
  "03-01":"Saint Aubin","03-02":"Saint Charles le Bon","03-03":"Saint Guénolé","03-04":"Saint Casimir","03-05":"Sainte Olive",
  "03-06":"Sainte Colette","03-07":"Sainte Félicité","03-08":"Saint Jean de Dieu","03-09":"Sainte Françoise","03-10":"Saint Vivien",
  "03-11":"Sainte Rosine","03-12":"Sainte Justine","03-13":"Saint Rodrigue","03-14":"Sainte Mathilde","03-15":"Saint Louise de Marillac",
  "03-16":"Sainte Bénédicte","03-17":"Saint Patrick","03-18":"Saint Cyrille","03-19":"Saint Joseph","03-20":"Saint Herbert",
  "03-21":"Sainte Clémence","03-22":"Sainte Léa","03-23":"Saint Victorien","03-24":"Saint Catherine de Suède","03-25":"Annonciation",
  "03-26":"Sainte Larissa","03-27":"Saint Habib","03-28":"Saint Gontran","03-29":"Sainte Gwladys","03-30":"Saint Amédée",
  "03-31":"Saint Benjamin","04-01":"Saint Hugues","04-02":"Sainte Sandrine","04-03":"Saint Richard","04-04":"Saint Isidore",
  "04-05":"Sainte Irène","04-06":"Saint Marcellin","04-07":"Saint Jean-Baptiste de la Salle","04-08":"Sainte Julie","04-09":"Saint Gautier",
  "04-10":"Saint Fulbert","04-11":"Saint Stanislas","04-12":"Saint Jules","04-13":"Sainte Ida","04-14":"Saint Maxime",
  "04-15":"Saint Paterne","04-16":"Saint Benoît-Joseph","04-17":"Saint Anicet","04-18":"Saint Parfait","04-19":"Sainte Emma",
  "04-20":"Sainte Odette","04-21":"Saint Anselme","04-22":"Saint Alexandre","04-23":"Saint Georges","04-24":"Saint Fidèle",
  "04-25":"Saint Marc","04-26":"Sainte Alida","04-27":"Sainte Zita","04-28":"Sainte Valérie","04-29":"Saint Catherine de Sienne",
  "04-30":"Saint Robert","05-01":"Fête du Travail","05-02":"Saint Boris","05-03":"Saints Philippe, Jacques","05-04":"Saint Sylvain",
  "05-05":"Sainte Judith","05-06":"Sainte Prudence","05-07":"Sainte Gisèle","05-08":"Victoire 1945","05-09":"Saint Pacôme",
  "05-10":"Sainte Solange","05-11":"Sainte Estelle","05-12":"Saint Achille","05-13":"Sainte Rolande","05-14":"Saint Matthias",
  "05-15":"Sainte Denise","05-16":"Saint Honoré","05-17":"Saint Pascal","05-18":"Saint Éric","05-19":"Saint Yves",
  "05-20":"Saint Bernardin","05-21":"Saint Constantin","05-22":"Saint Émile","05-23":"Saint Didier","05-24":"Saint Donatien",
  "05-25":"Sainte Sophie","05-26":"Saint Bérenger","05-27":"Saint Augustin","05-28":"Saint Germain","05-29":"Saint Aymar",
  "05-30":"Saint Ferdinand","05-31":"Visitation","06-01":"Saint Justin","06-02":"Sainte Blandine","06-03":"Saint Kévin",
  "06-04":"Sainte Clotilde","06-05":"Saint Igor","06-06":"Saint Norbert","06-07":"Saint Gilbert","06-08":"Saint Médard",
  "06-09":"Sainte Diane","06-10":"Saint Landry","06-11":"Saint Barnabé","06-12":"Saint Guy","06-13":"Saint Antoine de Padoue",
  "06-14":"Saint Élisée","06-15":"Sainte Germaine","06-16":"Saint J.-F. Régis","06-17":"Saint Hervé","06-18":"Saint Léonce",
  "06-19":"Saint Romuald","06-20":"Saint Silvère","06-21":"Saint Rodolphe","06-22":"Saint Alban","06-23":"Sainte Audrey",
  "06-24":"Saint Jean-Baptiste","06-25":"Saint Prosper","06-26":"Saint Anthelme","06-27":"Saint Fernand","06-28":"Saint Irénée",
  "06-29":"Saints Pierre, Paul","06-30":"Saint Martial","07-01":"Saint Thierry","07-02":"Saint Martinien","07-03":"Saint Thomas",
  "07-04":"Saint Florent","07-05":"Saint Antoine","07-06":"Sainte Mariette","07-07":"Saint Raoul","07-08":"Saint Thibaut",
  "07-09":"Sainte Amandine","07-10":"Saint Ulrich","07-11":"Saint Benoît","07-12":"Saint Olivier","07-13":"Saints Henri, Joël",
  "07-14":"Fête Nationale","07-15":"Saint Donald","07-16":"N.-D. du Mont-Carmel","07-17":"Sainte Charlotte","07-18":"Saint Frédéric",
  "07-19":"Saint Arsène","07-20":"Sainte Marina","07-21":"Saint Victor","07-22":"Sainte Marie-Madeleine","07-23":"Sainte Brigitte",
  "07-24":"Sainte Christine","07-25":"Saint Jacques","07-26":"Saints Anne, Joachim","07-27":"Sainte Nathalie","07-28":"Saint Samson",
  "07-29":"Sainte Marthe","07-30":"Sainte Juliette","07-31":"Saint Ignace de Loyola","08-01":"Saint Alphonse","08-02":"Saint Julien Eymard",
  "08-03":"Sainte Lydie","08-04":"Saint Jean-Marie Vianney","08-05":"Saint Abel","08-06":"Transfiguration","08-07":"Saint Gaétan",
  "08-08":"Saint Dominique","08-09":"Saint Amour","08-10":"Saint Laurent","08-11":"Sainte Claire","08-12":"Sainte Clarisse",
  "08-13":"Saint Hippolyte","08-14":"Saint Evrard","08-15":"Assomption","08-16":"Saint Armel","08-17":"Saint Hyacinthe",
  "08-18":"Sainte Hélène","08-19":"Saint Jean Eudes","08-20":"Saint Bernard","08-21":"Saint Christophe","08-22":"Saint Fabrice",
  "08-23":"Sainte Rose de Lima","08-24":"Saint Barthélémy","08-25":"Saint Louis","08-26":"Sainte Natacha","08-27":"Sainte Monique",
  "08-28":"Saint Augustin","08-29":"Sainte Sabine","08-30":"Saint Fiacre","08-31":"Saint Aristide","09-01":"Saint Gilles",
  "09-02":"Sainte Ingrid","09-03":"Saint Grégoire","09-04":"Sainte Rosalie","09-05":"Sainte Raïssa","09-06":"Saint Bertrand",
  "09-07":"Sainte Reine","09-08":"Nativité de N.-D.","09-09":"Saint Alain","09-10":"Sainte Inès","09-11":"Saint Adelphe",
  "09-12":"Saint Apollinaire","09-13":"Saint Aimé","09-14":"La Sainte-Croix","09-15":"Saint Roland","09-16":"Sainte Édith",
  "09-17":"Saint Renaud","09-18":"Sainte Nadège","09-19":"Sainte Émilie","09-20":"Saint Davy","09-21":"Saint Matthieu",
  "09-22":"Saint Maurice","09-23":"Saint Constant","09-24":"Sainte Thècle","09-25":"Saint Hermann","09-26":"Saints Côme, Damien",
  "09-27":"Saint Vincent de Paul","09-28":"Saint Venceslas","09-29":"Saints Michel, Gabriel, Raphaël","09-30":"Saint Jérôme","10-01":"Sainte Thérèse de l'Enfant-Jésus",
  "10-02":"Saint Léger","10-03":"Saint Gérard","10-04":"Saint François d'Assise","10-05":"Sainte Fleur","10-06":"Saint Bruno",
  "10-07":"Saint Serge","10-08":"Sainte Pélagie","10-09":"Saint Denis","10-10":"Saint Ghislain","10-11":"Saint Firmin",
  "10-12":"Saint Wilfried","10-13":"Saint Géraud","10-14":"Saint Juste","10-15":"Sainte Thérèse d'Avila","10-16":"Sainte Edwige",
  "10-17":"Saint Baudouin","10-18":"Saint Luc","10-19":"Saint René","10-20":"Sainte Adeline","10-21":"Sainte Céline",
  "10-22":"Sainte Élodie","10-23":"Saint Jean de Capistran","10-24":"Saint Florentin","10-25":"Saint Crépin","10-26":"Saint Dimitri",
  "10-27":"Sainte Émeline","10-28":"Saints Simon, Jude","10-29":"Saint Narcisse","10-30":"Sainte Bienvenue","10-31":"Saint Quentin",
  "11-01":"Toussaint","11-02":"Défunts","11-03":"Saint Hubert","11-04":"Saint Charles","11-05":"Sainte Sylvie",
  "11-06":"Sainte Bertille","11-07":"Sainte Carine","11-08":"Saint Geoffroy","11-09":"Saint Théodore","11-10":"Saint Léon",
  "11-11":"Armistice 1918","11-12":"Saint Christian","11-13":"Saint Brice","11-14":"Saint Sidoine","11-15":"Saint Albert",
  "11-16":"Sainte Marguerite","11-17":"Sainte Élisabeth","11-18":"Sainte Aude","11-19":"Saint Tanguy","11-20":"Saint Edmond",
  "11-21":"Présentation de Marie","11-22":"Sainte Cécile","11-23":"Saint Clément","11-24":"Sainte Flora","11-25":"Sainte Catherine",
  "11-26":"Sainte Delphine","11-27":"Saint Séverin","11-28":"Saint Jacques de la Marche","11-29":"Saint Saturnin","11-30":"Saint André",
  "12-01":"Sainte Florence","12-02":"Sainte Viviane","12-03":"Saint François-Xavier","12-04":"Sainte Barbara","12-05":"Saint Gérald",
  "12-06":"Saint Nicolas","12-07":"Saint Ambroise","12-08":"Immaculée Conception","12-09":"Saint Pierre Fourier","12-10":"Saint Romaric",
  "12-11":"Saint Daniel","12-12":"Sainte Chantal","12-13":"Sainte Lucie","12-14":"Sainte Odile","12-15":"Sainte Ninon",
  "12-16":"Sainte Alice","12-17":"Saint Judicaël","12-18":"Saint Gatien","12-19":"Saint Urbain","12-20":"Saint Théophile",
  "12-21":"Saint Pierre Canisius","12-22":"Sainte Françoise-Xavière","12-23":"Saint Armand","12-24":"Sainte Adèle","12-25":"Noël",
  "12-26":"Saint Étienne","12-27":"Saint Jean","12-28":"Innocents","12-29":"Saint David","12-30":"Saint Roger",
  "12-31":"Saint Sylvestre"
}};

// ── Horloge en direct (header) ────────────────────────────────────────────────
function updateLiveClock() {{
  const el = document.getElementById('liveClock');
  if (!el) return;
  const now = new Date();
  const time = now.toLocaleTimeString('fr-FR', {{
    timeZone: 'Europe/Paris', hour: '2-digit', minute: '2-digit'
  }});
  const date = now.toLocaleDateString('fr-FR', {{
    timeZone: 'Europe/Paris', weekday: 'long', day: '2-digit', month: 'long'
  }});
  const dateKey = now.toLocaleDateString('en-CA', {{timeZone: 'Europe/Paris'}}).slice(5); // "MM-DD"
  const fete = FETES[dateKey] ? ` · ${{FETES[dateKey]}}` : '';
  el.textContent = `${{time}} · ${{date}}${{fete}}`;
}}
updateLiveClock();
setInterval(updateLiveClock, 1000);

// ── Mode sombre / clair (bouton + mémorisation) ───────────────────────────────
(function() {{
  const stored = localStorage.getItem('mittelharth-theme');
  if (stored) document.documentElement.setAttribute('data-theme', stored);
  const btn = document.getElementById('themeToggle');
  function isDark() {{
    const attr = document.documentElement.getAttribute('data-theme');
    if (attr) return attr === 'dark';
    return window.matchMedia('(prefers-color-scheme:dark)').matches;
  }}
  function updateBtn() {{ btn.textContent = isDark() ? '☀️' : '🌙'; }}
  updateBtn();
  btn.addEventListener('click', () => {{
    const next = isDark() ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('mittelharth-theme', next);
    updateBtn();
    location.reload(); // recharge pour que les graphiques Chart.js reprennent les bonnes couleurs
  }});
}})();

// ── Mini graphique 24h ────────────────────────────────────────────────────────
const dark = document.documentElement.getAttribute('data-theme')
  ? document.documentElement.getAttribute('data-theme') === 'dark'
  : window.matchMedia('(prefers-color-scheme:dark)').matches;
const gc = () => dark ? '#2c2c2a' : '#e1e0d9';
const tc = () => '#898781';

if (hourly.length > 0) {{
  // Heure actuelle Paris
  const nowParis = new Date(new Date().toLocaleString('en-US', {{timeZone:'Europe/Paris'}}));
  const currentHour = nowParis.getHours();
  const pad2 = n => String(n).padStart(2, '0');
  // Construit la date à partir des getters LOCAUX (cohérent avec getHours()) :
  // toISOString() convertit en UTC et peut faire sauter d'un jour si le fuseau
  // système de l'appareil n'est pas Europe/Paris, ce qui rejetait à tort les
  // relevés du matin du filtre "aujourd'hui" ci-dessous.
  const todayStr = `${{nowParis.getFullYear()}}-${{pad2(nowParis.getMonth() + 1)}}-${{pad2(nowParis.getDate())}}`;

  // Grille fixe de 00:00 à l'heure actuelle
  const labels24 = Array.from({{length: currentHour + 1}}, (_,h) => String(h).padStart(2,'0')+':00');
  const data24   = new Array(currentHour + 1).fill(null);

  hourly.forEach(h => {{
    if (!h.time) return;
    // Filtrer seulement les données d'aujourd'hui
    const date_part = h.time.slice(0,10);
    if (date_part !== todayStr) return;
    const hour = parseInt(h.time.slice(11,13));
    if (!isNaN(hour) && hour <= currentHour && h.temp !== null) {{
      data24[hour] = h.temp;
    }}
  }});

  new Chart(document.getElementById('miniChart'), {{
    type: 'line',
    data: {{
      labels: labels24,
      datasets: [{{
        data:  data24,
        borderColor: '#d85a30',
        backgroundColor: 'rgba(216,90,48,0.08)',
        borderWidth: 2,
        pointRadius: 2,
        spanGaps: true,
        fill: true,
        tension: 0.4,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: c => c.parsed.y !== null ? c.parsed.y.toFixed(1) + ' °C' : '—' }} }} }},
      scales: {{
        x: {{
          ticks: {{ color: tc(), maxRotation: 0, callback: (v, i) => i % 3 === 0 ? labels24[i] : '' }},
          grid: {{ color: gc() }}
        }},
        y: {{ ticks: {{ color: tc(), callback: v => v + '°' }}, grid: {{ color: gc() }} }}
      }}
    }}
  }});
}}

// ── Prévisions 7 jours ⇄ heure par heure (un seul bloc basculable) ───────────
const forecastGrid  = document.getElementById('forecastGrid');
const forecastTitle = document.getElementById('forecastTitle');
const forecastBack  = document.getElementById('forecastBack');
const nowParis       = new Date(new Date().toLocaleString('en-US', {{timeZone:'Europe/Paris'}}));
const parsedHourly   = hourlyForecast.map(h => ({{...h, dt: new Date(h.time)}})).filter(h => !isNaN(h.dt));

function showDaysView() {{
  forecastGrid.className = 'forecast-grid';
  forecastGrid.innerHTML = '';
  forecastBack.style.display = 'none';
  forecastTitle.textContent = 'Prévisions 7 jours';

  forecast.forEach((day, idx) => {{
    const dt   = new Date(day.date + 'T12:00:00');
    const nom  = JOURS[dt.getDay()];
    const icon = WMO[String(day.code)] || '🌡';
    const div  = document.createElement('div');
    div.className = 'forecast-day';
    div.innerHTML = `
      <div class="forecast-day-name">${{nom}}</div>
      <div class="forecast-icon">${{icon}}</div>
      <div class="forecast-max">${{day.max_t !== null ? day.max_t.toFixed(0) + '°' : '—'}}</div>
      <div class="forecast-min">${{day.min_t !== null ? day.min_t.toFixed(0) + '°' : '—'}}</div>
      <div class="forecast-rain">${{day.rain !== null && day.rain > 0 ? day.rain.toFixed(1) + ' mm' : ''}}</div>
    `;
    div.addEventListener('click', () => showHoursView(day, idx === 0, nom, dt));
    forecastGrid.appendChild(div);
  }});
}}

function showHoursView(day, isToday, nom, dt) {{
  forecastGrid.className = 'hourly-scroll';
  forecastGrid.innerHTML = '';
  forecastBack.style.display = 'inline-block';
  const dateLabel = isToday
    ? "Aujourd'hui"
    : `${{nom}} ${{String(dt.getDate()).padStart(2,'0')}}/${{String(dt.getMonth()+1).padStart(2,'0')}}`;
  forecastTitle.textContent = 'Heure par heure · ' + dateLabel;

  let items;
  if (isToday) {{
    const startHour = new Date(nowParis.getFullYear(), nowParis.getMonth(), nowParis.getDate(), nowParis.getHours());
    items = parsedHourly.filter(h => h.dt >= startHour).slice(0, 12);
  }} else {{
    items = parsedHourly.filter(h => h.time.slice(0, 10) === day.date).slice(0, 12);
  }}

  items.forEach((h, i) => {{
    const div = document.createElement('div');
    div.className = 'hourly-item' + (isToday && i === 0 ? ' now' : '');
    const label = (isToday && i === 0)
      ? 'Maint.'
      : h.dt.toLocaleTimeString('fr-FR', {{timeZone:'Europe/Paris', hour:'2-digit'}}).replace(':00', 'h').replace(' ', '');
    const icon = WMO[String(h.code)] || '🌡';
    const temp = h.temp !== null && h.temp !== undefined ? Math.round(h.temp) + '°' : '—';
    const rain = h.rain_proba !== null && h.rain_proba !== undefined && h.rain_proba >= 20 ? '💧' + h.rain_proba + '%' : '';
    div.innerHTML = `
      <div class="hourly-hour">${{label}}</div>
      <div class="hourly-icon">${{icon}}</div>
      <div class="hourly-temp">${{temp}}</div>
      <div class="hourly-rain">${{rain}}</div>
    `;
    // Cliquer sur une heure revient à la vue 7 jours (bascule inverse)
    div.addEventListener('click', showDaysView);
    forecastGrid.appendChild(div);
  }});
}}

// Vue initiale : les 7 jours
showDaysView();
</script>
</body>
</html>"""
    Path("docs/index.html").write_text(html, encoding="utf-8")
    print("  → index.html généré")

def build_dashboard(years, data_by_year):
    """Dashboard avec sélecteur d'année."""
    if not years:
        return

    # Données JS par année
    years_js = json.dumps(years)
    data_js  = json.dumps({str(y): {
        "monthly": {str(m): v for m,v in data_by_year[y]["monthly"].items()},
        "daily":   data_by_year[y]["daily"],
        "heatmap": data_by_year[y]["heatmap"],
        "gel":     {str(k): v for k,v in data_by_year[y]["gel"].items()},
        "chaud":   {str(k): v for k,v in data_by_year[y]["chaud"].items()},
        "pluie":   {str(k): v for k,v in data_by_year[y]["pluie"].items()},
        "nuits_trop": {str(k): v for k,v in data_by_year[y]["nuits_trop"].items()},
        "canicule": {str(k): v for k,v in data_by_year[y]["canicule"].items()},
        "max_abs": data_by_year[y]["max_abs"],
        "min_abs": data_by_year[y]["min_abs"],
        "rain_total": data_by_year[y]["rain_total"],
        "n_days":  data_by_year[y]["n_days"],
    } for y in years}, ensure_ascii=False)

    html = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard · Météo Colmar-Mittelharth</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-annotation/3.0.1/chartjs-plugin-annotation.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f5f5f3;--surface:#fff;--surface-muted:#f0efec;
  --text:#0b0b0b;--text-secondary:#52514e;--text-muted:#898781;
  --border:rgba(11,11,11,.10);--radius:8px;--accent:#2a78d6;
  --accent-bg:#e6f1fb;--accent-border:rgba(42,120,214,.3);--grid:#e1e0d9;
}
@media(prefers-color-scheme:dark){
  :root{--bg:#111110;--surface:#1e1e1c;--surface-muted:#252523;
    --text:#fff;--text-secondary:#c3c2b7;--text-muted:#898781;
    --border:rgba(255,255,255,.10);--grid:#2c2c2a;
    --accent:#3987e5;--accent-bg:rgba(57,135,229,.12);--accent-border:rgba(57,135,229,.4);}
}
html[data-theme="dark"]{
  --bg:#111110;--surface:#1e1e1c;--surface-muted:#252523;
  --text:#fff;--text-secondary:#c3c2b7;--text-muted:#898781;
  --border:rgba(255,255,255,.10);--grid:#2c2c2a;
  --accent:#3987e5;--accent-bg:rgba(57,135,229,.12);--accent-border:rgba(57,135,229,.4);
}
html[data-theme="light"]{
  --bg:#f5f5f3;--surface:#fff;--surface-muted:#f0efec;
  --text:#0b0b0b;--text-secondary:#52514e;--text-muted:#898781;
  --border:rgba(11,11,11,.10);--radius:8px;--accent:#2a78d6;
  --accent-bg:#e6f1fb;--accent-border:rgba(42,120,214,.3);--grid:#e1e0d9;
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:1.5rem 1rem}
.container{max-width:960px;margin:0 auto}
header{margin-bottom:1.5rem;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px}
header h1{font-size:20px;font-weight:500;margin-bottom:4px}
header p{font-size:13px;color:var(--text-muted)}
.year-selector{display:flex;gap:8px;align-items:center}
.theme-toggle{background:var(--surface-muted);border:0.5px solid var(--border);border-radius:99px;width:34px;height:34px;font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--text)}
.year-selector label{font-size:13px;color:var(--text-muted)}
.year-selector select{font-size:15px;font-weight:500;padding:6px 12px;border-radius:var(--radius);border:0.5px solid var(--accent-border);background:var(--accent-bg);color:var(--accent);font-family:inherit;cursor:pointer}
nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:1.5rem}
nav a{font-size:13px;padding:6px 14px;border-radius:var(--radius);border:0.5px solid var(--border);background:var(--surface-muted);color:var(--text-secondary);text-decoration:none}
nav a.active{background:var(--accent-bg);color:var(--accent);border-color:var(--accent-border)}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:1.5rem}
.kpi-card{background:var(--surface);border-radius:var(--radius);border:0.5px solid var(--border);padding:1rem 1.25rem}
.kpi-label{font-size:13px;color:var(--text-muted);margin-bottom:6px}
.kpi-value{font-size:22px;font-weight:500;margin-bottom:2px}
.kpi-sub{font-size:12px;color:var(--text-muted)}
.section{background:var(--surface);border-radius:12px;border:0.5px solid var(--border);padding:1.5rem;margin-bottom:1.5rem}
.section-title{font-size:12px;font-weight:500;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:1rem}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:1.25rem}
.tab-btn{font-size:13px;padding:6px 14px;cursor:pointer;border-radius:var(--radius);border:0.5px solid var(--border);background:var(--surface-muted);color:var(--text-secondary);font-family:inherit;transition:all .15s}
.tab-btn.active{background:var(--accent-bg);color:var(--accent);border-color:var(--accent-border);font-weight:500}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary);margin-bottom:12px}
.legend-item{display:flex;align-items:center;gap:5px}
.legend-dot{width:10px;height:10px;border-radius:2px;flex-shrink:0}
.chart-wrap{position:relative;width:100%}
.chart-note{font-size:12px;color:var(--text-muted);text-align:center;margin-top:8px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem;margin-bottom:1.5rem}
@media(max-width:620px){.two-col{grid-template-columns:1fr}}
.jours-table{width:100%;border-collapse:collapse;font-size:13px}
.jours-table th{font-size:12px;font-weight:500;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;padding:6px 10px;text-align:center;border-bottom:0.5px solid var(--border)}
.jours-table th:first-child{text-align:left}
.jours-table td{padding:7px 10px;text-align:center;border-bottom:0.5px solid var(--border);color:var(--text-secondary)}
.jours-table td:first-child{font-weight:500;color:var(--text);text-align:left}
.jours-table tr:hover td{background:var(--surface-muted)}
.badge{display:inline-block;min-width:28px;padding:2px 8px;border-radius:4px;font-weight:500;font-size:13px}
.badge-gel{background:rgba(42,120,214,.12);color:#2a78d6}
.badge-chaud{background:rgba(216,90,48,.12);color:#d85a30}
.badge-pluie{background:rgba(27,175,122,.12);color:#1baf7a}
.badge-nuit{background:rgba(130,60,180,.12);color:#8a3cb4}
.badge-canicule{background:rgba(216,30,30,.14);color:#d81e1e}
.badge-zero{color:var(--text-muted)}
.totaux td{font-weight:600;color:var(--text)!important;background:var(--surface-muted);border-top:1px solid var(--border)!important}
.heatmap-wrap{overflow-x:auto}
.heatmap{display:grid;grid-template-columns:48px repeat(31,1fr);gap:2px;min-width:520px}
.hm-cell{height:20px;border-radius:2px;cursor:pointer}
.hm-label-month{font-size:12px;color:var(--text-secondary);font-weight:500;display:flex;align-items:center}
.hm-label-day{font-size:9px;color:var(--text-muted);display:flex;align-items:center;justify-content:center}
.hm-tooltip{position:fixed;background:var(--surface);border:0.5px solid var(--border);border-radius:6px;padding:5px 10px;font-size:12px;pointer-events:none;display:none;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,.12)}
footer{text-align:center;font-size:12px;color:var(--text-muted);margin-top:2rem;padding-top:1rem;border-top:0.5px solid var(--border)}
</style>
</head>
<body>
<div class="container">

<header>
  <div>
    <h1>📊 Historique de la station</h1>
    <p id="header-sub">Station Colmar-Mittelharth</p>
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <div class="year-selector">
      <label>Année :</label>
      <select id="yearSelect" onchange="loadYear(+this.value)"></select>
    </div>
    <button class="theme-toggle" id="themeToggle" title="Basculer mode clair/sombre">🌙</button>
  </div>
</header>

<nav>
  <a href="index.html">⚡ En direct</a>
  <a href="dashboard.html" class="active">📊 Historique</a>
  <a href="climate.html">🌍 Climatologie</a>
</nav>

<div class="kpi-grid">
  <div class="kpi-card"><div class="kpi-label">Temp. max</div><div class="kpi-value" id="kpi-max">—</div></div>
  <div class="kpi-card"><div class="kpi-label">Temp. min</div><div class="kpi-value" id="kpi-min">—</div></div>
  <div class="kpi-card"><div class="kpi-label">Précipitations</div><div class="kpi-value" id="kpi-rain">—</div><div class="kpi-sub" id="kpi-days">—</div></div>
  <div class="kpi-card"><div class="kpi-label">Jours de gel</div><div class="kpi-value" id="kpi-gel">—</div></div>
  <div class="kpi-card"><div class="kpi-label">Jours chauds</div><div class="kpi-value" id="kpi-chaud">—</div></div>
</div>

<div class="section">
  <div class="section-title">Analyse mensuelle</div>
  <div class="tabs">
    <button class="tab-btn active" id="btn-temp"     onclick="showChart('temp')">🌡 Températures</button>
    <button class="tab-btn"        id="btn-rain"     onclick="showChart('rain')">🌧 Précipitations</button>
    <button class="tab-btn"        id="btn-solar"    onclick="showChart('solar')">☀ Ensoleillement</button>
    <button class="tab-btn"        id="btn-hum"      onclick="showChart('hum')">💧 Humidité</button>
    <button class="tab-btn"        id="btn-pressure" onclick="showChart('pressure')">📊 Pression</button>
  </div>
  <div id="legend-main" class="legend">
    <span class="legend-item"><span class="legend-dot" style="background:#d85a30"></span>Max</span>
    <span class="legend-item"><span class="legend-dot" style="background:#2a78d6"></span>Moy.</span>
    <span class="legend-item"><span class="legend-dot" style="background:#1baf7a"></span>Min</span>
  </div>
  <div class="chart-wrap" style="height:300px"><canvas id="mainChart"></canvas></div>
</div>

<div class="section">
  <div class="section-title">Températures journalières</div>
  <div class="legend">
    <span class="legend-item"><span class="legend-dot" style="background:#d85a30"></span>Max</span>
    <span class="legend-item"><span class="legend-dot" style="background:#2a78d6"></span>Moyenne</span>
    <span class="legend-item"><span class="legend-dot" style="background:#1baf7a"></span>Min</span>
  </div>
  <div class="chart-wrap" style="height:260px"><canvas id="dailyChart"></canvas></div>
  <div class="chart-note">Survoler pour la date et les valeurs exactes</div>
</div>

<div class="section">
  <div class="section-title">Climatogramme de Walter-Lieth</div>
  <div class="legend">
    <span class="legend-item"><span class="legend-dot" style="background:#d85a30"></span>Température (°C) — axe gauche</span>
    <span class="legend-item"><span class="legend-dot" style="background:#2a78d6"></span>Précipitations (mm) — axe droit</span>
    <span class="legend-item"><span class="legend-dot" style="background:rgba(42,120,214,0.35);border:1px solid #2a78d6"></span>Période humide</span>
    <span class="legend-item"><span class="legend-dot" style="background:rgba(230,180,80,0.4);border:1px solid #b47a14"></span>Période sèche</span>
  </div>
  <div class="chart-wrap" style="height:300px"><canvas id="wlCanvas" style="width:100%;height:100%"></canvas></div>
  <div class="chart-note">Règle Walter-Lieth : échelle P = 2 × T</div>
</div>

<div class="section">
  <div class="section-title">Carte thermique calendaire</div>
  <div class="heatmap-wrap"><div class="heatmap" id="heatmapGrid"></div></div>
  <div class="hm-tooltip" id="hmTooltip"></div>
  <div style="display:flex;align-items:center;gap:8px;margin-top:12px;font-size:12px;color:var(--text-muted)">
    <span>Froid</span>
    <div id="hmLegendBar" style="flex:1;height:10px;border-radius:4px;background:linear-gradient(to right,rgb(30,60,180),rgb(80,140,220),rgb(150,210,200),rgb(250,220,100),rgb(230,100,30),rgb(180,20,20))"></div>
    <span>Chaud</span>
  </div>
</div>

<div class="section">
  <div class="section-title">Jours remarquables par mois</div>
  <table class="jours-table">
    <thead><tr><th>Mois</th><th>❄ Gel</th><th>🌡 Chauds</th><th>🔥 Canicule</th><th>🌧 Pluie</th><th>🌙 Nuits trop.</th></tr></thead>
    <tbody id="jours-tbody"></tbody>
    <tfoot><tr class="totaux"><td>Total</td><td id="t-gel"></td><td id="t-chaud"></td><td id="t-canicule"></td><td id="t-pluie"></td><td id="t-nuits"></td></tr></tfoot>
  </table>
  <div class="chart-note" style="text-align:left;margin-top:10px">🔥 Canicule : critère officiel Haut-Rhin (max ≥ 35°C et min ≥ 19°C, 3 jours consécutifs), avec une marge de ±1°C pour absorber l'incertitude de mesure de la station.</div>
</div>

<div class="section">
  <div class="section-title">Précipitations mensuelles</div>
  <div class="chart-wrap" style="height:200px"><canvas id="rainChart"></canvas></div>
</div>

<div class="section">
  <div class="section-title">Cumul de pluie annuel</div>
  <div class="legend">
    <span class="legend-item"><span class="legend-dot" style="background:#2a78d6"></span>Cumul journalier (mm)</span>
  </div>
  <div class="chart-wrap" style="height:220px"><canvas id="cumulChart"></canvas></div>
  <div class="chart-note">Courbe en escalier — total cumulé depuis le 1er janvier</div>
</div>
<div class="two-col">
  <div class="section" style="margin-bottom:0">
    <div class="section-title">Ensoleillement</div>
    <div class="chart-wrap" style="height:180px"><canvas id="solarChart"></canvas></div>
  </div>
  <div class="section" style="margin-bottom:0">
    <div class="section-title">Humidité</div>
    <div class="chart-wrap" style="height:180px"><canvas id="humChart"></canvas></div>
  </div>
</div>

<footer>Station météo personnelle · Colmar-Mittelharth · Alsace</footer>
</div>

<script>
const YEARS = """ + years_js + """;
const DATA  = """ + data_js  + """;
const MONTHS = ["Jan","Fév","Mar","Avr","Mai","Juin","Juil","Août","Sep","Oct","Nov","Déc"];

// ── Mode sombre / clair (bouton + mémorisation, partagé avec les autres pages) ─
(function() {
  const stored = localStorage.getItem('mittelharth-theme');
  if (stored) document.documentElement.setAttribute('data-theme', stored);
  const btn = document.getElementById('themeToggle');
  function isDark() {
    const attr = document.documentElement.getAttribute('data-theme');
    if (attr) return attr === 'dark';
    return window.matchMedia('(prefers-color-scheme:dark)').matches;
  }
  function updateBtn() { btn.textContent = isDark() ? '☀️' : '🌙'; }
  updateBtn();
  btn.addEventListener('click', () => {
    const next = isDark() ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('mittelharth-theme', next);
    updateBtn();
    location.reload(); // recharge pour que les graphiques Chart.js reprennent les bonnes couleurs
  });
})();

let currentYear = YEARS[YEARS.length - 1];
let cumulChart = null;
let mainChart = null, dailyChart = null, rainChart = null, solarChart = null, humChart = null;
let currentMode = 'temp';

// Peupler le sélecteur
const sel = document.getElementById('yearSelect');
YEARS.forEach(y => {
  const opt = document.createElement('option');
  opt.value = y; opt.textContent = y;
  if (y === currentYear) opt.selected = true;
  sel.appendChild(opt);
});

function isDarkTheme() {
  const attr = document.documentElement.getAttribute('data-theme');
  if (attr) return attr === 'dark';
  return window.matchMedia('(prefers-color-scheme:dark)').matches;
}
const gc = () => isDarkTheme() ? '#2c2c2a' : '#e1e0d9';
const tc = () => '#898781';

function getMnthArr(key) {
  const m = DATA[currentYear].monthly;
  return MONTHS.map((_,i) => (m[i+1] || {})[key] ?? null);
}

function loadYear(year) {
  currentYear = year;
  const d = DATA[year];
  document.getElementById('header-sub').textContent = `Station Colmar-Mittelharth · Année ${year} · ${d.n_days} jours`;
  document.getElementById('kpi-max').textContent   = d.max_abs !== null ? d.max_abs + ' °C' : '—';
  document.getElementById('kpi-min').textContent   = d.min_abs !== null ? d.min_abs + ' °C' : '—';
  document.getElementById('kpi-rain').textContent  = d.rain_total + ' mm';
  document.getElementById('kpi-days').textContent  = d.n_days + ' jours de données';
  document.getElementById('kpi-gel').textContent   = Object.values(d.gel).reduce((a,b)=>a+b,0);
  document.getElementById('kpi-chaud').textContent = Object.values(d.chaud).reduce((a,b)=>a+b,0);

  showChart(currentMode);
  buildDailyChart();
  drawWL();
  buildHeatmap();
  buildJours();
  buildSecondaryCharts();
}

// ── Graphique principal ───────────────────────────────────────────────────────
const legendMap = {
  temp:     '<span class="legend-item"><span class="legend-dot" style="background:#d85a30"></span>Max</span><span class="legend-item"><span class="legend-dot" style="background:#2a78d6"></span>Moy.</span><span class="legend-item"><span class="legend-dot" style="background:#1baf7a"></span>Min</span>',
  rain:     '<span class="legend-item"><span class="legend-dot" style="background:#2a78d6"></span>Précipitations (mm)</span>',
  solar:    '<span class="legend-item"><span class="legend-dot" style="background:#eda100"></span>Rayonnement (W/m²)</span>',
  hum:      '<span class="legend-item"><span class="legend-dot" style="background:#1baf7a"></span>Humidité (%)</span>',
  pressure: '<span class="legend-item"><span class="legend-dot" style="background:#4a3aa7"></span>Pression (hPa)</span>',
};

function showChart(mode) {
  currentMode = mode;
  ['temp','rain','solar','hum','pressure'].forEach(m => document.getElementById('btn-'+m).classList.toggle('active', m===mode));
  document.getElementById('legend-main').innerHTML = legendMap[mode];
  if (mainChart) mainChart.destroy();
  const opts = {
    responsive:true, maintainAspectRatio:false,
    plugins:{legend:{display:false}},
    scales:{x:{ticks:{color:tc(),autoSkip:false,maxRotation:0},grid:{color:gc()}},y:{ticks:{color:tc()},grid:{color:gc()}}}
  };
  const cfgs = {
    temp:     {type:'bar', data:{labels:MONTHS,datasets:[
      {label:'Max',data:getMnthArr('max_t'),backgroundColor:'rgba(216,90,48,.85)',borderRadius:4},
      {label:'Moy',data:getMnthArr('avg_t'),backgroundColor:'rgba(42,120,214,.85)',borderRadius:4},
      {label:'Min',data:getMnthArr('min_t'),backgroundColor:'rgba(27,175,122,.85)',borderRadius:4}
    ]}, options:opts},
    rain:     {type:'bar', data:{labels:MONTHS,datasets:[{label:'Pluie',data:getMnthArr('rain'),backgroundColor:'rgba(42,120,214,.7)',borderRadius:4}]}, options:opts},
    solar:    {type:'line',data:{labels:MONTHS,datasets:[{label:'Solaire',data:getMnthArr('solar'),borderColor:'#eda100',backgroundColor:'rgba(237,161,0,.12)',borderWidth:2,pointBackgroundColor:'#eda100',fill:true,tension:0.4}]}, options:opts},
    hum:      {type:'line',data:{labels:MONTHS,datasets:[{label:'Hum',data:getMnthArr('hum'),borderColor:'#1baf7a',backgroundColor:'rgba(27,175,122,.1)',borderWidth:2,pointBackgroundColor:'#1baf7a',fill:true,tension:0.4}]}, options:{...opts,scales:{x:{ticks:{color:tc(),autoSkip:false,maxRotation:0},grid:{color:gc()}},y:{min:0,max:100,ticks:{color:tc()},grid:{color:gc()}}}}},
    pressure: {type:'line',data:{labels:MONTHS,datasets:[{label:'Pression',data:getMnthArr('pressure'),borderColor:'#4a3aa7',backgroundColor:'rgba(74,58,167,.1)',borderWidth:2,pointBackgroundColor:'#4a3aa7',fill:true,tension:0.4}]}, options:opts},
  };
  mainChart = new Chart(document.getElementById('mainChart'), cfgs[mode]);
}

// ── Courbe journalière ────────────────────────────────────────────────────────
function buildDailyChart() {
  const daily = DATA[currentYear].daily;
  const dates = daily.map(d => d.date);
  const dlabels = dates.map(d => parseInt(d.slice(8))===1 ? MONTHS[parseInt(d.slice(5,7))-1] : '');
  if (dailyChart) dailyChart.destroy();
  dailyChart = new Chart(document.getElementById('dailyChart'), {
    type:'line',
    data:{labels:dlabels,datasets:[
      {label:'Max',data:daily.map(d=>d.hi),borderColor:'rgba(216,90,48,.65)',backgroundColor:'rgba(216,90,48,.08)',borderWidth:1.5,pointRadius:0,fill:'+1',tension:0.2},
      {label:'Moy',data:daily.map(d=>d.avg),borderColor:'#2a78d6',backgroundColor:'rgba(42,120,214,.07)',borderWidth:2,pointRadius:0,fill:false,tension:0.2},
      {label:'Min',data:daily.map(d=>d.lo),borderColor:'rgba(27,175,122,.65)',backgroundColor:'rgba(27,175,122,.08)',borderWidth:1.5,pointRadius:0,fill:'-1',tension:0.2},
    ]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{
        legend:{display:false},
        tooltip:{mode:'index',intersect:false,callbacks:{title:c=>dates[c[0].dataIndex],label:c=>c.dataset.label+' : '+(c.parsed.y!==null?c.parsed.y.toFixed(1)+' °C':'—')}},
        annotation:{
          annotations:{
            canicule:{type:'line',yMin:26.5,yMax:26.5,borderColor:'rgba(216,90,48,0.6)',borderWidth:1.5,borderDash:[6,3],
              label:{content:'Canicule 26,5°C (moy.)',display:true,position:'end',backgroundColor:'rgba(216,90,48,0.15)',color:'#d85a30',font:{size:11}}},
            gel:{type:'line',yMin:0,yMax:0,borderColor:'rgba(42,120,214,0.6)',borderWidth:1.5,borderDash:[6,3],
              label:{content:'Gel 0°C',display:true,position:'end',backgroundColor:'rgba(42,120,214,0.15)',color:'#2a78d6',font:{size:11}}},
            caniculeZone:{type:'box',yMin:26.5,yMax:50,backgroundColor:'rgba(216,90,48,0.06)',borderWidth:0},
            gelZone:{type:'box',yMin:-20,yMax:0,backgroundColor:'rgba(42,120,214,0.06)',borderWidth:0},
          }
        }
      },
      scales:{x:{ticks:{color:tc(),maxRotation:0,autoSkip:false,callback:(v,i)=>dlabels[i]},grid:{color:gc()}},y:{ticks:{color:tc(),callback:v=>v+' °C'},grid:{color:gc()}}}}
  });
}

// ── Walter-Lieth ──────────────────────────────────────────────────────────────
function drawWL() {
  const avgTemp = getMnthArr('avg_t');
  const rain    = getMnthArr('rain');
  const canvas  = document.getElementById('wlCanvas');
  const W = canvas.parentElement.clientWidth, H = canvas.parentElement.clientHeight;
  const dpr = window.devicePixelRatio||1;
  canvas.width=W*dpr; canvas.height=H*dpr;
  canvas.style.width=W+'px'; canvas.style.height=H+'px';
  const ctx = canvas.getContext('2d'); ctx.scale(dpr,dpr);
  const dark = isDarkTheme();
  const ml=62,mr=72,mt=18,mb=36,cW=W-ml-mr,cH=H-mt-mb;
  const Tmin=-10,Tmax=50,Tmax_ext=60,n=MONTHS.length;
  const xPx = i => ml+(i/(n-1))*cW;
  const yPxT = t => mt+(1-(t-Tmin)/(Tmax_ext-Tmin))*cH;
  const yPxP = p => { const te=p<=100?p/2:50+(p-100)/10; return yPxT(te); };
  const y100 = yPxT(50);
  ctx.clearRect(0,0,W,H);
  ctx.strokeStyle=dark?'#2c2c2a':'#e1e0d9'; ctx.lineWidth=1;
  for(let t=Tmin;t<=Tmax;t+=10){ctx.beginPath();ctx.moveTo(ml,yPxT(t));ctx.lineTo(ml+cW,yPxT(t));ctx.stroke();}
  for(let i=0;i<n;i++){ctx.beginPath();ctx.moveTo(xPx(i),mt);ctx.lineTo(xPx(i),mt+cH);ctx.stroke();}
  for(let i=0;i<n-1;i++){
    if(avgTemp[i]==null||avgTemp[i+1]==null||rain[i]==null||rain[i+1]==null) continue;
    const x0=xPx(i),x1=xPx(i+1),t0=avgTemp[i],t1=avgTemp[i+1],p0=rain[i],p1=rain[i+1];
    const pe0=p0/2,pe1=p1/2,yT0=yPxT(t0),yT1=yPxT(t1),yP0=yPxP(p0),yP1=yPxP(p1);
    let crossX=null,crossY=null;
    if((t0-pe0)*(t1-pe1)<0){const s=(t0-pe0)/((t0-pe0)-(t1-pe1));crossX=x0+s*(x1-x0);crossY=yPxT(t0+s*(t1-t0));}
    const segs=crossX!==null
      ?[{x0,x1:crossX,yT0,yP0,yT1:crossY,yP1:crossY,dry:t0>pe0},{x0:crossX,x1,yT0:crossY,yP0:crossY,yT1,yP1,dry:t1>pe1}]
      :[{x0,x1,yT0,yP0,yT1,yP1,dry:t0>pe0}];
    segs.forEach(s=>{
      if(s.dry){
        ctx.save();ctx.beginPath();
        ctx.moveTo(s.x0,s.yT0);ctx.lineTo(s.x1,s.yT1);ctx.lineTo(s.x1,s.yP1);ctx.lineTo(s.x0,s.yP0);ctx.closePath();
        ctx.fillStyle='rgba(230,180,80,.18)';ctx.fill();ctx.clip();
        ctx.strokeStyle=dark?'rgba(200,140,20,.55)':'rgba(140,90,10,.5)';ctx.lineWidth=1;
        const hgt=Math.max(s.yP0,s.yP1)-Math.min(s.yT0,s.yT1);
        for(let hx=s.x0-hgt;hx<s.x1+hgt;hx+=7){ctx.beginPath();ctx.moveTo(hx,Math.min(s.yT0,s.yT1)-5);ctx.lineTo(hx+hgt+5,Math.max(s.yP0,s.yP1)+5);ctx.stroke();}
        ctx.restore();
      }else{
        const yPa=Math.max(s.yP0,y100),yPb=Math.max(s.yP1,y100);
        ctx.beginPath();ctx.moveTo(s.x0,yPa);ctx.lineTo(s.x1,yPb);ctx.lineTo(s.x1,s.yT1);ctx.lineTo(s.x0,s.yT0);ctx.closePath();
        ctx.fillStyle=dark?'rgba(42,120,214,.30)':'rgba(42,120,214,.20)';ctx.fill();
        if(s.yP0<y100||s.yP1<y100){
          ctx.beginPath();ctx.moveTo(s.x0,Math.min(s.yP0,y100));ctx.lineTo(s.x1,Math.min(s.yP1,y100));ctx.lineTo(s.x1,s.yP1);ctx.lineTo(s.x0,s.yP0);ctx.closePath();
          ctx.fillStyle=dark?'rgba(10,20,80,.90)':'rgba(10,20,80,.82)';ctx.fill();
        }
      }
    });
  }
  ctx.beginPath();ctx.strokeStyle='#2a78d6';ctx.lineWidth=2.5;ctx.lineJoin='round';
  MONTHS.forEach((_,i)=>{if(rain[i]==null)return;const y=yPxP(rain[i]);i===0?ctx.moveTo(xPx(i),y):ctx.lineTo(xPx(i),y);});ctx.stroke();
  MONTHS.forEach((_,i)=>{if(rain[i]==null)return;ctx.beginPath();ctx.arc(xPx(i),yPxP(rain[i]),3.5,0,Math.PI*2);ctx.fillStyle='#2a78d6';ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.stroke();});
  ctx.beginPath();ctx.strokeStyle='#d85a30';ctx.lineWidth=2.5;ctx.lineJoin='round';
  MONTHS.forEach((_,i)=>{if(avgTemp[i]==null)return;const y=yPxT(avgTemp[i]);i===0?ctx.moveTo(xPx(i),y):ctx.lineTo(xPx(i),y);});ctx.stroke();
  MONTHS.forEach((_,i)=>{if(avgTemp[i]==null)return;ctx.beginPath();ctx.arc(xPx(i),yPxT(avgTemp[i]),3.5,0,Math.PI*2);ctx.fillStyle='#d85a30';ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.stroke();});
  ctx.beginPath();ctx.strokeStyle='rgba(42,120,214,.5)';ctx.lineWidth=1;ctx.setLineDash([4,4]);
  ctx.moveTo(ml,y100);ctx.lineTo(ml+cW,y100);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle='rgba(42,120,214,.8)';ctx.font='11px system-ui,sans-serif';ctx.textAlign='left';
  ctx.beginPath();ctx.strokeStyle='#2a78d6';ctx.lineWidth=1;ctx.moveTo(ml+cW,y100);ctx.lineTo(ml+cW+4,y100);ctx.stroke();
  ctx.fillText('100 mm',ml+cW+7,y100+4);
  const ac=dark?'#c3c2b7':'#52514e';
  ctx.strokeStyle=ac;ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(ml,mt);ctx.lineTo(ml,mt+cH);ctx.stroke();
  ctx.beginPath();ctx.moveTo(ml+cW,mt);ctx.lineTo(ml+cW,mt+cH);ctx.stroke();
  ctx.beginPath();ctx.moveTo(ml,mt+cH);ctx.lineTo(ml+cW,mt+cH);ctx.stroke();
  ctx.fillStyle='#d85a30';ctx.font='11px system-ui,sans-serif';ctx.textAlign='right';
  for(let t=Tmin;t<=Tmax;t+=10){const y=yPxT(t);ctx.beginPath();ctx.strokeStyle='#d85a30';ctx.lineWidth=1;ctx.moveTo(ml-4,y);ctx.lineTo(ml,y);ctx.stroke();ctx.fillText(t+' °C',ml-7,y+4);}
  ctx.save();ctx.translate(14,mt+cH/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.font='12px system-ui,sans-serif';ctx.fillText('Température (°C)',0,0);ctx.restore();
  ctx.fillStyle='#2a78d6';ctx.font='11px system-ui,sans-serif';ctx.textAlign='left';
  for(let p=0;p<100;p+=20){const y=yPxP(p);ctx.beginPath();ctx.strokeStyle='#2a78d6';ctx.lineWidth=1;ctx.moveTo(ml+cW,y);ctx.lineTo(ml+cW+4,y);ctx.stroke();ctx.fillText(p+' mm',ml+cW+7,y+4);}
  if(rain.some(r=>r>100)){const y=yPxP(200);ctx.beginPath();ctx.strokeStyle='#2a78d6';ctx.lineWidth=1;ctx.moveTo(ml+cW,y);ctx.lineTo(ml+cW+4,y);ctx.stroke();ctx.fillText('200 mm',ml+cW+7,y+4);}
  ctx.save();ctx.translate(W-10,mt+cH/2);ctx.rotate(Math.PI/2);ctx.textAlign='center';ctx.font='12px system-ui,sans-serif';ctx.fillStyle='#2a78d6';ctx.fillText('Précipitations (mm)',0,0);ctx.restore();
  ctx.fillStyle=tc();ctx.font='12px system-ui,sans-serif';ctx.textAlign='center';
  MONTHS.forEach((m,i)=>ctx.fillText(m,xPx(i),mt+cH+20));
}
window.addEventListener('resize', drawWL);

// ── Heatmap ───────────────────────────────────────────────────────────────────
function tempColor(t){
  if(t===null) return 'transparent';
  const stops=[{v:-10,r:30,g:60,b:180},{v:0,r:80,g:140,b:220},{v:10,r:150,g:210,b:200},{v:20,r:250,g:220,b:100},{v:30,r:230,g:100,b:30},{v:44,r:180,g:20,b:20}];
  let lo=stops[0],hi=stops[stops.length-1];
  for(let i=0;i<stops.length-1;i++) if(t>=stops[i].v&&t<=stops[i+1].v){lo=stops[i];hi=stops[i+1];break;}
  const f=lo.v===hi.v?1:Math.max(0,Math.min(1,(t-lo.v)/(hi.v-lo.v)));
  return `rgb(${Math.round(lo.r+f*(hi.r-lo.r))},${Math.round(lo.g+f*(hi.g-lo.g))},${Math.round(lo.b+f*(hi.b-lo.b))})`;
}

function buildHeatmap(){
  const grid = document.getElementById('heatmapGrid');
  const tooltip = document.getElementById('hmTooltip');
  grid.innerHTML = '';
  const heatmap = DATA[currentYear].heatmap;
  const monthDays=[31,28,31,30,31,30,31,31,30,31,30,31];
  // Vérifier année bissextile
  if(currentYear % 4 === 0) monthDays[1] = 29;
  const empty=document.createElement('div');empty.className='hm-label-month';grid.appendChild(empty);
  for(let d=1;d<=31;d++){const c=document.createElement('div');c.className='hm-label-day';c.textContent=d;grid.appendChild(c);}
  heatmap.forEach((mdata,mi)=>{
    const lbl=document.createElement('div');lbl.className='hm-label-month';lbl.textContent=MONTHS[mi];grid.appendChild(lbl);
    for(let d=0;d<31;d++){
      const cell=document.createElement('div');cell.className='hm-cell';
      const val=mdata[d];
      if(val!==null&&val!==undefined&&d<monthDays[mi]){
        cell.style.background=tempColor(val);
        cell.addEventListener('mousemove',e=>{tooltip.style.display='block';tooltip.style.left=(e.clientX+12)+'px';tooltip.style.top=(e.clientY-28)+'px';tooltip.textContent=MONTHS[mi]+' '+(d+1)+' '+currentYear+' → '+val.toFixed(1)+' °C';});
        cell.addEventListener('mouseleave',()=>tooltip.style.display='none');
      }else cell.style.background='transparent';
      grid.appendChild(cell);
    }
  });
}

// ── Jours remarquables ────────────────────────────────────────────────────────
function buildJours(){
  const d = DATA[currentYear];
  const tbody = document.getElementById('jours-tbody');
  tbody.innerHTML = '';
  MONTHS.forEach((m,i)=>{
    const tr=document.createElement('tr');
    const b=(v,cls)=>v>0?`<span class="badge ${cls}">${v}</span>`:'<span class="badge-zero">—</span>';
    const nt = (d.nuits_trop || {})[i+1] || 0;
    const can = (d.canicule || {})[i+1] || 0;
    tr.innerHTML=`<td>${m}</td><td>${b(d.gel[i+1]||0,'badge-gel')}</td><td>${b(d.chaud[i+1]||0,'badge-chaud')}</td><td>${b(can,'badge-canicule')}</td><td>${b(d.pluie[i+1]||0,'badge-pluie')}</td><td>${b(nt,'badge-nuit')}</td>`;
    tbody.appendChild(tr);
  });
  const sum = obj => Object.values(obj).reduce((a,b)=>a+b,0);
  document.getElementById('t-gel').innerHTML   = `<span class="badge badge-gel">${sum(d.gel)}</span>`;
  document.getElementById('t-chaud').innerHTML = `<span class="badge badge-chaud">${sum(d.chaud)}</span>`;
  document.getElementById('t-canicule').innerHTML = `<span class="badge badge-canicule">${sum(d.canicule||{})}</span>`;
  document.getElementById('t-pluie').innerHTML = `<span class="badge badge-pluie">${sum(d.pluie)}</span>`;
  document.getElementById('t-nuits').innerHTML = `<span class="badge badge-nuit">${sum(d.nuits_trop||{})}</span>`;
}

// ── Graphiques secondaires ────────────────────────────────────────────────────
function buildSecondaryCharts(){
  const opts = (suf) => ({responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.parsed.y?.toFixed(1)+suf}}},scales:{x:{ticks:{color:tc(),autoSkip:false,maxRotation:0},grid:{color:gc()}},y:{ticks:{color:tc(),callback:v=>v+suf},grid:{color:gc()}}}});
  const daily = DATA[currentYear].daily;
  let cumul = 0;
  const cumulData = daily.map(d => {
    cumul += (d.rain || 0);
    return Math.round(cumul * 10) / 10;
  });
  // Labels : nom du mois uniquement le 1er de chaque mois
  const cumulLabels = daily.map(d => {
    if (parseInt(d.date.slice(8)) === 1) return MONTHS[parseInt(d.date.slice(5,7))-1];
    return '';
  });
  if(cumulChart) cumulChart.destroy();
  cumulChart = new Chart(document.getElementById('cumulChart'), {
    type: 'line',
    data: { labels: cumulLabels, datasets: [{ label: 'Cumul pluie', data: cumulData, borderColor: '#2a78d6', backgroundColor: 'rgba(42,120,214,0.08)', borderWidth: 2, pointRadius: 0, fill: true, stepped: true }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: c => daily[c[0].dataIndex].date,
        label: c => 'Cumul : ' + c.parsed.y.toFixed(1) + ' mm'
      }}},
      scales: {
        x: {
          ticks: {
            color: tc(), maxRotation: 0, autoSkip: false,
            callback: (v, i) => cumulLabels[i]
          },
          grid: { color: gc() }
        },
        y: { ticks: { color: tc(), callback: v => v + ' mm' }, grid: { color: gc() } }
      }
    }
  });
  if(rainChart)  rainChart.destroy();
  if(solarChart) solarChart.destroy();
  if(humChart)   humChart.destroy();
  rainChart  = new Chart(document.getElementById('rainChart'),  {type:'bar', data:{labels:MONTHS,datasets:[{label:'Pluie', data:getMnthArr('rain'), backgroundColor:'rgba(42,120,214,.7)',borderRadius:4}]},options:opts(' mm')});
  solarChart = new Chart(document.getElementById('solarChart'), {type:'line',data:{labels:MONTHS,datasets:[{label:'Solaire',data:getMnthArr('solar'),borderColor:'#eda100',backgroundColor:'rgba(237,161,0,.12)',borderWidth:2,pointBackgroundColor:'#eda100',fill:true,tension:0.4}]},options:opts(' W/m²')});
  humChart   = new Chart(document.getElementById('humChart'),   {type:'line',data:{labels:MONTHS,datasets:[{label:'Hum',   data:getMnthArr('hum'),  borderColor:'#1baf7a',backgroundColor:'rgba(27,175,122,.1)',borderWidth:2,pointBackgroundColor:'#1baf7a',fill:true,tension:0.4}]},options:{...opts(' %'),scales:{x:{ticks:{color:tc(),autoSkip:false,maxRotation:0},grid:{color:gc()}},y:{min:0,max:100,ticks:{color:tc(),callback:v=>v+' %'},grid:{color:gc()}}}}});
}

// Init
loadYear(currentYear);
</script>
</body>
</html>"""
    Path("docs/dashboard.html").write_text(html, encoding="utf-8")
    print("  → dashboard.html généré")

def build_climate(years, data_by_year):
    """Page climatologie avec sélecteur d'année."""
    mn = ["Jan","Fév","Mar","Avr","Mai","Juin","Juil","Août","Sep","Oct","Nov","Déc"]
    years_js = json.dumps(years)
    data_js  = json.dumps({str(y): {
        str(m): data_by_year[y]["monthly"].get(m, {})
        for m in range(1,13)
    } for y in years}, ensure_ascii=False)

    html = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Climatologie · Météo Colmar-Mittelharth</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f5f5f3;--surface:#fff;--surface-muted:#f0efec;--text:#0b0b0b;--text-secondary:#52514e;--text-muted:#898781;--border:rgba(11,11,11,.10);--radius:8px;--accent:#2a78d6;--accent-bg:#e6f1fb;--accent-border:rgba(42,120,214,.3)}
@media(prefers-color-scheme:dark){:root{--bg:#111110;--surface:#1e1e1c;--surface-muted:#252523;--text:#fff;--text-secondary:#c3c2b7;--text-muted:#898781;--border:rgba(255,255,255,.10)}}
html[data-theme="dark"]{--bg:#111110;--surface:#1e1e1c;--surface-muted:#252523;--text:#fff;--text-secondary:#c3c2b7;--text-muted:#898781;--border:rgba(255,255,255,.10);--accent:#3987e5;--accent-bg:rgba(57,135,229,.12);--accent-border:rgba(57,135,229,.4)}
html[data-theme="light"]{--bg:#f5f5f3;--surface:#fff;--surface-muted:#f0efec;--text:#0b0b0b;--text-secondary:#52514e;--text-muted:#898781;--border:rgba(11,11,11,.10);--accent:#2a78d6;--accent-bg:#e6f1fb;--accent-border:rgba(42,120,214,.3)}
.theme-toggle{background:var(--surface-muted);border:0.5px solid var(--border);border-radius:99px;width:34px;height:34px;font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--text)}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:1.5rem 1rem}
.container{max-width:960px;margin:0 auto}
header{margin-bottom:1.5rem;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px}
header h1{font-size:20px;font-weight:500;margin-bottom:4px}
header p{font-size:13px;color:var(--text-muted)}
.year-selector{display:flex;gap:8px;align-items:center}
.year-selector label{font-size:13px;color:var(--text-muted)}
.year-selector select{font-size:15px;font-weight:500;padding:6px 12px;border-radius:var(--radius);border:0.5px solid var(--accent-border);background:var(--accent-bg);color:var(--accent);font-family:inherit;cursor:pointer}
nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:1.5rem}
nav a{font-size:13px;padding:6px 14px;border-radius:var(--radius);border:0.5px solid var(--border);background:var(--surface-muted);color:var(--text-secondary);text-decoration:none}
nav a.active{background:var(--accent-bg);color:var(--accent);border-color:var(--accent-border)}
.section{background:var(--surface);border-radius:12px;border:0.5px solid var(--border);padding:1.5rem;margin-bottom:1.5rem;overflow-x:auto}
.section-title{font-size:12px;font-weight:500;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:1rem}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:600px}
th{font-size:11px;font-weight:500;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;padding:8px 10px;text-align:right;border-bottom:0.5px solid var(--border)}
th:first-child{text-align:left}
td{padding:8px 10px;text-align:right;border-bottom:0.5px solid var(--border);color:var(--text-secondary)}
td:first-child{font-weight:500;color:var(--text);text-align:left}
tr:hover td{background:var(--surface-muted)}
tr:last-child td{border-bottom:none}
footer{text-align:center;font-size:12px;color:var(--text-muted);margin-top:2rem;padding-top:1rem;border-top:0.5px solid var(--border)}
</style>
</head>
<body>
<div class="container">
<header>
  <div>
    <h1>🌍 Climatologie</h1>
    <p id="header-sub">Station Colmar-Mittelharth</p>
  </div>
  <div style="display:flex;align-items:center;gap:10px">
    <div class="year-selector">
      <label>Année :</label>
      <select id="yearSelect" onchange="loadYear(+this.value)"></select>
    </div>
    <button class="theme-toggle" id="themeToggle" title="Basculer mode clair/sombre">🌙</button>
  </div>
</header>
<nav>
  <a href="index.html">⚡ En direct</a>
  <a href="dashboard.html">📊 Historique</a>
  <a href="climate.html" class="active">🌍 Climatologie</a>
</nav>
<div class="section">
  <div class="section-title" id="table-title">Moyennes mensuelles</div>
  <table>
    <thead><tr>
      <th>Mois</th><th>T moy.</th><th>T max</th><th>T min</th>
      <th>Pluie</th><th>Solaire</th><th>Humidité</th><th>Pression</th>
    </tr></thead>
    <tbody id="climate-tbody"></tbody>
  </table>
</div>
<footer>Station météo personnelle · Colmar-Mittelharth · Alsace</footer>
</div>
<script>
const YEARS = """ + years_js + """;
const DATA  = """ + data_js  + """;
const MONTHS = ["Jan","Fév","Mar","Avr","Mai","Juin","Juil","Août","Sep","Oct","Nov","Déc"];

// ── Mode sombre / clair (bouton + mémorisation, partagé avec les autres pages) ─
(function() {
  const stored = localStorage.getItem('mittelharth-theme');
  if (stored) document.documentElement.setAttribute('data-theme', stored);
  const btn = document.getElementById('themeToggle');
  function isDark() {
    const attr = document.documentElement.getAttribute('data-theme');
    if (attr) return attr === 'dark';
    return window.matchMedia('(prefers-color-scheme:dark)').matches;
  }
  function updateBtn() { btn.textContent = isDark() ? '☀️' : '🌙'; }
  updateBtn();
  btn.addEventListener('click', () => {
    const next = isDark() ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('mittelharth-theme', next);
    updateBtn();
  });
})();

const sel = document.getElementById('yearSelect');
YEARS.forEach(y => {
  const opt = document.createElement('option');
  opt.value = y; opt.textContent = y;
  if (y === YEARS[YEARS.length-1]) opt.selected = true;
  sel.appendChild(opt);
});

function fmt(v, u='', dec=1) {
  if (v === null || v === undefined) return '—';
  return v.toFixed(dec) + u;
}

function loadYear(year) {
  document.getElementById('header-sub').textContent = `Station Colmar-Mittelharth · Statistiques ${year}`;
  document.getElementById('table-title').textContent = `Moyennes mensuelles — ${year}`;
  const tbody = document.getElementById('climate-tbody');
  tbody.innerHTML = '';
  const m = DATA[String(year)];
  MONTHS.forEach((mn, i) => {
    const mv = m[String(i+1)] || {};
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${mn}</td>
      <td>${fmt(mv.avg_t, ' °C')}</td>
      <td style="color:#d85a30">${fmt(mv.max_t, ' °C')}</td>
      <td style="color:#2a78d6">${fmt(mv.min_t, ' °C')}</td>
      <td>${fmt(mv.rain, ' mm')}</td>
      <td>${fmt(mv.solar, ' W/m²', 0)}</td>
      <td>${fmt(mv.hum, ' %', 0)}</td>
      <td>${fmt(mv.pressure, ' hPa', 0)}</td>`;
    tbody.appendChild(tr);
  });
}

loadYear(YEARS[YEARS.length-1]);
</script>
</body>
</html>"""
    Path("docs/climate.html").write_text(html, encoding="utf-8")
    print("  → climate.html généré")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    Path("docs").mkdir(exist_ok=True)

    # Toujours : temps réel + accumulation horaire + prévisions
    live = fetch_realtime()
    hourly = update_hourly(live)
    forecast, hourly_fc = fetch_forecast()
    hiking_forecasts = fetch_hiking_forecasts()
    hiking_html = build_hiking_report(hiking_forecasts)

    # Historique (pour les records)
    hist_dict = update_history(live)
    records = get_records(hist_dict)

    build_index(live, hourly, forecast, hourly_fc, records, hiking_html)

    years, data_by_year = aggregate_by_year(hist_dict)
    build_dashboard(years, data_by_year)
    build_climate(years, data_by_year)
    print("✓ Site généré avec succès dans docs/")
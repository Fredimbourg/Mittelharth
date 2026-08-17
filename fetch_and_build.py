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

APP_KEY = os.environ["ECOWITT_APP_KEY"]
API_KEY = os.environ["ECOWITT_API_KEY"]
MAC     = os.environ["ECOWITT_MAC"]

BASE_URL  = "https://api.ecowitt.net/api/v3"
CALL_BACK = "outdoor,indoor,rainfall,wind,pressure,solar_and_uvi"
HIST_FILE = Path("docs/history.json")   # accumulation journalière
LIVE_FILE = Path("docs/live.json")

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
        "updated_at": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
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
                    "date":  d,
                    "month": int(d[5:7]),
                    "day":   int(d[8:10]),
                    "year":  int(d[:4]),
                    "avg":   fv(row[1]),
                    "lo":    fv(row[2]),
                    "hi":    fv(row[3]),
                    "hum":   fv(row[6]),
                    "rain":  fv(row[21]),
                    "solar": fv(row[18]),
                    "pres":  fv(row[32]),
                    "wind":  fv(row[28]),
                }
                count += 1
            print(f"     {count} jours lus")
        except Exception as e:
            print(f"  → Erreur {xlsx.name}: {e}")
    return all_data

# ── 3. Accumulation journalière ───────────────────────────────────────────────
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

    # Données d'aujourd'hui depuis l'API temps réel
    t = live.get("temp")
    existing = hist.get(today, {})
    hist[today] = {
        "date":  today,
        "month": int(today[5:7]),
        "day":   int(today[8:10]),
        "year":  int(today[:4]),
        "avg":   t,
        "hi":    max(existing.get("hi") or t or 0, t or 0) if t else existing.get("hi"),
        "lo":    min(existing.get("lo") or t or 0, t or 0) if t else existing.get("lo"),
        "hum":   live.get("hum"),
        "rain":  live.get("rain_daily"),
        "solar": live.get("solar"),
        "pres":  live.get("pressure"),
        "wind":  live.get("wind_speed"),
    }

    HIST_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2))
    print(f"  → Historique : {len(hist)} jours")
    return hist

# ── 4. Agrégation par année ───────────────────────────────────────────────────
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
                "rain":     round(sum(r),1)  if r  else 0,
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
            "max_abs": round(max(all_hi),1)     if all_hi   else None,
            "min_abs": round(min(all_lo),1)      if all_lo   else None,
            "rain_total": round(sum(all_rain),1) if all_rain else 0,
            "n_days":  len(daily),
        }
        print(f"  → {year}: {len(daily)} jours, max={result[year]['max_abs']}°C, pluie={result[year]['rain_total']}mm")

    return years, result

# ── 5. Génération HTML ────────────────────────────────────────────────────────
def build_index(live):
    def val(v, unit="", dec=1):
        if v is None: return "—"
        return f"{v:.{dec}f}{unit}"
    def wind_dir_str(deg):
        if deg is None: return "—"
        dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSO","SO","OSO","O","ONO","NO","NNO"]
        return dirs[round(deg/22.5) % 16]

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="3600">
<title>Météo Colmar-Mittelharth</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#f5f5f3;--surface:#fff;--surface-muted:#f0efec;--text:#0b0b0b;--text-secondary:#52514e;--text-muted:#898781;--border:rgba(11,11,11,.10);--radius:8px;--accent:#2a78d6;--accent-bg:#e6f1fb;--accent-border:rgba(42,120,214,.3)}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111110;--surface:#1e1e1c;--surface-muted:#252523;--text:#fff;--text-secondary:#c3c2b7;--text-muted:#898781;--border:rgba(255,255,255,.10)}}}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);padding:1.5rem 1rem}}
.container{{max-width:900px;margin:0 auto}}
header{{margin-bottom:1.5rem;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:8px}}
header h1{{font-size:20px;font-weight:500}}
header p{{font-size:13px;color:var(--text-muted)}}
.updated{{font-size:12px;color:var(--text-muted);background:var(--surface-muted);padding:4px 10px;border-radius:99px;border:0.5px solid var(--border)}}
nav{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:1.5rem}}
nav a{{font-size:13px;padding:6px 14px;border-radius:var(--radius);border:0.5px solid var(--border);background:var(--surface-muted);color:var(--text-secondary);text-decoration:none}}
nav a.active{{background:var(--accent-bg);color:var(--accent);border-color:var(--accent-border)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:1.5rem}}
.card{{background:var(--surface);border-radius:var(--radius);border:0.5px solid var(--border);padding:1.25rem}}
.card-icon{{font-size:22px;margin-bottom:8px}}
.card-label{{font-size:12px;color:var(--text-muted);margin-bottom:4px}}
.card-value{{font-size:28px;font-weight:500;line-height:1}}
.card-sub{{font-size:12px;color:var(--text-muted);margin-top:4px}}
.card.highlight{{border-color:var(--accent);background:rgba(42,120,214,0.04)}}
.section{{background:var(--surface);border-radius:12px;border:0.5px solid var(--border);padding:1.5rem;margin-bottom:1rem}}
.section-title{{font-size:12px;font-weight:500;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:1rem}}
.detail-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}}
.detail-item{{padding:10px 12px;background:var(--surface-muted);border-radius:var(--radius)}}
.detail-label{{font-size:11px;color:var(--text-muted);margin-bottom:2px}}
.detail-value{{font-size:16px;font-weight:500}}
footer{{text-align:center;font-size:12px;color:var(--text-muted);margin-top:2rem;padding-top:1rem;border-top:0.5px solid var(--border)}}
</style>
</head>
<body>
<div class="container">
<header>
  <div>
    <h1>🌤 Météo Colmar-Mittelharth</h1>
    <p>Station personnelle · Colmar (68) · Alsace</p>
  </div>
  <span class="updated">Mis à jour : {live['updated_at']}</span>
</header>
<nav>
  <a href="index.html" class="active">⚡ En direct</a>
  <a href="dashboard.html">📊 Historique</a>
  <a href="climate.html">🌍 Climatologie</a>
</nav>
<div class="grid">
  <div class="card highlight">
    <div class="card-icon">🌡</div>
    <div class="card-label">Température</div>
    <div class="card-value">{val(live['temp'],' °C')}</div>
    <div class="card-sub">Ressenti : {val(live['temp_feels'],' °C')}</div>
  </div>
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
    <div class="card-sub">Ce mois : {val(live['rain_monthly'],' mm')}</div>
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
<div class="section">
  <div class="section-title">Intérieur</div>
  <div class="detail-grid">
    <div class="detail-item"><div class="detail-label">Température</div><div class="detail-value">{val(live['temp_in'],' °C')}</div></div>
    <div class="detail-item"><div class="detail-label">Humidité</div><div class="detail-value">{val(live['hum_in'],' %',0)}</div></div>
  </div>
</div>
<footer>Station météo personnelle · Colmar-Mittelharth · Alsace</footer>
</div>
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
  <div class="year-selector">
    <label>Année :</label>
    <select id="yearSelect" onchange="loadYear(+this.value)"></select>
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
    <thead><tr><th>Mois</th><th>❄ Gel</th><th>🌡 Chauds</th><th>🌧 Pluie</th></tr></thead>
    <tbody id="jours-tbody"></tbody>
    <tfoot><tr class="totaux"><td>Total</td><td id="t-gel"></td><td id="t-chaud"></td><td id="t-pluie"></td></tr></tfoot>
  </table>
</div>

<div class="section">
  <div class="section-title">Précipitations mensuelles</div>
  <div class="chart-wrap" style="height:200px"><canvas id="rainChart"></canvas></div>
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

let currentYear = YEARS[YEARS.length - 1];
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

const gc = () => window.matchMedia('(prefers-color-scheme:dark)').matches ? '#2c2c2a' : '#e1e0d9';
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
      plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false,callbacks:{title:c=>dates[c[0].dataIndex],label:c=>c.dataset.label+' : '+(c.parsed.y!==null?c.parsed.y.toFixed(1)+' °C':'—')}}},
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
  const dark = window.matchMedia('(prefers-color-scheme:dark)').matches;
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
  ctx.fillStyle='rgba(42,120,214,.8)';ctx.font='10px system-ui,sans-serif';ctx.textAlign='left';ctx.fillText('100 mm',ml+cW+4,y100+4);
  const ac=dark?'#c3c2b7':'#52514e';
  ctx.strokeStyle=ac;ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(ml,mt);ctx.lineTo(ml,mt+cH);ctx.stroke();
  ctx.beginPath();ctx.moveTo(ml+cW,mt);ctx.lineTo(ml+cW,mt+cH);ctx.stroke();
  ctx.beginPath();ctx.moveTo(ml,mt+cH);ctx.lineTo(ml+cW,mt+cH);ctx.stroke();
  ctx.fillStyle='#d85a30';ctx.font='11px system-ui,sans-serif';ctx.textAlign='right';
  for(let t=Tmin;t<=Tmax;t+=10){const y=yPxT(t);ctx.beginPath();ctx.strokeStyle='#d85a30';ctx.lineWidth=1;ctx.moveTo(ml-4,y);ctx.lineTo(ml,y);ctx.stroke();ctx.fillText(t+' °C',ml-7,y+4);}
  ctx.save();ctx.translate(14,mt+cH/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.font='12px system-ui,sans-serif';ctx.fillText('Température (°C)',0,0);ctx.restore();
  ctx.fillStyle='#2a78d6';ctx.font='11px system-ui,sans-serif';ctx.textAlign='left';
  for(let p=0;p<=100;p+=20){const y=yPxP(p);ctx.beginPath();ctx.strokeStyle='#2a78d6';ctx.lineWidth=1;ctx.moveTo(ml+cW,y);ctx.lineTo(ml+cW+4,y);ctx.stroke();ctx.fillText(p+' mm',ml+cW+7,y+4);}
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
    tr.innerHTML=`<td>${m}</td><td>${b(d.gel[i+1]||0,'badge-gel')}</td><td>${b(d.chaud[i+1]||0,'badge-chaud')}</td><td>${b(d.pluie[i+1]||0,'badge-pluie')}</td>`;
    tbody.appendChild(tr);
  });
  const sum = obj => Object.values(obj).reduce((a,b)=>a+b,0);
  document.getElementById('t-gel').innerHTML   = `<span class="badge badge-gel">${sum(d.gel)}</span>`;
  document.getElementById('t-chaud').innerHTML = `<span class="badge badge-chaud">${sum(d.chaud)}</span>`;
  document.getElementById('t-pluie').innerHTML = `<span class="badge badge-pluie">${sum(d.pluie)}</span>`;
}

// ── Graphiques secondaires ────────────────────────────────────────────────────
function buildSecondaryCharts(){
  const opts = (suf) => ({responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.parsed.y?.toFixed(1)+suf}}},scales:{x:{ticks:{color:tc(),autoSkip:false,maxRotation:0},grid:{color:gc()}},y:{ticks:{color:tc(),callback:v=>v+suf},grid:{color:gc()}}}});
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
  <div class="year-selector">
    <label>Année :</label>
    <select id="yearSelect" onchange="loadYear(+this.value)"></select>
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

    # Toujours : temps réel + accumulation
    live = fetch_realtime()
    build_index(live)

    # Historique : charger ou mettre à jour
    if "--full" in sys.argv or not HIST_FILE.exists():
        hist_dict = update_history(live)
    else:
        if HIST_FILE.exists():
            hist_dict = json.loads(HIST_FILE.read_text())
            # Mettre à jour aujourd'hui
            hist_dict = update_history(live)
        else:
            hist_dict = update_history(live)

    years, data_by_year = aggregate_by_year(hist_dict)
    build_dashboard(years, data_by_year)
    build_climate(years, data_by_year)
    print("✓ Site généré avec succès dans docs/")
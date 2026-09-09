# -*- coding: utf-8 -*-
"""
hiking_score.py
================
Calcule un indice "Sortie Rando" (0-100) pour plusieurs sommets/sites des
Vosges, à partir des prévisions horaires Open-Meteo (déjà utilisé dans
fetch_and_build.py), et génère un bloc HTML à insérer dans index.html.

Contrairement à une première version pensée pour l'API Météo-France, ce
module s'appuie sur Open-Meteo car c'est la source déjà en place dans le
script (fetch_forecast()) — pas besoin de clé API supplémentaire.

INTÉGRATION
-----------
Dans fetch_and_build.py :

    from hiking_score import fetch_hiking_forecasts, build_hiking_report

    forecasts_by_site = fetch_hiking_forecasts()          # 1 appel Open-Meteo
    hiking_html = build_hiking_report(forecasts_by_site)  # bloc HTML

(voir le patch fourni séparément pour les emplacements exacts)
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
import json
import requests

PARIS_TZ = ZoneInfo("Europe/Paris")


# ---------------------------------------------------------------------------
# 1. CONFIGURATION DES SITES
# ---------------------------------------------------------------------------
@dataclass
class SiteProfile:
    name: str
    lat: float
    lon: float
    altitude_m: int
    emoji: str
    w_wind: float
    w_rain: float
    w_storm: float
    w_visibility: float
    w_temp: float
    note: str = ""
    # Facteur d'atténuation du vent (1.0 = site exposé, sans correction).
    # Un couvert forestier coupe une bonne partie des rafales ressenties au
    # sol par rapport à la prévision Open-Meteo (calculée à ~10 m en zone
    # dégagée) : on applique donc gust_kmh * wind_shelter avant de scorer,
    # pour un site où l'itinéraire est réellement en sous-bois.
    wind_shelter: float = 1.0


# NB : coordonnées approximatives, à affiner si besoin (ex: point de départ
# du sentier plutôt que le sommet exact).
SITES: dict[str, SiteProfile] = {
    "petit_ballon": SiteProfile(
        name="Petit Ballon",
        lat=47.9670, lon=7.1000, altitude_m=1267,
        emoji="🥾",
        w_wind=0.30, w_rain=0.20, w_storm=0.25, w_visibility=0.15, w_temp=0.10,
        note="Sommet dégagé, sensible au vent et à l'orage.",
    ),
    "grand_ballon": SiteProfile(
        name="Grand Ballon",
        lat=47.8970, lon=7.0970, altitude_m=1424,
        emoji="⛰️",
        w_wind=0.35, w_rain=0.20, w_storm=0.25, w_visibility=0.15, w_temp=0.05,
        note="Point culminant des Vosges, très exposé au vent.",
    ),
    "hohneck": SiteProfile(
        name="Hohneck",
        lat=48.0390, lon=7.0100, altitude_m=1362,
        emoji="🏔️",
        w_wind=0.30, w_rain=0.20, w_storm=0.25, w_visibility=0.20, w_temp=0.05,
        note="Vue panoramique : très sensible à la visibilité/nébulosité.",
    ),
    "wihr_au_val": SiteProfile(
        name="Wihr-au-Val (sentiers forestiers)",
        lat=48.045944908707554, lon=7.167098450291576, altitude_m=300,
        emoji="🐴",
        w_wind=0.15, w_rain=0.30, w_storm=0.20, w_visibility=0.10, w_temp=0.25,
        note="Randonnée en forêt au départ du centre équestre — bien abritée du vent, mais sensible à la pluie.",
        wind_shelter=0.45,
    ),
    "markstein": SiteProfile(
        name="Markstein",
        lat=47.9080, lon=7.0280, altitude_m=1230,
        emoji="⛷️",
        w_wind=0.30, w_rain=0.20, w_storm=0.30, w_visibility=0.10, w_temp=0.10,
        note="Col ouvert et venteux, altitude proche des grands sommets vosgiens.",
    ),
    "lac_blanc": SiteProfile(
        name="Lac Blanc",
        lat=48.1090, lon=7.0940, altitude_m=1054,
        emoji="🏞️",
        w_wind=0.15, w_rain=0.30, w_storm=0.25, w_visibility=0.15, w_temp=0.15,
        note="Site forestier autour du lac, plus abrité mais sensible à la pluie.",
        wind_shelter=0.65,
    ),
    "wintersberg": SiteProfile(
        name="Grand Wintersberg (Niederbronn)",
        lat=48.97861, lon=7.61472, altitude_m=581,
        emoji="🗼",
        w_wind=0.15, w_rain=0.30, w_storm=0.20, w_visibility=0.20, w_temp=0.15,
        note="Point culminant des Vosges du Nord, massif forestier avec tour panoramique — abrité du vent mais visibilité dépendante de la nébulosité depuis la tour.",
        wind_shelter=0.55,
    ),
}

DAY_WINDOW_START = time(8, 0)
DAY_WINDOW_END = time(18, 0)
BEST_SLOT_DURATION_HOURS = 3

# Codes météo WMO correspondant à un orage
STORM_WMO_CODES = {95, 96, 99}


# ---------------------------------------------------------------------------
# 2. APPEL OPEN-METEO MULTI-SITES + NORMALISATION
# ---------------------------------------------------------------------------
def fetch_hiking_forecasts(site_keys: Optional[list[str]] = None, timeout: int = 15) -> dict[str, list[dict]]:
    """
    Un seul appel Open-Meteo pour tous les sites (coordonnées séparées par
    des virgules -> l'API renvoie un tableau de résultats, un par site,
    dans le même ordre que les coordonnées envoyées).

    Retourne { site_key: [ {datetime, temp_c, wind_kmh, gust_kmh, rain_mm,
                             rain_proba_pct, storm_risk, cloud_cover_pct}, ... ] }
    """
    keys = site_keys or list(SITES.keys())
    sites = [SITES[k] for k in keys]

    lats = ",".join(str(s.lat) for s in sites)
    lons = ",".join(str(s.lon) for s in sites)

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        "&hourly=temperature_2m,precipitation_probability,precipitation,"
        "windspeed_10m,windgusts_10m,cloudcover,weathercode"
        "&timezone=Europe%2FParis"
        "&forecast_days=2"
    )

    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  → Erreur prévisions rando: {e}")
        return {}

    # Avec plusieurs coordonnées, l'API renvoie une LISTE d'objets (un par
    # site). Avec un seul site elle renverrait un objet unique : on
    # normalise donc les deux cas.
    if isinstance(data, dict):
        data = [data]

    result: dict[str, list[dict]] = {}
    for key, site_data in zip(keys, data):
        hourly = site_data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        rain_proba = hourly.get("precipitation_probability", [])
        rain_mm = hourly.get("precipitation", [])
        wind = hourly.get("windspeed_10m", [])
        gust = hourly.get("windgusts_10m", [])
        clouds = hourly.get("cloudcover", [])
        codes = hourly.get("weathercode", [])

        normalized = []
        for i, t in enumerate(times):
            code = codes[i] if i < len(codes) else None
            normalized.append({
                "datetime": datetime.fromisoformat(t),
                "temp_c": temps[i] if i < len(temps) else 15.0,
                "wind_kmh": wind[i] if i < len(wind) else 0.0,
                "gust_kmh": gust[i] if i < len(gust) else (wind[i] if i < len(wind) else 0.0),
                "rain_mm": rain_mm[i] if i < len(rain_mm) else 0.0,
                "rain_proba_pct": rain_proba[i] if i < len(rain_proba) else 0.0,
                "storm_risk": code in STORM_WMO_CODES if code is not None else False,
                "cloud_cover_pct": clouds[i] if i < len(clouds) else 50.0,
            })
        result[key] = normalized

    Path("docs/hiking.json").write_text(
        json.dumps({k: [{**h, "datetime": h["datetime"].isoformat()} for h in v] for k, v in result.items()},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  → Prévisions rando : {len(result)} sites")
    return result


# ---------------------------------------------------------------------------
# 3. CALCUL DU SCORE POUR UNE HEURE DONNÉE
# ---------------------------------------------------------------------------
def _score_wind(gust_kmh: float) -> float:
    if gust_kmh <= 20:
        return 100
    if gust_kmh >= 70:
        return 0
    return max(0, 100 - (gust_kmh - 20) * (100 / 50))


def _score_rain(rain_mm: float, rain_proba_pct: float) -> float:
    base = 100 - min(rain_mm, 5) * 15
    base -= rain_proba_pct * 0.3
    return max(0, min(100, base))


def _score_storm(storm_risk: bool) -> float:
    return 5 if storm_risk else 100


def _score_visibility(cloud_cover_pct: float) -> float:
    return max(0, 100 - cloud_cover_pct)


def _score_temp(temp_c: float, altitude_m: int) -> float:
    apparent = temp_c - (altitude_m / 1000) * 2
    if 5 <= apparent <= 22:
        return 100
    if apparent < -10 or apparent > 32:
        return 0
    if apparent < 5:
        return max(0, 100 - (5 - apparent) * 8)
    return max(0, 100 - (apparent - 22) * 8)


def score_hour(hour_data: dict, site: SiteProfile) -> float:
    effective_gust = hour_data["gust_kmh"] * site.wind_shelter
    s_wind = _score_wind(effective_gust)
    s_rain = _score_rain(hour_data["rain_mm"], hour_data.get("rain_proba_pct", 0))
    s_storm = _score_storm(hour_data.get("storm_risk", False))
    s_vis = _score_visibility(hour_data.get("cloud_cover_pct", 50))
    s_temp = _score_temp(hour_data["temp_c"], site.altitude_m)

    total = (
        s_wind * site.w_wind
        + s_rain * site.w_rain
        + s_storm * site.w_storm
        + s_vis * site.w_visibility
        + s_temp * site.w_temp
    )
    return round(total, 1)


# ---------------------------------------------------------------------------
# 4. MEILLEUR CRÉNEAU DU JOUR
# ---------------------------------------------------------------------------
def best_window_for_site(hourly_forecast: list[dict], site: SiteProfile) -> Optional[dict]:
    today = datetime.now(PARIS_TZ).date()
    today_hours = [
        h for h in hourly_forecast
        if h["datetime"].date() == today
        and DAY_WINDOW_START <= h["datetime"].time() <= DAY_WINDOW_END
    ]
    today_hours.sort(key=lambda h: h["datetime"])

    if len(today_hours) < BEST_SLOT_DURATION_HOURS:
        return None

    best = None
    for i in range(len(today_hours) - BEST_SLOT_DURATION_HOURS + 1):
        block = today_hours[i:i + BEST_SLOT_DURATION_HOURS]
        scores = [score_hour(h, site) for h in block]
        avg = sum(scores) / len(scores)
        if best is None or avg > best["score"]:
            best = {"score": round(avg, 1), "start": block[0]["datetime"], "end": block[-1]["datetime"]}
    return best


# ---------------------------------------------------------------------------
# 4b. RÉSUMÉ MÉTÉO DU JOUR (temp / pluie / vent)
# ---------------------------------------------------------------------------
def day_weather_summary(hourly_forecast: list[dict]) -> Optional[dict]:
    """
    Résume la journée en cours pour un site : plage de température,
    probabilité de pluie max + cumul, vent moyen/rafale max, risque d'orage.
    Utilisé pour remplacer la note statique par les prévisions du jour.
    """
    today = datetime.now(PARIS_TZ).date()
    today_hours = [h for h in hourly_forecast if h["datetime"].date() == today]
    if not today_hours:
        return None

    temps = [h["temp_c"] for h in today_hours]
    rain_total = sum(h.get("rain_mm", 0) or 0 for h in today_hours)
    rain_proba_max = max((h.get("rain_proba_pct", 0) or 0 for h in today_hours), default=0)
    wind_max = max((h.get("wind_kmh", 0) or 0 for h in today_hours), default=0)
    gust_max = max((h.get("gust_kmh", 0) or 0 for h in today_hours), default=0)
    storm = any(h.get("storm_risk") for h in today_hours)

    return {
        "temp_min": round(min(temps)),
        "temp_max": round(max(temps)),
        "rain_total_mm": round(rain_total, 1),
        "rain_proba_max_pct": round(rain_proba_max),
        "wind_max_kmh": round(wind_max),
        "gust_max_kmh": round(gust_max),
        "storm_risk": storm,
    }


def render_day_weather_note(summary: Optional[dict]) -> str:
    """Formate le résumé météo du jour en une ligne compacte pour la carte du site."""
    if summary is None:
        return "Prévisions du jour indisponibles."

    parts = [f"🌡️ {summary['temp_min']}–{summary['temp_max']}°C"]

    if summary["rain_total_mm"] > 0.1:
        parts.append(f"🌧️ {summary['rain_proba_max_pct']}% ({summary['rain_total_mm']} mm)")
    else:
        parts.append(f"🌧️ {summary['rain_proba_max_pct']}%")

    wind_str = f"💨 {summary['wind_max_kmh']} km/h"
    if summary["gust_max_kmh"] > summary["wind_max_kmh"] + 5:
        wind_str += f" (raf. {summary['gust_max_kmh']})"
    parts.append(wind_str)

    if summary["storm_risk"]:
        parts.append("⛈️ Risque d'orage")

    return " · ".join(parts)


# ---------------------------------------------------------------------------
# 5. LABEL / EMOJI
# ---------------------------------------------------------------------------
def label_for_score(score: float) -> tuple[str, str]:
    if score >= 80:
        return "Excellent", "🟢"
    if score >= 60:
        return "Bonne journée", "🟢"
    if score >= 40:
        return "Correct, avec prudence", "🟡"
    if score >= 20:
        return "Déconseillé", "🟠"
    return "À éviter", "🔴"


# ---------------------------------------------------------------------------
# 6. RENDU HTML
# ---------------------------------------------------------------------------
def build_hiking_report(forecasts_by_site: dict[str, list[dict]]) -> str:
    """
    forecasts_by_site : { site_key: [hour_dict, ...] } déjà normalisé
    (sortie directe de fetch_hiking_forecasts()).

    Rendu condensé : un bandeau avec la meilleure sortie du jour, et le
    détail des sites dans un <details> repliable (pas de JS requis).
    """
    results = []  # (key, site, window|None, hourly)
    for key, site in SITES.items():
        hourly = forecasts_by_site.get(key, [])
        window = best_window_for_site(hourly, site) if hourly else None
        results.append((key, site, window, hourly))

    valid = [(key, site, w, hourly) for key, site, w, hourly in results if w is not None]

    # ── Bandeau résumé (meilleure sortie du jour) ──────────────────────────
    if not valid:
        summary_html = """
        <div class="hiking-best hiking-best--unknown">
            <span class="hiking-best-emoji">🥾</span>
            <div>
                <div class="hiking-best-title">Prévisions indisponibles pour l'instant.</div>
            </div>
        </div>"""
    else:
        best_key, best_site, best_window, _ = max(valid, key=lambda t: t[2]["score"])
        label, badge = label_for_score(best_window["score"])
        start_str = best_window["start"].strftime("%Hh%M")
        end_str = best_window["end"].strftime("%Hh%M")
        summary_html = f"""
        <div class="hiking-best">
            <span class="hiking-best-emoji">{best_site.emoji}</span>
            <div>
                <div class="hiking-best-title">Meilleure sortie aujourd'hui : <b>{best_site.name}</b></div>
                <div class="hiking-best-sub">{badge} {label} — {best_window['score']}/100 · créneau {start_str}–{end_str}</div>
            </div>
        </div>"""

    # ── Détail des sites (repliable) ─────────────────────────────────────
    cards = []
    for key, site, window, hourly in results:
        weather_note = render_day_weather_note(day_weather_summary(hourly))

        if window is None:
            cards.append(f"""
            <div class="hiking-card hiking-card--unknown">
                <h3>{site.emoji} {site.name}</h3>
                <p class="hiking-note">{weather_note}</p>
            </div>""")
            continue

        label, badge = label_for_score(window["score"])
        start_str = window["start"].strftime("%Hh%M")
        end_str = window["end"].strftime("%Hh%M")

        cards.append(f"""
            <div class="hiking-card" data-score="{window['score']}">
                <h3>{site.emoji} {site.name} <span class="alt">({site.altitude_m} m)</span></h3>
                <p class="hiking-score">{badge} {label} — {window['score']}/100</p>
                <p class="hiking-window">Meilleur créneau : {start_str} – {end_str}</p>
                <p class="hiking-note">{weather_note}</p>
            </div>""")

    return f"""<section class="hiking-index">
    <h2>🥾 Indice Sortie Rando – Vosges</h2>
    {summary_html}
    <details class="hiking-details">
        <summary>Voir le détail des {len(results)} sites</summary>
        <p class="hiking-intro">Score sur le meilleur créneau du jour ({DAY_WINDOW_START.strftime('%Hh')}–{DAY_WINDOW_END.strftime('%Hh')}), basé sur le vent, la pluie, le risque d'orage, la visibilité et la température ressentie en altitude (Open-Meteo). {len(results)} sites suivis, des Vosges du Sud (Grand Ballon, Hohneck…) aux Vosges du Nord (Grand Wintersberg).</p>
        <div class="hiking-grid">
            {''.join(cards)}
        </div>
    </details>
</section>"""


# ---------------------------------------------------------------------------
# 7. TEST LOCAL (python hiking_score.py) — utilise l'API réelle
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    forecasts = fetch_hiking_forecasts()
    print(build_hiking_report(forecasts))
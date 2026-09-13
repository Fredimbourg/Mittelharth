#!/usr/bin/env python3
"""
fetch_reference_station.py — Climatologie de référence (Colmar-Meyenheim)
==========================================================================

Construit docs/reference_history.json à partir des données PUBLIQUES et
GRATUITES de Météo-France (aucune clé API, licence Ouverte 2.0), pour servir
de base de comparaison robuste à l'indice de rareté du dashboard
(fetch_and_build.py / compute_all_anomalies).

Pourquoi un script séparé, distinct de fetch_and_build.py ?
-------------------------------------------------------------
fetch_and_build.py tourne toutes les heures (données temps réel de la
station Ecowitt). L'historique climatologique officiel, lui, ne change
quasiment jamais pour les années passées — inutile de le retélécharger 24
fois par jour. Ce script est fait pour tourner rarement (une fois pour
l'initialiser, puis par ex. une fois par mois pour récupérer les derniers
mois publiés par Météo-France).

Source des données
-------------------
Jeu de données « Données climatologiques de base - quotidiennes »
(Météo-France, licence Ouverte 2.0, gratuit, sans clé) :
https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-quotidiennes

Ce jeu de données est distribué par département, en un ou plusieurs
fichiers csv.gz (ex. un fichier "avant 1950", un "1950-année-2", un
"2 dernières années"). Ce script les repère automatiquement via l'API du
jeu de données (pas d'URL de fichier codée en dur, qui deviendrait obsolète
à la prochaine mise à jour Météo-France) et ne conserve que les lignes de la
station demandée (par défaut : Colmar-Meyenheim, poste 68205001).

⚠️ Ce script n'a PAS pu être testé de bout en bout dans l'environnement où
il a été écrit (accès réseau restreint à data.gouv.fr). La structure du jeu
de données a été vérifiée via sa documentation et des réutilisations
publiques, mais il est recommandé de lancer une première fois ce script
manuellement (`python3 fetch_reference_station.py`) et de vérifier le
résultat avant de l'automatiser dans un workflow planifié.
"""

import csv
import gzip
import io
import json
import re
import sys
from pathlib import Path

import requests

# ── Configuration ─────────────────────────────────────────────────────────
DATASET_API_URL = "https://www.data.gouv.fr/api/1/datasets/6569b51ae64326786e4e8e1a/"
DEPARTEMENT     = "68"        # Haut-Rhin
NUM_POSTE       = "68205001"  # Colmar-Meyenheim, base militaire (OACI LFSC / WMO 07197)
OUT_FILE        = Path("docs/reference_history.json")


def fv(x):
    """Convertit une valeur CSV Météo-France (chaîne, virgule décimale
    possible, vide si absente) en float ou None."""
    if x is None:
        return None
    x = x.strip()
    if x == "":
        return None
    try:
        return float(x.replace(",", "."))
    except ValueError:
        return None


def find_department_resources():
    """Interroge l'API du jeu de données pour trouver les fichiers csv(.gz)
    du département demandé, quel que soit leur découpage en périodes
    (« avant 1950 », « 1950-2023 », « 2 dernières années », etc.) — évite de
    coder en dur des noms de fichiers ou des UUID de ressource qui changent
    à chaque mise à jour de Météo-France."""
    r = requests.get(DATASET_API_URL, timeout=30)
    r.raise_for_status()
    resources = r.json().get("resources", [])

    pattern = re.compile(rf"(^|[_-]){DEPARTEMENT}([_-]|$)", re.IGNORECASE)
    matches = [
        res for res in resources
        if pattern.search(res.get("title", "")) and res.get("format", "").startswith("csv")
    ]
    if not matches:
        raise RuntimeError(
            f"Aucun fichier csv trouvé pour le département {DEPARTEMENT} dans le jeu de "
            "données Météo-France. La structure du jeu de données a peut-être changé — "
            "vérifier manuellement sur https://www.data.gouv.fr/datasets/"
            "donnees-climatologiques-de-base-quotidiennes"
        )
    return matches


def download_station_rows(resource):
    """Télécharge une ressource (csv.gz ou csv) et ne garde que les lignes
    du poste demandé (NUM_POSTE), en tolérant les zéros de tête éventuels."""
    url = resource["url"]
    title = resource.get("title", url)
    print(f"  → Téléchargement : {title}")
    r = requests.get(url, timeout=180)
    r.raise_for_status()

    raw = r.content
    if resource.get("format", "").endswith("gz") or url.endswith(".gz"):
        text = gzip.decompress(raw).decode("latin-1", errors="replace")
    else:
        text = raw.decode("latin-1", errors="replace")

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    target = NUM_POSTE.lstrip("0")
    rows = [row for row in reader if (row.get("NUM_POSTE") or "").strip().lstrip("0") == target]
    print(f"     {len(rows)} jours trouvés pour le poste {NUM_POSTE}")
    return rows


def build_reference_history():
    print(f"Recherche des fichiers du département {DEPARTEMENT}...")
    resources = find_department_resources()

    hist = {}
    for resource in resources:
        for row in download_station_rows(resource):
            aaaammjj = (row.get("AAAAMMJJ") or "").strip()
            if len(aaaammjj) != 8 or not aaaammjj.isdigit():
                continue
            date_str = f"{aaaammjj[:4]}-{aaaammjj[4:6]}-{aaaammjj[6:8]}"
            hist[date_str] = {
                "date": date_str,
                "year": int(aaaammjj[:4]),
                "month": int(aaaammjj[4:6]),
                "day": int(aaaammjj[6:8]),
                "hi":   fv(row.get("TX")),   # température maximale (°C)
                "lo":   fv(row.get("TN")),   # température minimale (°C)
                "avg":  fv(row.get("TM")),   # température moyenne (°C)
                "rain": fv(row.get("RR")),   # précipitations (mm)
                "hum":  fv(row.get("UM")),   # humidité moyenne (%), absente sur les très anciennes années
            }

    if not hist:
        print("✗ Aucune donnée récupérée — le fichier existant (s'il y en a un) n'a pas été modifié.")
        sys.exit(1)

    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    dates = sorted(hist)
    n_years = len(set(d[:4] for d in dates))
    print(f"✓ {len(hist)} jours enregistrés dans {OUT_FILE}")
    print(f"  Période : {dates[0]} → {dates[-1]} ({n_years} années)")
    return hist


if __name__ == "__main__":
    build_reference_history()
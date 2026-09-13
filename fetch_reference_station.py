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

Ce jeu de données est distribué par département, en plusieurs fichiers
csv.gz, découpés selon DEUX axes indépendants :
  1) la PÉRIODE (« avant-1949 », « 1950-2024 », « 2025-2026 », etc.) ;
  2) la FAMILLE DE PARAMÈTRES : chaque département/période existe en deux
     fichiers distincts, "RR-T-Vent" (pluie RR, températures TN/TX/TM,
     vent) et "autres-parametres" (humidité UM, neige, orage, etc.).

⚠️ Point important, à l'origine d'un bug corrigé dans cette version : un
fichier "autres-parametres" ne contient PAS les colonnes RR/TN/TX/TM, et un
fichier "RR-T-Vent" ne contient pas UM. Si on traite les ressources les
unes après les autres en écrasant hist[date] à chaque ligne, le fichier
traité en dernier efface les champs du premier (typiquement : toutes les
températures/pluie disparaissent, seule l'humidité survit). La fonction
build_reference_history() ci-dessous FUSIONNE donc les champs par date,
sans jamais écraser une valeur déjà connue par un None.

Ce script les repère automatiquement via l'API du jeu de données (pas
d'URL de fichier codée en dur, qui deviendrait obsolète à la prochaine
mise à jour Météo-France) et ne conserve que les lignes de la station
demandée (par défaut : Colmar-Meyenheim, poste 68205001).
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

# Correspondance colonne CSV Météo-France → clé de sortie. Une seule ligne
# de config, réutilisée à la fois pour la fusion RR-T-Vent / autres-parametres
# et pour l'initialisation d'une entrée vide.
FIELD_MAP = {
    "hi":   "TX",  # température maximale (°C)
    "lo":   "TN",  # température minimale (°C)
    "avg":  "TM",  # température moyenne (°C)
    "rain": "RR",  # précipitations (mm)
    "hum":  "UM",  # humidité moyenne (%) — absente sur les très anciennes années
}


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
    (« avant 1950 », « 1950-2023 », « 2 dernières années », etc.) ET quelle
    que soit leur famille de paramètres (RR-T-Vent / autres-parametres) —
    on veut bien récupérer LES DEUX familles, elles sont fusionnées ensuite
    par build_reference_history(). Évite de coder en dur des noms de
    fichiers ou des UUID de ressource qui changent à chaque mise à jour de
    Météo-France."""
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

    n_rrtvent = sum(1 for res in matches if "rr-t-vent" in res.get("title", "").lower())
    n_autres  = sum(1 for res in matches if "autres-parametres" in res.get("title", "").lower())
    print(f"  → {len(matches)} fichiers trouvés ({n_rrtvent} RR-T-Vent, {n_autres} autres-parametres)")
    if n_rrtvent == 0 or n_autres == 0:
        print("  ⚠ Une des deux familles de paramètres est absente des résultats — "
              "les températures/pluie ou l'humidité risquent d'être incomplètes.")
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
    total_rows = 0
    for resource in resources:
        for row in download_station_rows(resource):
            total_rows += 1
            aaaammjj = (row.get("AAAAMMJJ") or "").strip()
            if len(aaaammjj) != 8 or not aaaammjj.isdigit():
                continue
            date_str = f"{aaaammjj[:4]}-{aaaammjj[4:6]}-{aaaammjj[6:8]}"

            # FUSION plutôt qu'écrasement : une même date peut apparaître
            # dans le fichier RR-T-Vent (avec TX/TN/TM/RR) ET dans le
            # fichier autres-parametres (avec UM), pour la même période.
            # setdefault réutilise l'entrée déjà créée par le fichier
            # traité précédemment ; chaque champ n'est écrit que si la
            # colonne correspondante est réellement présente dans CETTE
            # ligne (sinon on garde la valeur déjà connue, le cas échéant).
            entry = hist.setdefault(date_str, {
                "date": date_str,
                "year": int(aaaammjj[:4]),
                "month": int(aaaammjj[4:6]),
                "day": int(aaaammjj[6:8]),
                "hi": None, "lo": None, "avg": None, "rain": None, "hum": None,
            })
            for key, col in FIELD_MAP.items():
                val = fv(row.get(col))
                if val is not None:
                    entry[key] = val

    if not hist:
        print("✗ Aucune donnée récupérée — le fichier existant (s'il y en a un) n'a pas été modifié.")
        sys.exit(1)

    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    dates = sorted(hist)
    n_years = len(set(d[:4] for d in dates))

    # Petit résumé de complétude par champ, pour repérer immédiatement un
    # futur problème de ce genre sans avoir à ouvrir le JSON à la main.
    n = len(hist)
    completeness = {
        key: sum(1 for d in hist.values() if d.get(key) is not None)
        for key in FIELD_MAP
    }
    print(f"✓ {n} jours enregistrés dans {OUT_FILE} ({total_rows} lignes lues au total)")
    print(f"  Période : {dates[0]} → {dates[-1]} ({n_years} années)")
    print("  Complétude par champ :")
    for key, count in completeness.items():
        print(f"    {key:5s} ({FIELD_MAP[key]}) : {count}/{n} ({100*count/n:.0f}%)")
    return hist


if __name__ == "__main__":
    build_reference_history()
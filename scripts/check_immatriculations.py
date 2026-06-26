"""
Veille immatriculations Bretagne — script GitHub Actions
Interroge l'API data.gouv, filtre par date, upsert Supabase, alerte Resend.
"""

import os
import time
from datetime import datetime, timedelta

import requests

# --- Config (variables d'environnement GitHub Actions) ---

API_URL = "https://recherche-entreprises.api.gouv.fr/search"
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
ALERT_EMAIL = os.environ["ALERT_EMAIL"]
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

APE_CODES = [
    "56.10A", "56.10C", "55.10Z", "56.30Z",
    "10.71C", "10.71D", "47.22Z", "47.24Z", "56.21Z",
]
APE_LABELS = {
    "56.10A": "Restauration traditionnelle",
    "56.10C": "Restauration rapide",
    "55.10Z": "Hôtels et hébergement",
    "56.30Z": "Débits de boissons",
    "10.71C": "Boulangerie-pâtisserie",
    "10.71D": "Pâtisserie",
    "47.22Z": "Boucherie-charcuterie",
    "47.24Z": "Commerce pain/pâtisserie",
    "56.21Z": "Services traiteurs",
}
DEPARTEMENTS = ["22", "29", "35", "56"]
JOURS = 14


# --- Utilitaires ---

def date_limite():
    return (datetime.now() - timedelta(days=JOURS)).strftime("%Y-%m-%d")


def extraire_dirigeant(dirigeants):
    if not dirigeants:
        return ""
    d = dirigeants[0]
    if "physique" in d.get("type_dirigeant", ""):
        return f"{d.get('prenoms', '')} {d.get('nom', '')}".strip()
    return d.get("denomination", "")


# --- API data.gouv ---

def fetch_avec_retry(url, params, tentatives=3):
    for i in range(tentatives):
        r = requests.get(url, params=params, timeout=30)
        if r.ok:
            return r
        if r.status_code == 429:
            attente = (i + 1) * 2
            print(f"  Rate limit 429, attente {attente}s...")
            time.sleep(attente)
        else:
            raise Exception(f"Erreur API : {r.status_code}")
    raise Exception("Trop de tentatives (429)")


def fetch_departement(departement):
    resultats = []
    page = 1
    while True:
        params = {
            "activite_principale": ",".join(APE_CODES),
            "departement": departement,
            "etat_administratif": "A",
            "per_page": 25,
            "page": page,
        }
        r = fetch_avec_retry(API_URL, params)
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        resultats.extend(results)
        if page >= data.get("total_pages", 1):
            break
        page += 1
        time.sleep(0.3)
    return resultats


def fetch_toutes_pages():
    resultats = []
    for dept in DEPARTEMENTS:
        print(f"  Département {dept}...")
        resultats.extend(fetch_departement(dept))
        time.sleep(0.5)
    return resultats


def filtrer(entreprises, depuis):
    return [e for e in entreprises if (e.get("date_creation") or "") >= depuis]


# --- Supabase REST API ---

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def get_sirens_connus(sirens):
    if not sirens:
        return set()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/immatriculations",
        params={"select": "siren", "siren": f"in.({','.join(sirens)})"},
        headers=supabase_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return {row["siren"] for row in r.json()}


def upsert_supabase(rows):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/immatriculations?on_conflict=siren",
        json=rows,
        headers={**supabase_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=60,
    )
    r.raise_for_status()


# --- Scan Google Business ---

def _nom_similaire(nom_cherche, nom_trouve):
    n1 = nom_cherche.lower().strip()
    n2 = nom_trouve.lower().strip()
    mots = [m for m in n1.split() if len(m) > 2]
    return any(mot in n2 for mot in mots)


def _commune_similaire(commune, adresse):
    return commune.lower().strip() in (adresse or "").lower()


def check_google_business(nom, commune):
    r = requests.get("https://serpapi.com/search.json", params={
        "engine": "google_maps",
        "q": f"{nom} {commune}",
        "hl": "fr",
        "gl": "fr",
        "api_key": SERPAPI_KEY,
    }, timeout=15)
    if not r.ok:
        return {"google_business": None}

    results = r.json().get("local_results", [])
    vide = {"google_business": False, "google_place_id": None, "google_rating": None,
            "google_reviews": None, "phone": None, "website": None}
    if not results:
        return vide

    first = results[0]
    if not _nom_similaire(nom, first.get("title", "")) or \
       not _commune_similaire(commune, first.get("address", "")):
        return vide

    return {
        "google_business": True,
        "google_place_id": first.get("place_id"),
        "google_rating": first.get("rating"),
        "google_reviews": first.get("reviews"),
        "phone": first.get("phone"),
        "website": first.get("website"),
    }


def get_non_scannes():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/immatriculations",
        params={"select": "siren,raison_sociale,commune", "google_checked_at": "is.null"},
        headers=supabase_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def update_google(siren, data):
    data["google_checked_at"] = datetime.now().astimezone().isoformat()
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/immatriculations",
        json=data,
        params={"siren": f"eq.{siren}"},
        headers=supabase_headers(),
        timeout=15,
    )
    r.raise_for_status()


def scan_google():
    if not SERPAPI_KEY:
        print("SERPAPI_KEY non défini, scan Google ignoré.")
        return

    entreprises = get_non_scannes()
    print(f"{len(entreprises)} entreprise(s) à scanner sur Google Business...")

    for i, e in enumerate(entreprises, 1):
        nom = e.get("raison_sociale", "")
        commune = e.get("commune", "")
        siren = e.get("siren", "")
        print(f"  [{i}/{len(entreprises)}] {nom} ({commune})...", end=" ", flush=True)

        google = check_google_business(nom, commune)
        update_google(siren, google)

        statut = "✅" if google.get("google_business") else "❌"
        print(f"{statut} Tel: {google.get('phone') or '-'}")
        time.sleep(1)

    print("Scan Google terminé.")


# --- Email Resend ---

def construire_html(entreprises, depuis):
    lignes = "".join(f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">{e.get("nom_complet","")}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">{extraire_dirigeant(e.get("dirigeants",[]))}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">{e.get("siege",{}).get("libelle_commune","")}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">{APE_LABELS.get(e.get("activite_principale",""), e.get("activite_principale",""))}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">{e.get("date_creation","")}</td>
        </tr>""" for e in entreprises)

    return f"""
    <div style="font-family:sans-serif;max-width:800px;margin:0 auto;padding:24px;">
      <h2 style="color:#2563eb;">Nouvelles immatriculations — Bretagne</h2>
      <p style="color:#64748b;">{len(entreprises)} nouvelle(s) immatriculation(s) depuis le {depuis}.</p>
      <table style="width:100%;border-collapse:collapse;margin-top:16px;">
        <thead><tr style="background:#f8fafc;">
          <th style="padding:10px;text-align:left;font-size:12px;color:#64748b;">Raison sociale</th>
          <th style="padding:10px;text-align:left;font-size:12px;color:#64748b;">Dirigeant</th>
          <th style="padding:10px;text-align:left;font-size:12px;color:#64748b;">Commune</th>
          <th style="padding:10px;text-align:left;font-size:12px;color:#64748b;">Activité</th>
          <th style="padding:10px;text-align:left;font-size:12px;color:#64748b;">Date création</th>
        </tr></thead>
        <tbody>{lignes}</tbody>
      </table>
      <p style="margin-top:24px;color:#64748b;font-size:13px;">
        Tableau de bord : <a href="https://immatriculations-22.vercel.app">immatriculations-22.vercel.app</a>
      </p>
    </div>"""


def envoyer_email(entreprises, depuis):
    date = datetime.now().strftime("%-d %B %Y")
    r = requests.post(
        "https://api.resend.com/emails",
        json={
            "from": "Veille Immatriculations <onboarding@resend.dev>",
            "to": ALERT_EMAIL,
            "subject": f"🏪 {len(entreprises)} nouvelle(s) immatriculation(s) — {date}",
            "html": construire_html(entreprises, depuis),
        },
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    print(f"  Email envoyé à {ALERT_EMAIL}.")


# --- Point d'entrée ---

def main():
    depuis = date_limite()
    print(f"Recherche depuis le {depuis}...")

    toutes = fetch_toutes_pages()
    print(f"{len(toutes)} entreprises actives trouvées.")

    retenues = filtrer(toutes, depuis)
    print(f"{len(retenues)} nouvelles immatriculations.")

    if not retenues:
        print("Aucune nouvelle immatriculation.")
        return

    sirens = [e["siren"] for e in retenues if e.get("siren")]
    sirens_connus = get_sirens_connus(sirens)
    vraiment_nouveaux = [e for e in retenues if e.get("siren") and e["siren"] not in sirens_connus]
    print(f"{len(vraiment_nouveaux)} vraiment nouvelle(s) (pas encore en base).")

    rows = [{
        "siren": e.get("siren", ""),
        "raison_sociale": e.get("nom_complet", ""),
        "dirigeant": extraire_dirigeant(e.get("dirigeants", [])),
        "adresse": e.get("siege", {}).get("adresse", ""),
        "code_postal": e.get("siege", {}).get("code_postal", ""),
        "commune": e.get("siege", {}).get("libelle_commune", ""),
        "date_creation": e.get("date_creation") or None,
        "code_ape": e.get("activite_principale", ""),
        "activite": APE_LABELS.get(e.get("activite_principale", ""), e.get("activite_principale", "")),
    } for e in retenues]

    upsert_supabase(rows)
    print(f"{len(rows)} entrées sauvegardées dans Supabase.")

    if vraiment_nouveaux:
        envoyer_email(vraiment_nouveaux, depuis)

    scan_google()


if __name__ == "__main__":
    main()

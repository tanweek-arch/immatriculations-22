/**
 * Fonction Netlify planifiée — tourne tous les lundis à 8h
 * Interroge l'API data.gouv, stocke les nouvelles immatriculations dans Supabase
 * et envoie une alerte email via Resend.
 */

import { schedule } from "@netlify/functions";
import { createClient } from "@supabase/supabase-js";
import { Resend } from "resend";

// --- Configuration ---

const API_URL = "https://recherche-entreprises.api.gouv.fr/search";

const APE_CODES = [
  "56.10A", // Restauration traditionnelle
  "56.10C", // Restauration rapide
  "55.10Z", // Hôtels et hébergement similaire
  "56.30Z", // Débits de boissons
  "10.71C", // Boulangerie-pâtisserie
  "10.71D", // Pâtisserie
  "47.22Z", // Boucherie-charcuterie
  "47.24Z", // Commerce pain/pâtisserie
  "56.21Z", // Services traiteurs
];

const APE_LABELS = {
  "56.10A": "Restauration traditionnelle",
  "56.10C": "Restauration rapide",
  "55.10Z": "Hôtels et hébergement",
  "56.30Z": "Débits de boissons",
  "10.71C": "Boulangerie-pâtisserie",
  "10.71D": "Pâtisserie",
  "47.22Z": "Boucherie-charcuterie",
  "47.24Z": "Commerce pain/pâtisserie",
  "56.21Z": "Services traiteurs",
};

const DEPARTEMENTS = ["22", "29", "35", "56"];
const JOURS = 14;

// --- Utilitaires ---

function dateLimite() {
  const d = new Date();
  d.setDate(d.getDate() - JOURS);
  return d.toISOString().split("T")[0];
}

function extraireDirigeant(dirigeants) {
  if (!dirigeants || dirigeants.length === 0) return "";
  const d = dirigeants[0];
  if ((d.type_dirigeant || "").includes("physique")) {
    return `${d.prenoms || ""} ${d.nom || ""}`.trim();
  }
  return d.denomination || "";
}

// --- Récupération API ---

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchAvecRetry(url, tentatives = 3) {
  for (let i = 0; i < tentatives; i++) {
    const resp = await fetch(url);
    if (resp.ok) return resp;
    if (resp.status === 429) {
      const attente = (i + 1) * 2000;
      console.log(`Rate limit (429), attente ${attente}ms avant retry...`);
      await sleep(attente);
    } else {
      throw new Error(`Erreur API : ${resp.status}`);
    }
  }
  throw new Error("Erreur API : trop de tentatives (429)");
}

async function fetchDepartement(departement, depuis) {
  const resultats = [];
  let page = 1;

  while (true) {
    const params = new URLSearchParams({
      activite_principale: APE_CODES.join(","),
      departement,
      etat_administratif: "A",
      date_creation_min: depuis,
      per_page: "25",
      page: String(page),
    });

    const resp = await fetchAvecRetry(`${API_URL}?${params}`);
    const data = await resp.json();
    const results = data.results || [];
    if (results.length === 0) break;

    resultats.push(...results);
    if (page >= (data.total_pages || 1)) break;
    page++;
    await sleep(300);
  }

  return resultats;
}

async function fetchToutesPages(depuis) {
  const resultats = [];
  for (const dept of DEPARTEMENTS) {
    console.log(`Récupération département ${dept}...`);
    resultats.push(...await fetchDepartement(dept, depuis));
    await sleep(500);
  }
  return resultats;
}

// --- Email HTML ---

function construireEmailHtml(entreprises, depuis) {
  const lignes = entreprises
    .map((e) => {
      const siege = e.siege || {};
      return `
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">${e.nom_complet || ""}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">${extraireDirigeant(e.dirigeants || [])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">${siege.libelle_commune || ""}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">${APE_LABELS[e.activite_principale] || e.activite_principale || ""}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e2e8f0;">${e.date_creation || ""}</td>
        </tr>`;
    })
    .join("");

  return `
    <div style="font-family:sans-serif;max-width:800px;margin:0 auto;padding:24px;">
      <h2 style="color:#2563eb;">Nouvelles immatriculations — Bretagne</h2>
      <p style="color:#64748b;">${entreprises.length} nouvelle(s) immatriculation(s) détectée(s) depuis le ${depuis}.</p>
      <table style="width:100%;border-collapse:collapse;margin-top:16px;">
        <thead>
          <tr style="background:#f8fafc;">
            <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;text-transform:uppercase;">Raison sociale</th>
            <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;text-transform:uppercase;">Dirigeant</th>
            <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;text-transform:uppercase;">Commune</th>
            <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;text-transform:uppercase;">Activité</th>
            <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;text-transform:uppercase;">Date création</th>
          </tr>
        </thead>
        <tbody>${lignes}</tbody>
      </table>
      <p style="margin-top:24px;color:#64748b;font-size:13px;">
        Consultez le tableau de bord complet sur votre plateforme Netlify.
      </p>
    </div>`;
}

// --- Google Business ---

// --- Handler principal ---

async function run() {
  const supabase = createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_SERVICE_ROLE_KEY
  );

  const depuis = dateLimite();
  console.log(`Recherche depuis le ${depuis}...`);

  const toutes = await fetchToutesPages(depuis);
  console.log(`${toutes.length} nouvelles immatriculations trouvées depuis le ${depuis}.`);

  const retenues = toutes;

  if (retenues.length === 0) {
    console.log("Aucune nouvelle immatriculation sur la période.");
    return { statusCode: 200 };
  }

  // Récupérer les SIRENs déjà connus dans Supabase
  const sirensRetenues = retenues.map((e) => e.siren);
  const { data: dejaDansBdd } = await supabase
    .from("immatriculations")
    .select("siren")
    .in("siren", sirensRetenues);

  const sirensConnus = new Set((dejaDansBdd || []).map((r) => r.siren));
  const vraimentNouveaux = retenues.filter((e) => !sirensConnus.has(e.siren));
  console.log(`${vraimentNouveaux.length} vraiment nouvelle(s) (pas encore en base).`);

  // Upsert dans Supabase (le siren unique évite les doublons)
  const rows = retenues.map((e) => ({
    siren: e.siren,
    raison_sociale: e.nom_complet || "",
    dirigeant: extraireDirigeant(e.dirigeants || []),
    adresse: e.siege?.adresse || "",
    code_postal: e.siege?.code_postal || "",
    commune: e.siege?.libelle_commune || "",
    date_creation: e.date_creation || null,
    code_ape: e.activite_principale || "",
    activite: APE_LABELS[e.activite_principale] || e.activite_principale || "",
  }));

  const { error } = await supabase
    .from("immatriculations")
    .upsert(rows, { onConflict: "siren" });

  if (error) {
    console.error("Erreur Supabase :", error);
    throw error;
  }

  console.log(`${rows.length} entrées sauvegardées dans Supabase.`);

  // Email uniquement si de vraies nouvelles entreprises
  if (vraimentNouveaux.length === 0) {
    console.log("Pas de nouvelle entreprise depuis la dernière vérification.");
    return { statusCode: 200 };
  }

  // Envoi email
  const resend = new Resend(process.env.RESEND_API_KEY);
  const date = new Date().toLocaleDateString("fr-FR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  await resend.emails.send({
    from: "Veille Immatriculations <onboarding@resend.dev>",
    to: process.env.ALERT_EMAIL,
    subject: `🏪 ${vraimentNouveaux.length} nouvelle(s) immatriculation(s) détectée(s) — ${date}`,
    html: construireEmailHtml(vraimentNouveaux, depuis),
  });

  console.log(`Email envoyé à ${process.env.ALERT_EMAIL}.`);
  return { statusCode: 200 };
}

export const handler = schedule("0 8 * * *", run);

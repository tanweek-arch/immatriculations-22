-- CRM de prospection make-it-web
-- Colle ce SQL dans l'éditeur SQL de ton projet Supabase (onglet "SQL Editor")

CREATE TABLE prospects (
  id              BIGSERIAL PRIMARY KEY,
  nom             TEXT NOT NULL,
  ville           TEXT NOT NULL DEFAULT '',
  adresse         TEXT,
  categorie       TEXT,
  telephone       TEXT,
  email           TEXT,
  site_web        TEXT,
  type_site       TEXT,            -- 'vrai site' | 'plateforme' | 'aucun'
  note            NUMERIC(2,1),
  avis            INTEGER,
  statut          TEXT NOT NULL DEFAULT 'a_contacter'
                  CHECK (statut IN ('a_contacter', 'mail_envoye', 'relance', 'repondu', 'rdv', 'client', 'refus', 'email_invalide')),
  dernier_contact DATE,
  notes           TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (nom, ville)              -- clé d'upsert pour les imports CSV
);

CREATE INDEX idx_prospects_statut    ON prospects (statut);
CREATE INDEX idx_prospects_type_site ON prospects (type_site);
CREATE INDEX idx_prospects_ville     ON prospects (ville);

ALTER TABLE prospects ENABLE ROW LEVEL SECURITY;

-- Lecture publique (même logique que la table immatriculations)
CREATE POLICY "Lecture publique"
  ON prospects
  FOR SELECT
  USING (true);

-- Mise à jour publique : nécessaire pour changer les statuts depuis le
-- dashboard (clé anon). ATTENTION : toute personne qui connaît l'URL du
-- dashboard peut modifier les statuts. Acceptable pour un usage interne,
-- à durcir si la donnée devient sensible.
CREATE POLICY "Mise a jour publique"
  ON prospects
  FOR UPDATE
  USING (true)
  WITH CHECK (true);

-- Droits d'accès : ce projet Supabase ne donne pas les privilèges par
-- défaut aux rôles API, il faut les accorder explicitement.
GRANT SELECT, INSERT, UPDATE ON public.prospects TO service_role;
GRANT SELECT, UPDATE ON public.prospects TO anon;
GRANT USAGE, SELECT ON SEQUENCE public.prospects_id_seq TO service_role;

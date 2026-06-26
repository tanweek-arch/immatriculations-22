-- Colle ce SQL dans l'éditeur SQL de ton projet Supabase (onglet "SQL Editor")

-- Table principale
CREATE TABLE immatriculations (
  id          BIGSERIAL PRIMARY KEY,
  siren       TEXT UNIQUE NOT NULL,
  raison_sociale TEXT,
  dirigeant   TEXT,
  adresse     TEXT,
  code_postal TEXT,
  commune     TEXT,
  date_creation DATE,
  code_ape    TEXT,
  activite    TEXT,
  detected_at TIMESTAMPTZ DEFAULT NOW()
);

-- Colonnes Google Business
ALTER TABLE immatriculations
  ADD COLUMN IF NOT EXISTS google_business     BOOLEAN,
  ADD COLUMN IF NOT EXISTS google_place_id     TEXT,
  ADD COLUMN IF NOT EXISTS google_rating       NUMERIC(2,1),
  ADD COLUMN IF NOT EXISTS google_reviews      INTEGER,
  ADD COLUMN IF NOT EXISTS phone               TEXT,
  ADD COLUMN IF NOT EXISTS website             TEXT,
  ADD COLUMN IF NOT EXISTS google_checked_at   TIMESTAMPTZ;

-- Index pour les tris et filtres fréquents
CREATE INDEX idx_detected_at ON immatriculations (detected_at DESC);
CREATE INDEX idx_code_ape    ON immatriculations (code_ape);
CREATE INDEX idx_commune     ON immatriculations (commune);

-- Sécurité : lecture publique (les données sont publiques de toute façon)
ALTER TABLE immatriculations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Lecture publique"
  ON immatriculations
  FOR SELECT
  USING (true);

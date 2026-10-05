-- TaskFlow leads — SQLite schema v1
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS leads (
  id           INTEGER PRIMARY KEY,           -- surrogate: barato de indexar, no se expone
  email        TEXT    NOT NULL COLLATE NOCASE,
  email_key    TEXT    NOT NULL,             -- email trim+lower: clave de dedupe estable
  source       TEXT    NOT NULL DEFAULT 'landing_cta',
  utm_source   TEXT,
  utm_medium   TEXT,
  utm_campaign TEXT,
  user_agent   TEXT,
  ip_hash      TEXT,                        -- SHA256(salt+ip), nunca la IP cruda
  status       TEXT    NOT NULL DEFAULT 'new'
                       CHECK (status IN ('new','contacted','qualified','converted','spam')),
  created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  contacted_at TEXT,
  notes        TEXT
);

-- Una sola fila por email: el reenvío del form actualiza, no duplica
CREATE UNIQUE INDEX IF NOT EXISTS ux_leads_email_key ON leads(email_key);

-- Índices para las consultas que realmente vamos a hacer
CREATE INDEX IF NOT EXISTS ix_leads_status_created ON leads(status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_leads_campaign        ON leads(utm_campaign, created_at DESC);

CREATE TABLE IF NOT EXISTS lead_events (
  id         INTEGER PRIMARY KEY,
  lead_id    INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
  event      TEXT    NOT NULL,               -- 'created','resubmit','flagged_honeypot','rate_limited'
  detail     TEXT,
  created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_lead_events_lead ON lead_events(lead_id, created_at DESC);

-- Rate limit por IP+endpoint, ventana fija de 1 hora
CREATE TABLE IF NOT EXISTS rate_limits (
  bucket      TEXT    NOT NULL,               -- p.ej. 'lead:203.0.113.9'
  window_from TEXT    NOT NULL,               -- ISO-8601, truncado a la hora
  hits        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket, window_from)
) WITHOUT ROWID;

-- Honeypot: IPs que yatocaron el campo trampa, para no volver a procesarlas
CREATE TABLE IF NOT EXISTS blocked_ips (
  ip_hash    TEXT PRIMARY KEY,
  reason     TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
) WITHOUT ROWID;

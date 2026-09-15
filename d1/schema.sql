-- Esquema D1 para EMA Saladillo (Cloudflare D1 / SQLite)
-- Reemplaza a las 4 tablas de Supabase (mediciones_ema/cfr/dc/cs), unificadas.

CREATE TABLE IF NOT EXISTS mediciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    estacion TEXT NOT NULL,        -- 'EMA-EET' | 'EMA-CFR' | 'EMA-DC' | 'EMA-CS' | 'EMA-25C'
    codigo INTEGER,                -- código de parámetro SNIH (solo EMA-EET)
    parametro TEXT NOT NULL,       -- nombre del parámetro, ej. "Temperatura"
    unidad TEXT,
    valor REAL,
    valor_texto TEXT,              -- texto crudo tal como lo reporta la fuente (CFR y DC)
    fecha_hora_utc TEXT NOT NULL,  -- 'YYYY-MM-DD HH:MM:SS' en UTC, ordenable como texto
    insertado_en TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mediciones_estacion_fecha ON mediciones (estacion, fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_mediciones_fecha ON mediciones (fecha_hora_utc);
CREATE INDEX IF NOT EXISTS idx_mediciones_parametro ON mediciones (estacion, parametro, fecha_hora_utc);

-- Único: evita filas duplicadas cuando los scrapers reintentan o se
-- superponen (cron + corrida manual). Ver tools/d1_writer.py (INSERT OR IGNORE).
CREATE UNIQUE INDEX IF NOT EXISTS idx_mediciones_dedup ON mediciones (estacion, parametro, fecha_hora_utc);

-- Última lectura de cada parámetro por estación (para el panel de "estado actual")
CREATE VIEW IF NOT EXISTS v_ultima_lectura AS
SELECT m.*
FROM mediciones m
INNER JOIN (
    SELECT estacion, parametro, MAX(fecha_hora_utc) AS max_ts
    FROM mediciones
    GROUP BY estacion, parametro
) u ON m.estacion = u.estacion AND m.parametro = u.parametro AND m.fecha_hora_utc = u.max_ts;

"""
d1_writer.py
============
Helper compartido por los 4 scrapers de EMA Saladillo: inserta las
mediciones en la tabla unificada `mediciones` de Cloudflare D1, vía la
API HTTP de Cloudflare (mismo patrón que usa
purpleair-saladillo/ingest_purpleair.py para calidad del aire).

Reemplaza a guardar_en_supabase(): antes cada scraper escribía en su
propia tabla de Supabase (mediciones_ema/cfr/dc/cs); ahora todos escriben
acá, en D1, que es lo que sirve emas.lemeit.ar a través del Worker
ema-saladillo-api.

Variables de entorno requeridas (se cargan como secrets en GitHub Actions):
    CF_ACCOUNT_ID   -> Account ID de Cloudflare
    CF_DATABASE_ID  -> database_id de ema-saladillo-db
                       (b5b1eef7-5c8d-42a8-a23e-69cd5ae1cd30)
    CF_API_TOKEN    -> API token de Cloudflare con permiso D1:Edit

Requiere el índice único idx_mediciones_dedup sobre
(estacion, parametro, fecha_hora_utc) — ver d1/schema.sql — para que
"INSERT OR IGNORE" no duplique filas si un scraper se reintenta o si el
cron se superpone con una corrida manual.
"""

import os
import requests
from datetime import datetime, timezone, timedelta

# .strip() por las dudas: es común que un secret de GitHub Actions quede
# con un salto de línea de sobra al pegarlo (rompe la URL de la API con un
# "%0A" invisible y Cloudflare responde 403 Forbidden sin más explicación).
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_DATABASE_ID = os.environ.get("CF_DATABASE_ID", "").strip()
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()

D1_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/d1/database/{CF_DATABASE_ID}/query"
)

TZ_AR = timezone(timedelta(hours=-3))

INSERT_SQL = """
    INSERT OR IGNORE INTO mediciones
        (estacion, codigo, parametro, unidad, valor, valor_texto, fecha_hora_utc)
    VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def _fecha_ar_a_utc(fecha_ar):
    """'DD/MM/YYYY HH:MM' o 'DD/MM/YY HH:MM' (hora Argentina, UTC-3 fijo) ->
    'YYYY-MM-DD HH:MM:SS' (UTC).

    Se prueban los dos formatos de año (4 y 2 dígitos) porque al menos una
    fuente (la página del CFR) cambió en algún momento de 4 a 2 dígitos sin
    aviso, y antes esto tiraba ValueError silenciosamente descartado fila
    por fila en guardar_en_d1() -- casi un mes entero de EMA-CFR se perdió
    así, sin ningún error visible. Si en el futuro aparece un tercer
    formato, guardar_en_d1() ahora sí lo va a hacer notar (ver más abajo).
    """
    texto = fecha_ar.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%y %H:%M"):
        try:
            dt_local = datetime.strptime(texto, fmt).replace(tzinfo=TZ_AR)
            return dt_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"formato de fecha no reconocido: {fecha_ar!r}")


def _normalizar_fecha_utc(valor):
    """Acepta 'YYYY-MM-DDTHH:MM:SSZ' o 'YYYY-MM-DD HH:MM:SS' y devuelve el segundo formato."""
    return valor.strip().replace("T", " ").rstrip("Z")


def _d1_query(sql, params_list):
    if not (CF_ACCOUNT_ID and CF_DATABASE_ID and CF_API_TOKEN):
        raise RuntimeError(
            "Faltan variables CF_ACCOUNT_ID / CF_DATABASE_ID / CF_API_TOKEN (D1)"
        )
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        D1_URL, headers=headers, json={"sql": sql, "params": params_list}, timeout=30
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("success"):
        raise RuntimeError(f"D1 query failed: {result}")
    return result


def guardar_en_d1(estacion_d1, filas):
    """
    Inserta `filas` en la tabla unificada `mediciones` de D1.

    estacion_d1: código canónico de la estación en D1
                 ('EMA-EET' | 'EMA-CFR' | 'EMA-DC' | 'EMA-CS')
    filas: lista de dicts, cada uno con:
        codigo        (int, opcional — solo EMA-EET)
        parametro     (str, requerido)
        unidad        (str, opcional)
        valor         (float, requerido — filas sin valor se descartan)
        valor_texto   (str, opcional)
        fecha_hora_utc (str) o fecha_hora_ar (str 'DD/MM/YYYY HH:MM') — se
                        requiere al menos uno de los dos.

    Devuelve la cantidad de filas enviadas a D1 (no necesariamente
    insertadas: INSERT OR IGNORE descarta duplicados en silencio).

    Si hay filas con un valor válido para guardar pero NINGUNA termina
    insertándose (por ejemplo: todas con una fecha en un formato que
    _fecha_ar_a_utc() no reconoce), se levanta RuntimeError en vez de
    devolver 0 calladamente -- así lo agarra el try/except que ya tiene
    cada scraper alrededor de este llamado, y sale como un fallo visible
    (exit code != 0) en vez de perderse. Así se rompió EMA-CFR sin que
    ninguna corrida de GitHub Actions se viera en rojo: el sitio cambió el
    formato de la fecha, cada fila se descartaba en el continue de abajo, y
    guardar_en_d1() devolvía 0 como si simplemente no hubiera habido nada
    que mandar.
    """
    if not filas:
        return 0

    elegibles = 0
    fecha_invalida = 0
    enviados = 0
    for f in filas:
        if f.get("valor") is None:
            continue
        elegibles += 1

        fecha_utc = f.get("fecha_hora_utc")
        if fecha_utc:
            fecha_utc = _normalizar_fecha_utc(fecha_utc)
        else:
            fecha_ar = f.get("fecha_hora_ar")
            if not fecha_ar or fecha_ar == "—":
                continue
            try:
                fecha_utc = _fecha_ar_a_utc(fecha_ar)
            except ValueError as e:
                fecha_invalida += 1
                print(f"  ⚠  D1: fecha descartada ({e})")
                continue

        params = [
            estacion_d1,
            f.get("codigo"),
            f["parametro"],
            f.get("unidad"),
            float(f["valor"]),
            f.get("valor_texto"),
            fecha_utc,
        ]
        _d1_query(INSERT_SQL, params)
        enviados += 1

    if elegibles > 0 and enviados == 0:
        detalle = f", {fecha_invalida} por fecha con formato inesperado" if fecha_invalida else ""
        raise RuntimeError(
            f"0 de {elegibles} fila(s) con valor válido se guardaron en D1{detalle} "
            f"-- revisar la fuente de la estación {estacion_d1}"
        )

    return enviados

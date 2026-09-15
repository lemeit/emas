"""
subir_a_wu.py
==============
"Activador" para la estación que registraste en Weather Underground con
datos ficticios de ubicación (lat/lon/altura) pero sin hardware propio
subiendo datos — por eso figura offline y WU no te deja generar la API
key de LECTURA todavía (esa requiere una estación activa de verdad).

Este script toma una lectura real de uno de TUS sensores ya en producción
(por defecto, un PurpleAir con temperatura/humedad/presión — Madre Teresa
High School, sensor 198617) desde la API pública de purpleair-saladillo,
y la sube a WU con el protocolo clásico de estaciones personales ("PWS
Upload Protocol"). Con datos reales entrando, la estación debería dejar
de figurar offline y destrabarte la API key de lectura en tu perfil.

Funciona con cualquier sensor que reporte temperatura + humedad (presión
es opcional) — no hace falta que sea el de Madre Teresa, se puede apuntar
a cualquier otro con --sensor <sensor_index>, o dejar que el script elija
automáticamente el primero disponible que tenga los 3 campos.

Uso:
    $env:WU_STATION_ID  = "el ID que generaste al registrar la estación"
    $env:WU_STATION_KEY = "la key/password que te dio junto al ID"
    python subir_a_wu.py                       # sube la lectura más reciente, una vez
    python subir_a_wu.py --sensor 198617        # fuerza un sensor puntual
    python subir_a_wu.py --loop 900             # repite cada 15 min (recomendado
                                                  # para "mantener viva" la estación)
    python subir_a_wu.py --dry-run              # solo muestra qué se subiría, sin subirlo

No hace falta que ID/KEY sean secrets de GitHub todavía — este script es
para correrlo desde tu PC mientras confirmás que funciona. Si más adelante
lo automatizamos (GitHub Actions), esas dos variables pasan a secrets del
repo, igual que WU_API_KEY.

Dependencias:
    pip install requests
"""

import os
import sys
import time
import argparse
import getpass
from datetime import datetime, timezone

import requests

API_ULTIMAS = "https://purpleair-saladillo-api.fisicai-eureka-01.workers.dev/api/ultimas"
WU_UPLOAD_URL = "https://weatherstation.wunderground.com/weatherstation/updateweatherstation.php"

# Sensor por defecto: PurpleAir "Madre Teresa High School" — tiene
# temperatura, humedad Y presión (varios AirGradient de este proyecto no
# reportan presión, así que no sirven tan bien para "activar" la estación).
SENSOR_DEFAULT = 198617

# Una lectura más vieja que esto no se sube — no tiene sentido reportarle
# a WU como "actual" un dato de hace horas, y WU puede rechazarlo igual.
MAX_ANTIGUEDAD_MIN = 30


def c_a_f(temp_c):
    return temp_c * 9 / 5 + 32


def hpa_a_inhg(hpa):
    return hpa * 0.0295299830714


def punto_rocio_f(temp_c, humedad_pct):
    """Aproximación de Magnus — suficiente para este uso (no es una medición real)."""
    import math
    a, b = 17.62, 243.12
    gamma = (a * temp_c) / (b + temp_c) + math.log(max(humedad_pct, 1) / 100.0)
    dewpt_c = (b * gamma) / (a - gamma)
    return c_a_f(dewpt_c)


def obtener_lectura(sensor_index=None):
    resp = requests.get(API_ULTIMAS, timeout=15)
    resp.raise_for_status()
    datos = resp.json()

    candidatos = datos
    if sensor_index is not None:
        candidatos = [d for d in datos if d.get("sensor_index") == sensor_index]
        if not candidatos:
            raise RuntimeError(f"No encontré el sensor_index {sensor_index} en /api/ultimas")
    else:
        # Auto: el primero que tenga temperatura Y humedad (presión es un plus)
        candidatos = [d for d in datos if d.get("temperatura") is not None and d.get("humedad") is not None]
        candidatos.sort(key=lambda d: d.get("presion") is None)  # con presión primero
        if not candidatos:
            raise RuntimeError("Ningún sensor de /api/ultimas tiene temperatura+humedad ahora mismo")

    return candidatos[0]


def verificar_antiguedad(lectura):
    ts = lectura.get("timestamp")
    if not ts:
        return None
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    edad_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    return edad_min


def armar_params(lectura, station_id, station_key):
    params = {
        "ID": station_id,
        "PASSWORD": station_key,
        "dateutc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "action": "updateraw",
        "softwaretype": "lemeit-ema-saladillo",
        "tempf": round(c_a_f(lectura["temperatura"]), 1),
        "humidity": round(lectura["humedad"]),
    }
    if lectura.get("presion") is not None:
        params["baromin"] = round(hpa_a_inhg(lectura["presion"]), 2)
    try:
        params["dewptf"] = round(punto_rocio_f(lectura["temperatura"], lectura["humedad"]), 1)
    except Exception:
        pass
    return params


def subir_a_wu(params):
    resp = requests.get(WU_UPLOAD_URL, params=params, timeout=15)
    return resp.status_code, resp.text.strip()


def pedir_credenciales():
    station_id = os.environ.get("WU_STATION_ID", "").strip()
    station_key = os.environ.get("WU_STATION_KEY", "").strip()
    if not station_id:
        station_id = input("Station ID (el que te dio WU al registrar la estación): ").strip()
    if not station_key:
        station_key = getpass.getpass("Station Key/Password: ").strip()
    return station_id, station_key


def ciclo(args, station_id, station_key):
    try:
        lectura = obtener_lectura(args.sensor)
    except Exception as e:
        print(f"  ✖ No pude leer /api/ultimas: {e}")
        return

    edad_min = verificar_antiguedad(lectura)
    if edad_min is not None and edad_min > MAX_ANTIGUEDAD_MIN:
        print(f"  ⚠  Lectura de '{lectura.get('nombre')}' tiene {edad_min:.0f} min — se salta esta corrida (máximo {MAX_ANTIGUEDAD_MIN} min)")
        return

    params = armar_params(lectura, station_id, station_key)

    print()
    print(f"  Sensor origen: {lectura.get('nombre')} (#{lectura.get('sensor_index')}, {lectura.get('proveedor')})")
    print(f"  Temp: {lectura['temperatura']}°C -> {params['tempf']}°F   "
          f"Humedad: {lectura['humedad']}%"
          + (f"   Presion: {lectura['presion']} hPa -> {params['baromin']} inHg" if "baromin" in params else "  (sin presion)"))

    if args.dry_run:
        safe = {k: v for k, v in params.items() if k != "PASSWORD"}
        print(f"  [dry-run] Se subiría: {safe}")
        return

    status, texto = subir_a_wu(params)
    if status == 200 and texto.lower().startswith("success"):
        print(f"  ✔  WU respondió: {texto}")
    else:
        print(f"  ✖  WU respondió (status {status}): {texto}")


def main():
    parser = argparse.ArgumentParser(description="Sube una lectura real de la red EMA/aire a Weather Underground para activar la estación")
    parser.add_argument("--sensor", type=int, default=None,
                        help=f"sensor_index puntual a usar (default: auto, prioriza {SENSOR_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true", help="No sube nada, solo muestra qué haría")
    parser.add_argument("--loop", type=int, default=0, help="Repetir cada N segundos (ej: --loop 900)")
    args = parser.parse_args()

    if args.sensor is None:
        args.sensor = SENSOR_DEFAULT

    station_id, station_key = pedir_credenciales()
    if not station_id or not station_key:
        print("Faltan credenciales (WU_STATION_ID / WU_STATION_KEY).")
        sys.exit(1)

    if args.loop > 0:
        print(f"  Modo automático — cada {args.loop // 60} min. Ctrl+C para detener.\n")
        while True:
            ciclo(args, station_id, station_key)
            time.sleep(args.loop)
    else:
        ciclo(args, station_id, station_key)


if __name__ == "__main__":
    main()

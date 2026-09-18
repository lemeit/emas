"""
wu_25demayo.py
===============
Obtiene datos de la estación "25Clima-Centro" (25 de Mayo, Buenos Aires,
https://25clima.ar/) vía la API pública v2 de Weather Underground (PWS),
usando el station ID con el que está registrada ahí: IDEMAY14.

A diferencia de los otros 4 scrapers de este repo (que consultan cada
fuente propia por HTML/OCR/JSON), esta estación no es propia del proyecto
ni tiene endpoint propio — es de un tercero (N-TecLab / SS Desarrollos) y
sus datos ya viven en la red de Weather Underground, de donde cualquiera
con su propia API key los puede consultar (no hace falta ser dueño de la
estación ni pedirle la key al operador). Ver wiki para más contexto y la
nota sobre contactar al operador antes de darle uso público permanente.

Ubicación: -35.435069, -60.170656 — 25 de Mayo, Buenos Aires (según el
propio sitio 25clima.ar; el dashboard de Weather Underground redondea a
-35.44, -60.17). Muy cerca de la Esc. Ed. Artística N°1 "Lola Mora"
(-35.43668, -60.16899), ya en la red de aq.lemeit.ar — buena señal para
sumarla al mapa conjunto. Elevación ~16 m.

Parámetros disponibles (API "current conditions", instantáneos — no son
promedios/máximas del día como en cs_saladillo.py):
  temp        Temperatura actual         [°C]
  heatIndex / windChill   Sensación térmica  [°C] (según corresponda)
  dewpt       Punto de rocío             [°C]
  humidity    Humedad                    [%]
  pressure    Presión                    [hPa]
  windSpeed   Velocidad viento           [km/h]
  windGust    Ráfaga                     [km/h]
  winddir     Dirección viento           [°]
  precipTotal Lluvia acumulada del día   [mm]
  solarRadiation  Radiación solar        [W/m²] (si la estación lo reporta)

Uso:
    python wu_25demayo.py              # consulta + guarda en D1
    python wu_25demayo.py --csv        # también exporta CSV
    python wu_25demayo.py --nod1       # solo consola
    python wu_25demayo.py --json       # muestra el JSON crudo de WU
    python wu_25demayo.py --loop 3600  # repite cada 1 hora

Variables de entorno requeridas:
    WU_API_KEY      API key propia de Weather Underground (ver instrucciones
                     en la wiki — no hace falta estación física real para
                     generarla). NO es la key embebida en 25clima.ar.
    CF_ACCOUNT_ID / CF_DATABASE_ID / CF_API_TOKEN   (ya configuradas para
    los otros 4 scrapers — ver d1_writer.py)

Dependencias:
    pip install requests

Proyecto: Integración EMA Saladillo — EEST N°1 "Gral. Savio"
"""

import os
import sys
import requests
import json
import csv
import argparse
import time
from datetime import datetime, timezone, timedelta

from d1_writer import guardar_en_d1

# ─── Configuración ─────────────────────────────────────────────────────────────
STATION_ID  = "IDEMAY14"          # station ID en Weather Underground (estación "25Clima-Centro")
ESTACION    = "25Clima (25 de Mayo)"
ESTACION_D1 = "EMA-25C"
TIMEOUT     = 15

WU_API_KEY = os.environ.get("WU_API_KEY", "").strip()

URL_ACTUAL = (
    "https://api.weather.com/v2/pws/observations/current"
    f"?stationId={STATION_ID}&format=json&units=m&numericPrecision=decimal&apiKey={{key}}"
)

TZ_AR = timezone(timedelta(hours=-3))


# ─── Helpers ───────────────────────────────────────────────────────────────────
def ahora_ar():
    return datetime.now(tz=TZ_AR).strftime("%d/%m/%Y %H:%M")


def primer_valor(*valores):
    for v in valores:
        if v is not None:
            return v
    return None


# ─── Obtener datos ─────────────────────────────────────────────────────────────
def obtener_datos():
    if not WU_API_KEY:
        raise RuntimeError(
            "Falta WU_API_KEY (API key propia de Weather Underground) — "
            "ver la wiki para cómo generarla, no es la key de 25clima.ar."
        )

    resp = requests.get(URL_ACTUAL.format(key=WU_API_KEY), timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    obs_list = data.get("observations") or []
    if not obs_list:
        # La estación puede estar offline momentáneamente — no es un error
        # del scraper, simplemente no hay nada nuevo que guardar esta corrida.
        return [], data

    obs = obs_list[0]
    metric = obs.get("metric") or {}
    timestamp = ahora_ar()

    campos = {
        "Temperatura":          ("°C",   metric.get("temp")),
        "Sensacion Termica":    ("°C",   primer_valor(metric.get("heatIndex"), metric.get("windChill"), metric.get("temp"))),
        "Punto de Rocio":       ("°C",   metric.get("dewpt")),
        "Humedad":              ("%",    obs.get("humidity")),
        "Presion":              ("hPa",  metric.get("pressure")),
        "Velocidad Viento":     ("km/h", metric.get("windSpeed")),
        "Rafaga":               ("km/h", metric.get("windGust")),
        "Direccion del Viento": ("°",    obs.get("winddir")),
        "Lluvia Diaria":        ("mm",   metric.get("precipTotal")),
        "Radiacion Solar":      ("W/m2", obs.get("solarRadiation")),
    }

    resultados = []
    for parametro, (unidad, valor) in campos.items():
        if valor is not None:
            resultados.append({
                "estacion":      ESTACION,
                "parametro":     parametro,
                "unidad":        unidad,
                "valor":         float(valor),
                "fecha_hora_ar": timestamp,
            })

    return resultados, data


# ─── D1 (Cloudflare) ────────────────────────────────────────────────────────────
def guardar_en_d1_desde_wu(datos):
    """Inserta los datos en la tabla unificada `mediciones` (estación EMA-25C)."""
    return guardar_en_d1(ESTACION_D1, datos)


# ─── Consola ───────────────────────────────────────────────────────────────────
def mostrar_consola(datos, enviados=None, offline=False):
    ahora = ahora_ar()
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   25Clima — 25 de Mayo (IDEMAY14 / Weather Underground) ")
    print(f"║   Consulta: {ahora}                            ║")
    print("╠══════════════════════════════════════════════════════╣")
    if offline:
        print("║  ⚠  Estación sin lecturas nuevas (offline por ahora)  ║")
    for d in datos:
        val = f"{d['valor']:.1f} {d['unidad']}"
        print(f"║  {d['parametro']:<22} {val:<14}  {d['fecha_hora_ar']}  ║")
    print("╠══════════════════════════════════════════════════════╣")
    if enviados is not None:
        print(f"║  D1: {enviados} filas enviadas ✔                              ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()


# ─── CSV ───────────────────────────────────────────────────────────────────────
def exportar_csv(datos, archivo="wu_25demayo_actuales.csv"):
    with open(archivo, "w", newline="", encoding="utf-8-sig") as f:
        campos = ["estacion", "parametro", "unidad", "valor", "fecha_hora_ar"]
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(datos)
    print(f"  ✔  CSV → {archivo}")


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="25Clima (25 de Mayo) → Cloudflare D1")
    parser.add_argument("--csv",  action="store_true", help="Exportar CSV")
    parser.add_argument("--json", action="store_true", help="Mostrar JSON crudo")
    parser.add_argument("--nod1", action="store_true", help="No guardar en D1")
    parser.add_argument("--loop", type=int, default=0,
                        help="Repetir cada N segundos (ej: --loop 3600)")
    args = parser.parse_args()

    def ciclo():
        """Devuelve True si el ciclo salió bien, False si algo falló --
        para que main() pueda terminar con código de salida != 0 y una
        corrida de GitHub Actions se vea roja de verdad, en vez de quedar
        en verde con un error impreso que nadie llega a leer (ver bitácora
        / auditoría horaria). "Sin datos" (datos=[]) NO cuenta como falla
        acá a propósito: EMA-25C es una estación de terceros (25Clima, no
        propia del proyecto) que puede estar momentáneamente sin reportar
        en Weather Underground sin que sea un error de nuestro lado."""
        ok = True
        try:
            datos, raw = obtener_datos()

            if args.json:
                print(json.dumps(raw, indent=2, ensure_ascii=False))
                return True

            enviados = None
            if not args.nod1 and datos:
                try:
                    enviados = guardar_en_d1_desde_wu(datos)
                except Exception as e:
                    print(f"  ⚠  D1: {e}")
                    ok = False

            mostrar_consola(datos, enviados, offline=not datos)

            if args.csv and datos:
                exportar_csv(datos)

            return ok

        except requests.HTTPError as e:
            print(f"\n  ✖ Error HTTP: {e}")
        except requests.ConnectionError:
            print("\n  ✖ Sin conexión a api.weather.com")
        except Exception as e:
            print(f"\n  ✖ Error inesperado: {e}")
        return False

    if args.loop > 0:
        print(f"  Modo automático — cada {args.loop//60} min. Ctrl+C para detener.\n")
        while True:
            ciclo()
            time.sleep(args.loop)
    else:
        if not ciclo():
            sys.exit(1)


if __name__ == "__main__":
    main()

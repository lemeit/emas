"""
cs_saladillo.py
===============
Obtiene datos de climasaladillo.com (Estación Meteorológica Saladillo)
via endpoint JSON público de Meteotemplate.

URL: https://climasaladillo.com/template/homepage/blocks/stationData/stationDataAjax.php?period=today
Ubicación estimada: -35.645, -59.7758 (barrio Falucho, Saladillo)
Equipo: Davis Instruments / Meteotemplate

Parámetros disponibles:
  avgT   Temperatura actual        [°C]
  avgH   Humedad actual            [%]
  avgP   Presión actual            [hPa]
  avgW   Velocidad viento          [km/h]
  avgG   Ráfaga                    [km/h]
  avgD   Punto de rocío            [°C]
  avgA   Sensación térmica         [°C]
  totalR Lluvia del día            [mm]
  avgS   Radiación solar           [W/m²]

Uso:
    python cs_saladillo.py              # consulta + guarda en D1
    python cs_saladillo.py --csv        # también exporta CSV
    python cs_saladillo.py --nod1       # solo consola
    python cs_saladillo.py --loop 3600  # repite cada 1 hora

Dependencias:
    pip install requests

Proyecto: Integración EMA Saladillo — EEST N°1 "Gral. Savio"
"""

import os
import sys
import requests
import json
import csv
import re
import argparse
import time
import warnings
from datetime import datetime, timezone, timedelta
from urllib3.exceptions import InsecureRequestWarning

from d1_writer import guardar_en_d1

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

# ─── Configuración ─────────────────────────────────────────────────────────────
URL_JSON    = "https://climasaladillo.com/template/homepage/blocks/stationData/stationDataAjax.php?period=today"
ESTACION    = "ClimaSaladillo"
ESTACION_D1 = "EMA-CS"
TIMEOUT     = 15

HEADERS = {
    "User-Agent":  "Mozilla/5.0 (compatible; EEST-Saladillo-script/1.0)",
    "Referer":     "https://climasaladillo.com/template/indexDesktop.php",
    "Accept":      "application/json, text/javascript, */*",
}

TZ_AR = timezone(timedelta(hours=-3))

# ─── Mapa de campos JSON → parámetros ─────────────────────────────────────────
# Meteotemplate devuelve strings con unidad incluida: "13.3 °C"
# Usamos avgX para el valor actual del día
CAMPOS = {
    "avgT": {"parametro": "Temperatura",       "unidad": "°C"},
    "avgH": {"parametro": "Humedad",            "unidad": "%"},
    "avgP": {"parametro": "Presion",            "unidad": "hPa"},
    "avgW": {"parametro": "Velocidad Viento",   "unidad": "km/h"},
    "avgG": {"parametro": "Rafaga",             "unidad": "km/h"},
    "avgD": {"parametro": "Punto de Rocio",     "unidad": "°C"},
    "avgA": {"parametro": "Sensacion Termica",  "unidad": "°C"},
    "totalR": {"parametro": "Lluvia Diaria",    "unidad": "mm"},
    "avgS": {"parametro": "Radiacion Solar",    "unidad": "W/m2"},
    "maxT": {"parametro": "Temperatura Maxima", "unidad": "°C"},
    "minT": {"parametro": "Temperatura Minima", "unidad": "°C"},
}


# ─── Helpers ───────────────────────────────────────────────────────────────────
def extraer_numero(texto):
    """Extrae el número de un string como '13.3 °C' o '0 W/m<sup>2</sup>'."""
    if texto is None:
        return None
    # Remover tags HTML
    texto = re.sub(r'<[^>]+>', '', str(texto))
    m = re.search(r'-?\d+\.?\d*', texto)
    if m:
        return float(m.group())
    return None

def ahora_ar():
    return datetime.now(tz=TZ_AR).strftime("%d/%m/%Y %H:%M")


# ─── Obtener datos ─────────────────────────────────────────────────────────────
def obtener_datos():
    resp = requests.get(URL_JSON, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    timestamp = ahora_ar()
    resultados = []

    for campo, meta in CAMPOS.items():
        valor_str = data.get(campo)
        valor_num = extraer_numero(valor_str)
        if valor_num is not None:
            resultados.append({
                "estacion":      ESTACION,
                "parametro":     meta["parametro"],
                "unidad":        meta["unidad"],
                "valor":         valor_num,
                "fecha_hora_ar": timestamp,
            })

    return resultados, data


# ─── D1 (Cloudflare) ────────────────────────────────────────────────────────────
def guardar_en_d1_desde_cs(datos):
    """Inserta los datos en la tabla unificada `mediciones` (estación EMA-CS)."""
    return guardar_en_d1(ESTACION_D1, datos)


# ─── Consola ───────────────────────────────────────────────────────────────────
def mostrar_consola(datos, enviados=None):
    ahora = ahora_ar()
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   Clima Saladillo — Barrio Falucho                   ║")
    print(f"║   Consulta: {ahora}                            ║")
    print("║   Davis Instruments · climasaladillo.com             ║")
    print("╠══════════════════════════════════════════════════════╣")
    for d in datos:
        val = f"{d['valor']:.1f} {d['unidad']}"
        print(f"║  {d['parametro']:<28} {val:<14}  {d['fecha_hora_ar']}  ║")
    print("╠══════════════════════════════════════════════════════╣")
    if enviados is not None:
        print(f"║  D1: {enviados} filas enviadas ✔                              ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()


# ─── CSV ───────────────────────────────────────────────────────────────────────
def exportar_csv(datos, archivo="cs_actuales.csv"):
    with open(archivo, "w", newline="", encoding="utf-8-sig") as f:
        campos = ["estacion","parametro","unidad","valor","fecha_hora_ar"]
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(datos)
    print(f"  ✔  CSV → {archivo}")


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Clima Saladillo → Cloudflare D1")
    parser.add_argument("--csv",  action="store_true", help="Exportar CSV")
    parser.add_argument("--json", action="store_true", help="Mostrar JSON crudo")
    parser.add_argument("--nod1", action="store_true", help="No guardar en D1")
    parser.add_argument("--loop", type=int, default=0,
                        help="Repetir cada N segundos (ej: --loop 3600)")
    args = parser.parse_args()

    def ciclo():
        """Devuelve True si el ciclo salió bien (scrapeo + guardado en D1,
        cuando corresponde), False si algo falló -- para que main() pueda
        terminar con código de salida != 0 y una corrida de GitHub Actions
        se vea roja de verdad, en vez de quedar en verde con un error
        impreso que nadie llega a leer (ver bitácora / auditoría horaria)."""
        ok = True
        try:
            datos, raw = obtener_datos()

            if args.json:
                print(json.dumps(raw, indent=2, ensure_ascii=False))
                return True

            enviados = None
            if not args.nod1:
                try:
                    enviados = guardar_en_d1_desde_cs(datos)
                except Exception as e:
                    print(f"  ⚠  D1: {e}")
                    ok = False

            mostrar_consola(datos, enviados)

            if args.csv:
                exportar_csv(datos)

            return ok

        except requests.HTTPError as e:
            print(f"\n  ✖ Error HTTP: {e}")
        except requests.ConnectionError:
            print("\n  ✖ Sin conexión a climasaladillo.com")
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

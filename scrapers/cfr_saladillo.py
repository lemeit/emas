"""
cfr_saladillo.py
================
Scraper para la EMA del CFR Saladillo.
Extrae datos del HTML de https://ema.cfrsaladillo.edu.ar/
y los guarda en Cloudflare D1 (tabla unificada `mediciones`, estación EMA-CFR).

Parámetros extraídos:
  Temperatura actual      [°C]   celda 8
  Humedad actual          [%]    celda 29
  Punto de rocío          [°C]   celda 50
  Presión barométrica     [hPa]  celda 66
  Velocidad del viento    [km/h] celda 72
  Dirección del viento    [°]    celda 74
  Lluvia diaria           [mm]   celda 91
  Lluvia intensidad       [mm/h] celda 93
  Radiación solar         [W/m²] celda 113

Uso:
    python cfr_saladillo.py              # scraping + guarda en D1
    python cfr_saladillo.py --csv        # también exporta CSV local
    python cfr_saladillo.py --nod1       # solo consola, sin D1
    python cfr_saladillo.py --loop 3600  # repite cada 1 hora

Dependencias:
    pip install requests beautifulsoup4

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

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("  ✖ Falta BeautifulSoup4. Instalá con: pip install beautifulsoup4")
    exit(1)

from d1_writer import guardar_en_d1

# ─── Configuración ─────────────────────────────────────────────────────────────
URL_CFR     = "https://ema.cfrsaladillo.edu.ar/"
ESTACION    = "CFR-Saladillo"
ESTACION_D1 = "EMA-CFR"
TIMEOUT     = 15

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; EEST-Saladillo-script/1.0)",
    "Accept-Language": "es-AR,es;q=0.9",
}

# ─── Zona horaria Argentina ────────────────────────────────────────────────────
TZ_AR = timezone(timedelta(hours=-3))

# ─── Mapa de celdas → parámetros ──────────────────────────────────────────────
# Índice de celda <td> confirmado por inspección del HTML
MAPA_CELDAS = {
    8:   {"parametro": "Temperatura",          "unidad": "°C"},
    29:  {"parametro": "Humedad",               "unidad": "%"},
    50:  {"parametro": "Punto de Rocio",        "unidad": "°C"},
    66:  {"parametro": "Presion Barometrica",   "unidad": "hPa"},
    72:  {"parametro": "Velocidad del Viento",  "unidad": "km/h"},
    74:  {"parametro": "Direccion del Viento",  "unidad": "°"},
    91:  {"parametro": "Lluvia Diaria",         "unidad": "mm"},
    93:  {"parametro": "Intensidad Lluvia",     "unidad": "mm/h"},
    113: {"parametro": "Radiacion Solar",       "unidad": "W/m2"},
}

# ─── Helpers ───────────────────────────────────────────────────────────────────
def limpiar_valor(texto):
    """
    Extrae el número de un string como '12.1 °C', '94 %', 'S (185)'.
    Para dirección del viento devuelve los grados entre paréntesis.
    Devuelve (valor_numerico_o_None, texto_original_limpio).
    """
    texto = texto.strip()
    # Reemplazar caracteres mal encodados
    texto = texto.replace("°", "°").replace("Â°", "°").replace("â€°", "°")
    texto = " ".join(texto.split())  # normalizar espacios

    # Dirección del viento: "S (185)" → 185
    m = re.search(r'\((\d+\.?\d*)\)', texto)
    if m:
        return float(m.group(1)), texto

    # Número con posible decimal
    m = re.search(r'-?\d+\.?\d*', texto)
    if m:
        return float(m.group()), texto

    return None, texto


def ahora_ar():
    return datetime.now(tz=TZ_AR).strftime("%d/%m/%Y %H:%M")


# ─── Scraping ──────────────────────────────────────────────────────────────────
def obtener_datos_cfr():
    """
    Descarga el HTML del CFR y extrae los parámetros por índice de celda.
    Devuelve lista de dicts con parametro, unidad, valor, valor_texto, fecha_hora_ar.
    """
    resp = requests.get(URL_CFR, headers=HEADERS, timeout=TIMEOUT, verify=False)
    resp.raise_for_status()

    # El HTML usa windows-1252 — forzar encoding correcto
    resp.encoding = "windows-1252"
    html = resp.text

    soup = BeautifulSoup(html, "html.parser")
    celdas = soup.find_all("td")

    # Extraer fecha y hora de la página
    fecha_pag = ""
    hora_pag  = ""
    for i, td in enumerate(celdas):
        txt = td.get_text(strip=True)
        if "FECHA:" in txt:
            fecha_pag = txt.replace("FECHA:", "").strip()
        if "HORA:" in txt:
            hora_pag = txt.replace("HORA:", "").strip()

    fecha_hora = f"{fecha_pag} {hora_pag}".strip() or ahora_ar()

    resultados = []
    for idx, meta in MAPA_CELDAS.items():
        if idx >= len(celdas):
            continue
        texto_celda = celdas[idx].get_text(separator=" ", strip=True)
        valor_num, texto_limpio = limpiar_valor(texto_celda)

        resultados.append({
            "estacion":      ESTACION,
            "parametro":     meta["parametro"],
            "unidad":        meta["unidad"],
            "valor":         valor_num,
            "valor_texto":   texto_limpio,
            "fecha_hora_ar": fecha_hora,
        })

    return resultados


# ─── D1 (Cloudflare) ────────────────────────────────────────────────────────────
def guardar_en_d1_desde_cfr(datos):
    """Inserta los datos en la tabla unificada `mediciones` (estación EMA-CFR)."""
    filas = [d for d in datos if d["valor"] is not None]
    return guardar_en_d1(ESTACION_D1, filas)


# ─── Consola ───────────────────────────────────────────────────────────────────
def mostrar_consola(datos, enviados=None):
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   EMA CFR Saladillo                                  ║")
    print(f"║   Consulta: {ahora_ar()}                            ║")
    print("║   Davis Instruments · ema.cfrsaladillo.edu.ar        ║")
    print("╠══════════════════════════════════════════════════════╣")
    for d in datos:
        val = f"{d['valor']:.1f} {d['unidad']}" if d['valor'] is not None else "Sin dato"
        print(f"║  {d['parametro']:<28} {val:<14}  {d['fecha_hora_ar']}  ║")
    print("╠══════════════════════════════════════════════════════╣")
    if enviados is not None:
        print(f"║  D1: {enviados} filas enviadas ✔                              ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()


# ─── CSV ───────────────────────────────────────────────────────────────────────
def exportar_csv(datos, archivo="cfr_actuales.csv"):
    with open(archivo, "w", newline="", encoding="utf-8-sig") as f:
        campos = ["estacion","parametro","unidad","valor","valor_texto","fecha_hora_ar"]
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(datos)
    print(f"  ✔  CSV → {archivo}")


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="EMA CFR Saladillo → Cloudflare D1")
    parser.add_argument("--csv",  action="store_true", help="Exportar CSV")
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
            datos = obtener_datos_cfr()

            enviados = None
            if not args.nod1:
                try:
                    enviados = guardar_en_d1_desde_cfr(datos)
                except Exception as e:
                    print(f"  ⚠  D1: {e}")
                    ok = False

            mostrar_consola(datos, enviados)

            if args.csv:
                exportar_csv(datos)

            return ok

        except requests.HTTPError as e:
            print(f"\n  ✖ Error HTTP: {e}")
        except requests.ConnectionError as e:
            print(f"\n  ✖ Sin conexión a {URL_CFR}: {e}")
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

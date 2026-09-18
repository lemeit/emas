"""
snih_saladillo_v3.py
====================
Obtiene datos de la EMA Saladillo (SNIH) y los guarda en Cloudflare D1
(tabla unificada `mediciones`, estación EMA-EET).

Estación SNIH : 284094  (red RMET=28 + ID=4094)

Uso:
    python snih_saladillo_v3.py              # consulta + guarda en D1
    python snih_saladillo_v3.py --csv        # también exporta CSV local
    python snih_saladillo_v3.py --json       # muestra JSON crudo del SNIH
    python snih_saladillo_v3.py --loop 3600  # repite cada 1 hora (3600 seg)
    python snih_saladillo_v3.py --nod1       # solo consola, sin D1

Dependencias:
    pip install requests

Proyecto: Integración sensores calidad del aire
          Laboratorio de Industrias 7° Año — EEST N°1 "Gral. Savio" — Saladillo
Fuente:   SNIH / INA — datos sin validar — citar la fuente al publicar
"""

import os
import sys
import requests
import json
import csv
import argparse
import time
import warnings
from urllib3.exceptions import InsecureRequestWarning
from datetime import datetime, timezone, timedelta

from d1_writer import guardar_en_d1

ESTACION_D1 = "EMA-EET"

# ─── Configuración SNIH ────────────────────────────────────────────────────────
ESTACION     = "284094"
URL_ACTUALES = "https://snih.hidricosargentina.gob.ar/MuestraDatos.aspx/LeerDatosActuales"
URL_ULTIMOS  = "https://snih.hidricosargentina.gob.ar/MuestraDatos.aspx/LeerUltimosRegistros"
TIMEOUT      = 15

warnings.filterwarnings("ignore", category=InsecureRequestWarning)
SSL_VERIFY = False

HEADERS_SNIH = {
    "Content-Type":     "application/json; charset=utf-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":          "https://snih.hidricosargentina.gob.ar/MuestraDatos.aspx",
    "User-Agent":       "Mozilla/5.0 (compatible; EEST-Saladillo-script/3.0)",
}

# ─── Zona horaria Argentina ────────────────────────────────────────────────────
TZ_AR = timezone(timedelta(hours=-3))

# ─── Metadatos de parámetros ───────────────────────────────────────────────────
PARAMETROS = {
    4:    {"nombre": "Velocidad del Viento",      "unidad": "km/h",  "dec": 1},
    5:    {"nombre": "Dirección del Viento",       "unidad": "°",     "dec": 0, "rosa": True},
    14:   {"nombre": "Temperatura",               "unidad": "°C",    "dec": 1},
    18:   {"nombre": "Humedad",                   "unidad": "%",     "dec": 0},
    20:   {"nombre": "Precipitación (acum.)",      "unidad": "mm",    "dec": 2},
    218:  {"nombre": "Presión Atmosférica",        "unidad": "mBar",  "dec": 1},
    9998: {"nombre": "Precipitación Instantánea", "unidad": "mm",    "dec": 2},
}


# ─── Helpers ───────────────────────────────────────────────────────────────────
def parse_fecha(fecha_str):
    try:
        ms = int(fecha_str.replace("/Date(", "").replace(")/", ""))
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except Exception:
        return None

def formato_ar(dt):
    if dt is None:
        return "—"
    return dt.astimezone(TZ_AR).strftime("%d/%m/%Y %H:%M")

def grados_a_rosa(g):
    puntos = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
              "S","SSO","SO","OSO","O","ONO","NO","NNO"]
    return puntos[round(g / 22.5) % 16]

def formatear_valor(medicion):
    codigo = medicion["Codigo"]
    valor  = medicion["Valor"]
    meta   = PARAMETROS.get(codigo, {"unidad": "?", "dec": 2})
    if valor < -998:
        return "Sin dato"
    s = f"{valor:.{meta['dec']}f}"
    if meta.get("rosa"):
        return f"{s}° ({grados_a_rosa(valor)})"
    return f"{s} {meta['unidad']}"


# ─── SNIH ──────────────────────────────────────────────────────────────────────
def obtener_datos_actuales():
    payload = json.dumps({"estacion": ESTACION})
    resp = requests.post(URL_ACTUALES, data=payload, headers=HEADERS_SNIH,
                         timeout=TIMEOUT, verify=SSL_VERIFY)
    resp.raise_for_status()
    d = resp.json().get("d", {})
    if not d.get("RespuestaOK"):
        raise RuntimeError(f"SNIH error: {d.get('MsgErr','sin detalle')}")
    meds = d.get("Mediciones", [])
    if not meds:
        raise RuntimeError("SNIH respondió OK pero Mediciones vacío.")
    return meds


# ─── D1 (Cloudflare) ────────────────────────────────────────────────────────────
def guardar_en_d1_desde_snih(mediciones):
    """
    Convierte las mediciones del SNIH al formato de d1_writer e inserta
    en la tabla unificada `mediciones` (estación EMA-EET).
    Devuelve la cantidad de filas enviadas a D1.
    """
    filas = []
    for m in mediciones:
        codigo = m["Codigo"]
        valor  = m["Valor"]
        meta   = PARAMETROS.get(codigo, {"nombre": m.get("NombreCodigo","?"), "unidad":"?", "dec":2})
        dt_utc = parse_fecha(m["FechaHora"])

        if valor < -998 or dt_utc is None:
            continue

        filas.append({
            "codigo":         codigo,
            "parametro":      meta["nombre"],
            "unidad":         meta.get("unidad", "?"),
            "valor":          round(float(valor), meta.get("dec", 2)),
            "fecha_hora_utc": dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
        })

    return guardar_en_d1(ESTACION_D1, filas)


# ─── Consola ───────────────────────────────────────────────────────────────────
def mostrar_consola(mediciones, resultado_d1=None):
    ahora_ar = datetime.now(tz=TZ_AR).strftime("%d/%m/%Y %H:%M")
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   EMA Saladillo — EEST N°1 'Gral. Savio'            ║")
    print(f"║   Estación {ESTACION}  |  {ahora_ar}               ║")
    print("║   Red Meteorológica RMET — SNIH/INA — Sin validar   ║")
    print("╠══════════════════════════════════════════════════════╣")
    for m in mediciones:
        codigo = m["Codigo"]
        meta   = PARAMETROS.get(codigo, {"nombre": m.get("NombreCodigo", f"Cod {codigo}")})
        print(f"║  {meta['nombre']:<28} {formatear_valor(m):<14}  {formato_ar(parse_fecha(m['FechaHora']))}  ║")
    print("╠══════════════════════════════════════════════════════╣")
    if resultado_d1 is not None:
        print(f"║  D1: {resultado_d1} filas enviadas ✔                          ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()


# ─── CSV ───────────────────────────────────────────────────────────────────────
def exportar_csv(mediciones, archivo="saladillo_actuales.csv"):
    filas = []
    for m in mediciones:
        codigo = m["Codigo"]
        meta   = PARAMETROS.get(codigo, {"nombre": m.get("NombreCodigo","?"), "unidad":"?", "dec":2})
        valor  = m["Valor"]
        dt     = parse_fecha(m["FechaHora"])
        filas.append({
            "estacion":       ESTACION,
            "codigo":         codigo,
            "parametro":      meta["nombre"],
            "unidad":         meta.get("unidad","?"),
            "valor":          "" if valor < -998 else round(valor, meta.get("dec",2)),
            "fecha_hora_ar":  formato_ar(dt),
            "fecha_hora_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else "",
        })
    with open(archivo, "w", newline="", encoding="utf-8-sig") as f:
        campos = ["estacion","codigo","parametro","unidad","valor","fecha_hora_ar","fecha_hora_utc"]
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(filas)
    print(f"  ✔  CSV → {archivo}")


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="EMA Saladillo → Cloudflare D1")
    parser.add_argument("--csv",  action="store_true", help="Exportar CSV local")
    parser.add_argument("--json", action="store_true", help="Mostrar JSON crudo")
    parser.add_argument("--nod1", action="store_true", help="No guardar en D1")
    parser.add_argument("--loop", type=int, default=0,
                        help="Repetir cada N segundos (ej: --loop 3600)")
    args = parser.parse_args()

    def ciclo():
        """Devuelve True si el ciclo salió bien (consulta + guardado en D1,
        cuando corresponde), False si algo falló -- para que main() pueda
        terminar con código de salida != 0 y una corrida de GitHub Actions
        se vea roja de verdad, en vez de quedar en verde con un error
        impreso que nadie llega a leer (ver bitácora / auditoría horaria)."""
        ok = True
        try:
            mediciones = obtener_datos_actuales()

            if args.json:
                print(json.dumps(mediciones, indent=2, ensure_ascii=False))
                return True

            resultado_d1 = None
            if not args.nod1:
                try:
                    resultado_d1 = guardar_en_d1_desde_snih(mediciones)
                except Exception as e:
                    print(f"  ⚠  D1: {e}")
                    ok = False

            mostrar_consola(mediciones, resultado_d1)

            if args.csv:
                exportar_csv(mediciones)

            return ok

        except requests.HTTPError as e:
            print(f"\n  ✖ Error HTTP SNIH: {e}")
        except requests.ConnectionError as e:
            if "SSL" in str(e) or "certificate" in str(e).lower():
                print("\n  ✖ Error SSL del servidor SNIH (certificado vencido)")
            else:
                print("\n  ✖ Sin conexión a snih.hidricosargentina.gob.ar")
        except RuntimeError as e:
            print(f"\n  ✖ {e}")
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

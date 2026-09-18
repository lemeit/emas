#!/usr/bin/env python3
"""
audit_timestamps.py
====================
Auditoría rápida de sanidad horaria para las 5 estaciones de EMAS.

Qué chequea, por estación:
  1. Ciclo diurno de temperatura: agrupa las lecturas de los últimos N días
     por hora del día (0-23, hora Argentina) y promedia. Si el reloj de la
     estación (o algo en el scraper) está corrido, la hora del mínimo
     debería seguir cayendo de madrugada y la del máximo a la tarde -- si
     no, es señal de que la hora que estamos guardando no es la hora real
     de la medición.
  2. Frescura: hace cuánto no llega un dato nuevo. Una estación "muda"
     hace más de unas pocas horas es un scraper caído, no un problema de
     huso horario -- pero igual conviene saberlo.

No hace falta tocar nada del repo para correrlo ni escribe nada en D1;
solo lee la API pública de solo lectura.

Uso:
    python audit_timestamps.py
    python audit_timestamps.py --dias 14        # ventana más corta
    python audit_timestamps.py --base https://api.lemeit.ar/emas
"""
import argparse
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import requests

TZ_AR = timezone(timedelta(hours=-3))

# tabla -> (nombre para mostrar, clave del parámetro Temperatura en el JSON
# "wide" que devuelve /historico -- las estaciones tipo EET usan el código
# numérico como string, el resto usan el nombre del parámetro).
ESTACIONES = {
    "mediciones_ema": ("EMA-EET", "14"),
    "mediciones_cfr": ("EMA-CFR", "Temperatura"),
    "mediciones_dc":  ("EMA-DC",  "Temperatura"),
    "mediciones_cs":  ("EMA-CS",  "Temperatura"),
    "mediciones_25c": ("EMA-25C", "Temperatura"),
}

# Rango horario "esperable" para el mínimo/máximo diario en Saladillo/25 de
# Mayo (clima templado, sin depender de estación del año exacta -- ventanas
# generosas a propósito para no marcar falsos positivos).
HORA_MIN_ESPERADA = range(1, 10)    # 01:00 a 09:00 -- madrugada/amanecer
HORA_MAX_ESPERADA = range(11, 20)   # 11:00 a 19:00 -- mediodía/tarde


def fetch_historico(base, tabla, horas):
    url = f"{base}/rest/v1/{tabla}/historico"
    r = requests.get(url, params={"horas": horas}, timeout=30)
    r.raise_for_status()
    return r.json()


def analizar_estacion(nombre, filas, clave_temp):
    if not filas:
        return {
            "nombre": nombre, "filas": 0, "ok_diurno": None,
            "hora_min": None, "hora_max": None,
            "ultima_ar": None, "horas_desde_ultima": None,
        }

    por_hora = defaultdict(list)
    for f in filas:
        v = f.get(clave_temp)
        ar = f.get("fecha_hora_ar")
        if v is None or not ar:
            continue
        try:
            hora = int(ar.split(" ")[1].split(":")[0])
        except (IndexError, ValueError):
            continue
        por_hora[hora].append(float(v))

    promedios = {h: sum(vs) / len(vs) for h, vs in por_hora.items() if vs}
    hora_min = min(promedios, key=promedios.get) if promedios else None
    hora_max = max(promedios, key=promedios.get) if promedios else None

    ok_diurno = None
    if hora_min is not None and hora_max is not None:
        ok_diurno = (hora_min in HORA_MIN_ESPERADA) and (hora_max in HORA_MAX_ESPERADA)

    # Frescura: última fila (el endpoint ya devuelve ascendente, más vieja
    # primero -- la última de la lista es la más reciente).
    ultima_ar = filas[-1].get("fecha_hora_ar")
    horas_desde_ultima = None
    if ultima_ar:
        try:
            dt_ar = datetime.strptime(ultima_ar, "%d/%m/%Y %H:%M").replace(tzinfo=TZ_AR)
            horas_desde_ultima = (datetime.now(tz=TZ_AR) - dt_ar).total_seconds() / 3600
        except ValueError:
            pass

    return {
        "nombre": nombre, "filas": len(filas), "ok_diurno": ok_diurno,
        "hora_min": hora_min, "hora_max": hora_max,
        "temp_en_hora_min": promedios.get(hora_min) if hora_min is not None else None,
        "temp_en_hora_max": promedios.get(hora_max) if hora_max is not None else None,
        "ultima_ar": ultima_ar, "horas_desde_ultima": horas_desde_ultima,
    }


def main():
    ap = argparse.ArgumentParser(description="Auditoría de sanidad horaria de EMAS")
    ap.add_argument("--dias", type=int, default=30, help="ventana a analizar, en días (default 30)")
    ap.add_argument("--base", default="https://api.lemeit.ar/emas", help="base URL de la API")
    args = ap.parse_args()

    horas = args.dias * 24
    print(f"Auditando últimos {args.dias} días ({horas}h) contra {args.base}\n")
    print(f"{'Estación':<10} {'Filas':>6}  {'Mín ~hora':>10} {'Máx ~hora':>10}  {'Diurno OK':>10}  {'Última lectura AR':>20}  {'Hace (h)':>9}")
    print("-" * 90)

    hubo_problema = False
    for tabla, (nombre, clave_temp) in ESTACIONES.items():
        try:
            filas = fetch_historico(args.base, tabla, horas)
        except Exception as e:
            print(f"{nombre:<10} ERROR consultando la API: {e}")
            hubo_problema = True
            continue

        r = analizar_estacion(nombre, filas, clave_temp)

        if r["filas"] == 0:
            print(f"{r['nombre']:<10} {'0':>6}  {'—':>10} {'—':>10}  {'SIN DATOS':>10}  {'—':>20}  {'—':>9}")
            hubo_problema = True
            continue

        diurno_txt = "OK" if r["ok_diurno"] else ("¿?" if r["ok_diurno"] is None else "¡REVISAR!")
        if not r["ok_diurno"]:
            hubo_problema = True

        gap_txt = f"{r['horas_desde_ultima']:.1f}" if r["horas_desde_ultima"] is not None else "—"
        if r["horas_desde_ultima"] is not None and r["horas_desde_ultima"] > 6:
            gap_txt += " ⚠"
            hubo_problema = True

        print(f"{r['nombre']:<10} {r['filas']:>6}  {str(r['hora_min'])+'h':>10} {str(r['hora_max'])+'h':>10}  {diurno_txt:>10}  {str(r['ultima_ar']):>20}  {gap_txt:>9}")

    print("\nCómo leerlo:")
    print("- 'Mín ~hora' / 'Máx ~hora': a qué hora del día (AR) da en promedio la temperatura")
    print("  más baja / más alta. Si 'Mín' no cae de madrugada (~1-9h) o 'Máx' no cae de")
    print("  tarde (~11-19h), la hora que estamos guardando probablemente no es la hora real")
    print("  de la medición -- ahí sí hay que mirar el scraper de esa estación en detalle.")
    print("- 'Hace (h)': cuántas horas pasaron desde el último dato. Más de ~6h con un cron")
    print("  horario sugiere que el scraper de esa estación está fallando silenciosamente.")

    if hubo_problema:
        print("\n⚠ Se encontraron estaciones para revisar (ver arriba).")
        sys.exit(1)
    print("\nTodo OK: las 5 estaciones tienen datos recientes y un ciclo diurno coherente.")


if __name__ == "__main__":
    main()

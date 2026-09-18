"""
dc_saladillo.py
===============
Extrae datos meteorológicos de la EMA Defensa Civil Saladillo
mediante OCR sobre la imagen de la cámara Meteobridge.

URL imagen: https://content.meteobridge.com/cam/791751a5ea5fe89561a11d743920c3ef/camplus.jpg
Destino: Cloudflare D1 (tabla unificada `mediciones`, estación EMA-DC)

Parámetros extraídos:
  Temperatura actual    [°C]
  Temperatura máxima    [°C]
  Temperatura mínima    [°C]
  Humedad               [%]
  Presión               [hPa]
  Velocidad viento      [km/h]
  Ráfaga                [km/h]
  Dirección viento      [°]
  Lluvia acumulada      [mm]
  Punto de rocío        [°C]

Uso:
    python dc_saladillo.py              # OCR + guarda en D1
    python dc_saladillo.py --csv        # también exporta CSV
    python dc_saladillo.py --nod1       # solo consola
    python dc_saladillo.py --loop 3600  # repite cada hora

Dependencias:
    pip install pytesseract pillow requests
    + Tesseract instalado en C:\\Program Files\\Tesseract-OCR\\

Proyecto: Integración EMA Saladillo — EEST N°1 "Gral. Savio"
"""

import os
import requests
import pytesseract
import json
import csv
import re
import argparse
import time
import warnings
from PIL import Image, ImageEnhance, ImageFilter
import numpy as np
from io import BytesIO
from datetime import datetime, timezone, timedelta
from urllib3.exceptions import InsecureRequestWarning

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

# ─── Configuración ─────────────────────────────────────────────────────────────
import sys, shutil
if sys.platform == "win32":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    # Linux (GitHub Actions / GitLab CI) — tesseract en PATH
    tess_path = shutil.which("tesseract")
    if tess_path:
        pytesseract.pytesseract.tesseract_cmd = tess_path

from d1_writer import guardar_en_d1

URL_CAM     = "https://content.meteobridge.com/cam/791751a5ea5fe89561a11d743920c3ef/camplus.jpg"
ESTACION    = "DC-Saladillo"
ESTACION_D1 = "EMA-DC"
TIMEOUT     = 15

# Layout aproximado de bloques Meteobridge (coordenadas en imagen 1800x1013)
# Usado por preprocesar_bloques()

TZ_AR = timezone(timedelta(hours=-3))

# ─── Rangos válidos por parámetro (validación anti-OCR) ───────────────────────
# Valores fuera de rango se descartan con advertencia en lugar de insertarse
RANGOS_VALIDOS = {
    "Temperatura":        (-10,  45),
    "Temperatura Maxima": (-10,  50),
    "Temperatura Minima": (-15,  40),
    "Humedad":            ( 10, 100),
    "Presion":            (980, 1040),
    "Velocidad Viento":   (  0,  80),
    "Rafaga":             (  0, 120),
    "Direccion Viento":   (  0, 360),
    "Lluvia Acumulada":   (  0,  60),
    "Punto de Rocio":     (-15,  35),
}

# Máxima variación aceptable respecto al valor anterior (detección de saltos de OCR)
DELTA_MAXIMO = {
    "Temperatura":      8.0,
    "Humedad":         25.0,
    "Presion":          5.0,
    "Velocidad Viento": 40.0,
    "Rafaga":           60.0,
    "Lluvia Acumulada": 40.0,
}

_ultimo_valido = {}   # caché del último valor válido por parámetro
_rechazados    = []   # log de valores rechazados en la última ejecución


def validar_valor(parametro, valor):
    """
    Valida rango absoluto y delta respecto al último registro.
    Retorna (True, None) si válido, (False, motivo) si no.
    """
    if parametro in RANGOS_VALIDOS:
        vmin, vmax = RANGOS_VALIDOS[parametro]
        if not (vmin <= valor <= vmax):
            return False, f"fuera de rango [{vmin}, {vmax}]"

    if parametro in DELTA_MAXIMO and parametro in _ultimo_valido:
        delta = abs(valor - _ultimo_valido[parametro])
        if delta > DELTA_MAXIMO[parametro]:
            return False, f"delta {delta:.1f} > máx {DELTA_MAXIMO[parametro]}"

    _ultimo_valido[parametro] = valor
    return True, None


# ─── Descarga y preprocesamiento ───────────────────────────────────────────────
def descargar_imagen():
    r = requests.get(URL_CAM, timeout=TIMEOUT)
    r.raise_for_status()
    return Image.open(BytesIO(r.content))


def preprocesar(img):
    """
    Recorta y preprocesa la imagen para OCR.

    La imagen de Meteobridge tiene bloques de colores fuertes en la franja
    inferior: verde (temperatura), rojo (máxima), azul (mínima/lluvia),
    gris oscuro (humedad, presión, viento, ráfaga).

    Estrategia: recortamos cada bloque individualmente por color y
    los apilamos verticalmente para el OCR. Esto evita que el ruido
    de la imagen de la pista interfiera con los datos.
    """
    import numpy as np

    w, h = img.size
    arr = np.array(img)

    # ── Paso 1: detectar la fila donde empiezan los bloques de datos ──────────
    # Los bloques tienen fondo muy oscuro (R,G,B todos < 80) o colores fuertes
    # Buscamos la primera fila desde abajo que tenga píxeles oscuros o coloreados
    # en al menos 30% del ancho
    fila_inicio = int(h * 0.70)  # nunca subir más del 70% desde arriba
    for y in range(int(h * 0.95), int(h * 0.60), -1):
        fila = arr[y]
        # Píxeles con fondo negro/oscuro semitransparente (R<100, G<100, B<100)
        oscuros = np.sum((fila[:,0] < 100) & (fila[:,1] < 100) & (fila[:,2] < 100))
        if oscuros > w * 0.25:
            fila_inicio = y
            break

    # ── Paso 2: recortar solo la franja de datos ──────────────────────────────
    # Incluir también la línea de título (20px arriba del inicio detectado)
    top = max(0, fila_inicio - int(h * 0.08))
    franja = img.crop((0, top, w, h))

    # ── Paso 3: escalar x3 (más que antes para mejor OCR en texto pequeño) ────
    fw, fh = franja.size
    franja = franja.resize((fw * 3, fh * 3), Image.LANCZOS)

    # ── Paso 4: procesar cada bloque de color por separado ───────────────────
    # Convertir a RGB numpy para segmentar por color
    arr2 = np.array(franja)
    fh2, fw2 = arr2.shape[:2]

    # Máscara de texto: píxeles blancos/claros (texto) sobre fondos de color
    # Invertir: hacer el fondo blanco y el texto negro para Tesseract
    gris = franja.convert("L")

    # Aumentar contraste fuertemente
    gris = ImageEnhance.Contrast(gris).enhance(3.0)
    gris = ImageEnhance.Sharpness(gris).enhance(2.0)

    return gris


def preprocesar_bloques(img):
    """
    Preprocesamiento por extracción de píxeles blancos.

    El texto en la imagen de Meteobridge es blanco sobre fondos
    de color (verde, rojo, azul, gris). La conversión directa a
    escala de grises pierde el texto. La solución es:
    1. Recortar la franja inferior (74% hacia abajo)
    2. Crear máscara de píxeles claros (R,G,B > 175)
    3. Convertir a binaria: texto negro sobre blanco
    4. Escalar x2 para mejor OCR
    """
    w, h = img.size
    top = int(h * 0.74)
    franja = img.crop((0, top, w, h))

    arr = np.array(franja.convert("RGB"))
    r_ch = arr[:,:,0].astype(int)
    g_ch = arr[:,:,1].astype(int)
    b_ch = arr[:,:,2].astype(int)

    # Píxeles blancos/claros = texto
    mascara = (r_ch > 175) & (g_ch > 175) & (b_ch > 175)

    # Imagen binaria: texto negro sobre fondo blanco
    binaria = np.ones_like(r_ch) * 255
    binaria[mascara] = 0

    resultado = Image.fromarray(binaria.astype(np.uint8))
    resultado = resultado.resize(
        (resultado.width * 2, resultado.height * 2), Image.NEAREST
    )
    return resultado


# ─── OCR y extracción ──────────────────────────────────────────────────────────
def extraer_texto(img_procesada):
    return pytesseract.image_to_string(
        img_procesada,
        lang="eng",
        config="--psm 6 --oem 3"
    )


def parsear_datos(texto, timestamp_ar):
    """
    Extrae los valores meteorológicos del texto OCR.
    Devuelve lista de dicts listos para guardar_en_d1().
    """
    datos = []

    def add(parametro, unidad, valor, texto_val=None):
        if valor is not None:
            val_float = round(float(valor), 2)
            ok, motivo = validar_valor(parametro, val_float)
            if not ok:
                _rechazados.append(f"  ⚠  {parametro}: {val_float} — {motivo}")
                return
            datos.append({
                "estacion":      ESTACION,
                "parametro":     parametro,
                "unidad":        unidad,
                "valor":         val_float,
                "valor_texto":   texto_val or str(val_float),
                "fecha_hora_ar": timestamp_ar,
            })

    # Temperatura actual — buscada en la línea de datos (la que tiene % y hPa)
    # El OCR con extracción de blancos puede producir:
    #   "22.3C  42%  1011.4hPa ..."   (ideal)
    #   "2270 Ne 42% 1011.4hPa ..."   (punto/C fusionados por OCR)
    temp_actual = None
    for linea in texto.split('\n'):
        linea = linea.strip()
        if '%' not in linea:
            continue
        if not re.search(r'h[Pp][Aa]?', linea):
            continue
        # Patrón 1: número con punto decimal → "22.3C"
        m = re.match(r'^(-?\d{1,2}\.\d)\s*[Cc0O]', linea)
        if m:
            val = float(m.group(1))
            if -10 <= val <= 45:
                temp_actual = val
                break
        # Patrón 2: 4 dígitos sin punto → "2270" = "22.7" (OCR fusionó decimal)
        m = re.match(r'^(\d{2})(\d{2})\b', linea)
        if m:
            val = float(m.group(1) + '.' + m.group(2)[0])
            if -10 <= val <= 45:
                temp_actual = val
                break
        # Patrón 3: 3 dígitos → "227" = "22.7"
        m = re.match(r'^(\d{2})(\d{1})\b', linea)
        if m:
            val = float(m.group(1) + '.' + m.group(2))
            if -10 <= val <= 45:
                temp_actual = val
                break

    # Fallback: buscar XX.XC en todo el texto evitando Max/Min
    if temp_actual is None:
        for cand in re.findall(r'(\d{1,2}\.\d)\s*[Cc]', texto):
            val = float(cand)
            pos = texto.find(cand)
            contexto = texto[max(0, pos-5):pos].lower()
            if 'max' not in contexto and 'min' not in contexto and -10 <= val <= 45:
                temp_actual = val
                break

    if temp_actual is not None:
        add("Temperatura", "°C", round(temp_actual, 1))

    # Temperatura máxima — "Max 28.1C"
    m = re.search(r'[Mm]ax\s+(\d+\.?\d*)\s*[Cc]', texto)
    if m:
        add("Temperatura Maxima", "°C", m.group(1))

    # Temperatura mínima — "Min 12.0C"
    m = re.search(r'[Mm]in\s+(\d+\.?\d*)\s*[Cc]', texto)
    if m:
        add("Temperatura Minima", "°C", m.group(1))

    # Humedad — "84%"
    m = re.search(r'(\d{1,3})\s*%', texto)
    if m:
        add("Humedad", "%", m.group(1))

    # Presión — "1009.5 hPa" o "1009.5h"
    m = re.search(r'(\d{3,4}\.?\d*)\s*h[Pp]?[Aa]?', texto)
    if m:
        val = float(m.group(1))
        if 900 < val < 1100:  # rango válido de presión
            add("Presion", "hPa", val)

    # Punto de rocío — "P.rocio 9.5C" o "rocio 9.5C"
    m = re.search(r'[Rr]ocio\s+(\d+\.?\d*)\s*[Cc]', texto)
    if m:
        add("Punto de Rocio", "°C", m.group(1))

    # Dirección del viento — "NW (315.0°)" o "NW 315.0"
    m = re.search(r'([NS][NESOW]*|[EW])\s*[\(\[]?\s*(\d+\.?\d*)\s*[°o]?\s*[\)\]]?', texto)
    if m:
        grados = float(m.group(2))
        if 0 <= grados <= 360:
            add("Direccion Viento", "°", grados, f"{m.group(1)} ({grados}°)")

    # Velocidades: hay varias — tomamos todas las "X km/h"
    velocidades = re.findall(r'(\d+\.?\d*)\s*km\s*[\/\s]?\s*h', texto, re.IGNORECASE)
    if len(velocidades) >= 1:
        add("Velocidad Viento", "km/h", velocidades[0])
    if len(velocidades) >= 2:
        # La segunda velocidad suele ser la ráfaga
        rafaga = float(velocidades[1])
        if rafaga > float(velocidades[0]):  # ráfaga siempre >= velocidad media
            add("Rafaga", "km/h", rafaga)

    # Lluvia acumulada diaria — buscar el valor mm que sigue a "Lluvia"
    # Evitar el acumulado anual (205.8mm que aparece al final)
    m = re.search(r'[Ll]luvia[^\n]*?(\d+\.?\d*)\s*mm', texto)
    if m:
        val = float(m.group(1))
        if val < 200:  # el acumulado anual suele ser >> 200mm
            add("Lluvia Acumulada", "mm", val)
    else:
        # fallback: primer mm razonable
        for lv in re.findall(r'(\d+\.?\d*)\s*mm', texto, re.IGNORECASE):
            if float(lv) < 200:
                add("Lluvia Acumulada", "mm", lv)
                break

    return datos


def extraer_timestamp_imagen(texto):
    """
    Intenta extraer el timestamp de la imagen del texto OCR.
    Primero busca fecha completa "21-03-2026 23:11",
    si no encuentra, busca solo la hora "23:21" y combina con fecha de hoy.
    """
    # Intentar fecha completa dd-mm-yyyy HH:MM
    m = re.search(r'(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2})', texto)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d-%m-%Y %H:%M")
            return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass

    # Intentar solo hora HH:MM (la primera que aparezca, típicamente 23:21)
    m = re.search(r'\b(\d{2}:\d{2})\b', texto)
    if m:
        hoy = datetime.now(tz=TZ_AR).strftime("%d/%m/%Y")
        return f"{hoy} {m.group(1)}"

    return datetime.now(tz=TZ_AR).strftime("%d/%m/%Y %H:%M")


# ─── D1 (Cloudflare) ────────────────────────────────────────────────────────────
def guardar_en_d1_desde_dc(datos):
    """Inserta los datos en la tabla unificada `mediciones` (estación EMA-DC)."""
    return guardar_en_d1(ESTACION_D1, datos)


# ─── Consola ───────────────────────────────────────────────────────────────────
def mostrar_consola(datos, enviados=None, rechazados=None):
    ahora = datetime.now(tz=TZ_AR).strftime("%d/%m/%Y %H:%M")
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║   EMA Defensa Civil Saladillo                        ║")
    print(f"║   Consulta: {ahora}                            ║")
    print("║   Davis Instruments · Meteobridge · OCR              ║")
    print("╠══════════════════════════════════════════════════════╣")
    for d in datos:
        val = f"{d['valor']:.1f} {d['unidad']}"
        print(f"║  {d['parametro']:<28} {val:<14}  {d['fecha_hora_ar']}  ║")
    if not datos:
        print("║  Sin datos extraídos — revisar imagen                ║")
    print("╠══════════════════════════════════════════════════════╣")
    if rechazados:
        for msg in rechazados:
            print(f"║  {msg:<52}  ║")
        print("╠══════════════════════════════════════════════════════╣")
    if enviados is not None:
        print(f"║  D1: {enviados} filas enviadas ✔                              ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()


# ─── CSV ───────────────────────────────────────────────────────────────────────
def exportar_csv(datos, archivo="dc_actuales.csv"):
    with open(archivo, "w", newline="", encoding="utf-8-sig") as f:
        campos = ["estacion","parametro","unidad","valor","valor_texto","fecha_hora_ar"]
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(datos)
    print(f"  ✔  CSV → {archivo}")


# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="EMA Defensa Civil → Cloudflare D1 (OCR)")
    parser.add_argument("--csv",   action="store_true", help="Exportar CSV")
    parser.add_argument("--nod1",  action="store_true", help="No guardar en D1")
    parser.add_argument("--debug", action="store_true", help="Mostrar texto OCR crudo")
    parser.add_argument("--loop",  type=int, default=0,
                        help="Repetir cada N segundos (ej: --loop 3600)")
    args = parser.parse_args()

    def ciclo():
        """Devuelve True si el ciclo salió bien (OCR + guardado en D1,
        cuando corresponde), False si algo falló -- para que main() pueda
        terminar con código de salida != 0 y una corrida de GitHub Actions
        se vea roja de verdad, en vez de quedar en verde con un error
        impreso que nadie llega a leer (ver bitácora / auditoría horaria)."""
        ok = True
        try:
            img         = descargar_imagen()
            procesada   = preprocesar_bloques(img)
            texto       = extraer_texto(procesada)

            if args.debug:
                print("\n── TEXTO OCR CRUDO ──")
                print(texto)
                print("─────────────────────\n")

            _rechazados.clear()
            timestamp   = extraer_timestamp_imagen(texto)
            datos       = parsear_datos(texto, timestamp)

            if _rechazados:
                print("\n── VALORES RECHAZADOS (OCR) ──")
                for r in _rechazados:
                    print(r)
                print()

            enviados = None
            if not args.nod1:
                try:
                    enviados = guardar_en_d1_desde_dc(datos)
                except Exception as e:
                    print(f"  ⚠  D1: {e}")
                    ok = False

            mostrar_consola(datos, enviados, _rechazados)

            if args.csv:
                exportar_csv(datos)

            return ok

        except requests.HTTPError as e:
            print(f"\n  ✖ Error HTTP descargando imagen: {e}")
        except requests.ConnectionError:
            print("\n  ✖ Sin conexión a content.meteobridge.com")
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

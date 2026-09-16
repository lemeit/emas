# Red EMAS

[![sitio](https://img.shields.io/badge/sitio-emas.lemeit.ar-009688?style=flat-square)](https://emas.lemeit.ar) [![docs](https://img.shields.io/badge/docs-wiki.lemeit.ar-009688?style=flat-square)](https://wiki.lemeit.ar/red-ambiental/02-ema-saladillo/) [![API](https://img.shields.io/badge/API-pública-FF5722?style=flat-square)](https://emas.lemeit.ar/api.html) [![licencia](https://img.shields.io/badge/licencia-MIT-009688?style=flat-square)](#licencia)

Sistema de adquisición y visualización de datos meteorológicos de estaciones automáticas en Saladillo y 25 de Mayo, Buenos Aires, Argentina. Publicado en [emas.lemeit.ar](https://emas.lemeit.ar).

Es uno de tres proyectos de monitoreo ambiental que comparten la misma infraestructura de Cloudflare (Pages + Workers + D1), pensados para integrarse a futuro: este (meteorología), [aq.lemeit.ar](https://aq.lemeit.ar) (calidad del aire, sensores PurpleAir) y [lemeit-wq](https://github.com/lemeit/lemeit-wq) (calidad del agua, en desarrollo, pensado para wq.lemeit.ar).

📚 Documentación técnica completa, guías de uso de la API y bitácora de los tres portales: [wiki.lemeit.ar](https://wiki.lemeit.ar).

## Estaciones

| Código | Nombre | Método | Coordenadas |
|--------|--------|--------|-------------|
| **EMA-EET** | EEST N°1 "Gral. Savio" | API SNIH/INA | -35.64533, -59.78482 |
| **EMA-CFR** | Centro de Formación Rural | HTML scraping | -35.62236, -59.78359 |
| **EMA-DC** | Defensa Civil — Aeródromo | OCR imagen Meteobridge | -35.60063, -59.81350 |
| **EMA-CS** | Clima Saladillo — B° Falucho | JSON Meteotemplate | -35.64500, -59.77580 |
| **EMA-25C** | 25Clima — 25 de Mayo (`IDEMAY14`) | API Weather Underground (PWS) | -35.435069, -60.170656 |

**EMA-25C (septiembre 2026, en incorporación)**: primera estación fuera del partido de Saladillo — 25 de Mayo, muy cerca de la Esc. Ed. Artística N°1 "Lola Mora" (ya parte de la red de aq.lemeit.ar). No es una estación propia del proyecto: es la estación pública "25Clima" (25clima.ar), operada por N-TecLab/SS Desarrollos y registrada en Weather Underground con station ID `IDEMAY14`. Se consulta vía la API pública de PWS de Weather Underground (`scrapers/wu_25demayo.py`), con una API key propia (no la del sitio) — ver "Variables de entorno" abajo. Pendiente: confirmar con el operador antes de darle carácter permanente/oficial en el dashboard, ya que la estación no es del proyecto.

## Arquitectura

```
Scrapers (GitHub Actions, cron horario)
    ↓ (Cloudflare D1 HTTP API)
Cloudflare D1 — tabla unificada "mediciones"
    ↓ (consultada por)
Worker "ema-saladillo-api" (Cloudflare Workers)
    ↓ (mismo formato de consulta que antes usaba PostgREST/Supabase)
Dashboard HTML estático (Cloudflare Pages) — emas.lemeit.ar
```

Hasta agosto de 2026 la base de datos era Supabase (PostgreSQL), con 4 tablas separadas (una por estación). Se migró todo el historial (~30.200 filas) a una tabla D1 unificada, y se agregó el Worker como capa de compatibilidad para no tener que reescribir el dashboard. Ver `worker/` y `d1/schema.sql`.

## Scrapers

| Script | Estación | Descripción |
|--------|----------|-------------|
| `scrapers/snih_saladillo_v3.py` | EMA-EET | API POST JSON al SNIH/INA |
| `scrapers/cfr_saladillo.py` | EMA-CFR | Scraping HTML con BeautifulSoup |
| `scrapers/dc_saladillo.py` | EMA-DC | OCR con Tesseract sobre imagen JPG |
| `scrapers/cs_saladillo.py` | EMA-CS | Endpoint JSON de Meteotemplate |
| `scrapers/wu_25demayo.py` | EMA-25C | API pública v2 de Weather Underground (PWS `IDEMAY14`), estación de terceros |
| `scrapers/d1_writer.py` | — | Helper compartido: escribe en D1 vía la API HTTP de Cloudflare |
| `scrapers/supabase_ping.py` | — | Ping diario a Supabase (proyecto compartido con otras apps personales, no relacionado a EMA) |

## Instalación local

```bash
pip install -r requirements.txt
# + Tesseract OCR instalado en el sistema (solo para EMA-DC)
```

## Variables de entorno (GitHub Actions)

Los scrapers escriben en D1 a través de la API HTTP de Cloudflare (`scrapers/d1_writer.py`). Se configuran como secrets del repositorio (GitHub → repo → Settings → Secrets and variables → Actions → New repository secret):

```
CF_ACCOUNT_ID=...
CF_DATABASE_ID=b5b1eef7-5c8d-42a8-a23e-69cd5ae1cd30
CF_API_TOKEN=...   # con permiso D1:Edit
WU_API_KEY=...     # API key propia de Weather Underground, solo para scrapers/wu_25demayo.py
                    # (EMA-25C). Se genera gratis en wunderground.com sin necesitar una
                    # estación física propia — no es la key embebida en 25clima.ar, esa no
                    # se usa acá. Ver wiki para el paso a paso.
```

`supabase_ping.py` sigue usando `SUPA_URL` / `SUPA_KEY` por separado — no tiene relación con EMA, solo mantiene activo el proyecto Supabase compartido con otras apps.

## Dashboard

El archivo `index.html` (raíz del repo) es un single-file HTML estático que consulta el Worker de Cloudflare vía REST. No requiere backend propio. Deployado en Cloudflare Pages (`wrangler pages deploy`).

**Logos de proveedor por estación** (agosto 2026): el footer muestra el logo del proveedor de la red/hardware de la estación seleccionada (o los 4 juntos en la vista "⇌ Comparativa") — TECMES para EMA-EET (deliberadamente el proveedor de la red RMET/SNIH, no la institución que aloja la estación), CFR Saladillo, Defensa Civil Saladillo y Clima Saladillo para las otras tres. Los mismos 4 logos también aparecen en el panel "Acerca de" (sección "📍 Estaciones de la red"), por ahora — más adelante podrían reemplazarse por fotos reales de cada estación/monitor. El tamaño de los logos del footer usa `logoSize: "md"` (20px) en vez del `"sm"` (15px) por defecto del footer compartido — ver `lemeit-design`.

**Mapa**: los tiles se piden al propio Worker (`GET /tiles/:style/:z/:x/:y{@2x}.png`, `style` = `light_all` \| `dark_all`), que actúa de proxy hacia CARTO Basemaps agregando la API key del secret `CARTO_API_KEY` del lado del servidor — así la key nunca queda expuesta en el HTML público. Configurar con `npx wrangler secret put CARTO_API_KEY` desde `worker/`. Key gratuita (tope 5M tiles/mes) en [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey).

## Base de datos (Cloudflare D1)

Base: `ema-saladillo-db` — tabla unificada `mediciones` (columna `estacion` distingue EMA-EET/CFR/DC/CS). Ver `d1/schema.sql` para el esquema completo, incluyendo el índice único que evita filas duplicadas.

El Worker `worker/src/index.js` expone rutas compatibles con el formato PostgREST que usaba el dashboard (`mediciones_ema`, `mediciones_cfr`, `mediciones_dc`, `mediciones_cs`, `v_ema_armonizada`, `v_temperatura_comparativa`), calculadas sobre la tabla unificada.

**API pública (agosto 2026)**: las mismas rutas de arriba están pensadas para que cualquiera las consuma directo — CORS abierto, sin autenticación ni token, son de solo lectura. Todas aceptan `desde`/`hasta` (`YYYY-MM-DD[ HH:MM:SS]`, UTC) como rango de fechas absoluto (pisa a `horas` si viene alguno de los dos) y `&formato=csv` para bajar CSV en vez de JSON. Documentación con ejemplos: [`emas.lemeit.ar/api.html`](https://emas.lemeit.ar/api.html) (fuente: `api.html` en la raíz de este repo).

## Proyecto educativo

Red de estaciones meteorológicas automáticas de Saladillo y 25 de Mayo, con fines de monitoreo ambiental y educación ambiental ciudadana.
Ing. Luciano Lamaita — docente de Física y Química en Saladillo, Buenos Aires — más proyectos y materiales en [profe.lemeit.ar](https://profe.lemeit.ar)

## Licencia

Datos meteorológicos: Creative Commons (EMA-EET/SNIH), uso público (EMA-CFR, EMA-DC, EMA-CS).  
Código: MIT.

# EMAS — Red de Estaciones Meteorológicas Automáticas

[![sitio](https://img.shields.io/badge/sitio-app.lemeit.ar/emas-009688?style=flat-square)](https://app.lemeit.ar/emas) [![docs](https://img.shields.io/badge/docs-wiki.lemeit.ar-009688?style=flat-square)](https://wiki.lemeit.ar/red-ambiental/02-ema-saladillo/) [![API](https://img.shields.io/badge/API-pública-FF5722?style=flat-square)](https://app.lemeit.ar/emas/api.html) [![licencia](https://img.shields.io/badge/licencia-MIT-009688?style=flat-square)](#licencia)

Sistema de adquisición y visualización de datos meteorológicos de estaciones automáticas en Saladillo y 25 de Mayo, Buenos Aires, Argentina. Publicado en [app.lemeit.ar/emas](https://app.lemeit.ar/emas).

Es uno de tres proyectos de monitoreo ambiental que comparten la misma infraestructura de Cloudflare (Pages + Workers + D1), pensados para integrarse a futuro: este (meteorología), [app.lemeit.ar/aq](https://app.lemeit.ar/aq) (calidad del aire, sensores PurpleAir + AirGradient) y [app.lemeit.ar/wq](https://app.lemeit.ar/wq) (calidad del agua).

📚 Documentación técnica completa, guías de uso de la API y bitácora de los tres portales: [wiki.lemeit.ar](https://wiki.lemeit.ar).

## Estaciones

| Código | Nombre | Método | Coordenadas |
|--------|--------|--------|-------------|
| **EMA-EET** | EEST N°1 "Gral. Savio" | API SNIH/INA | -35.64533, -59.78482 |
| **EMA-CFR** | Centro de Formación Rural | HTML scraping | -35.62236, -59.78359 |
| **EMA-DC** | Defensa Civil — Aeródromo | OCR imagen Meteobridge | -35.60063, -59.81350 |
| **EMA-CS** | Clima Saladillo — B° Falucho | JSON Meteotemplate | -35.64500, -59.77580 |
| **EMA-25C** | 25Clima — 25 de Mayo (`IDEMAY14`) | API Weather Underground (PWS) | -35.435069, -60.170656 |

**EMA-25C (septiembre 2026, en incorporación)**: primera estación fuera del partido de Saladillo — 25 de Mayo, muy cerca de la Esc. Ed. Artística N°1 "Lola Mora" (ya parte de la red de app.lemeit.ar/aq). No es una estación propia del proyecto: es la estación pública "25Clima" (25clima.ar), operada por N-TecLab/SS Desarrollos y registrada en Weather Underground con station ID `IDEMAY14`. Se consulta vía la API pública de PWS de Weather Underground (`scrapers/wu_25demayo.py`), con una API key propia (no la del sitio) — ver "Variables de entorno" abajo. Pendiente: confirmar con el operador antes de darle carácter permanente/oficial en el dashboard, ya que la estación no es del proyecto. Ver "Historia técnica" más abajo para el detalle de cómo se consiguió la API key de Weather Underground sin tener una estación física propia.

## Arquitectura

```
Scrapers (GitHub Actions, cron horario)
    ↓ (Cloudflare D1 HTTP API)
Cloudflare D1 — tabla unificada "mediciones"
    ↓ (consultada por)
Worker "ema-saladillo-api" (Cloudflare Workers)
    ↓ (mismo formato de consulta que antes usaba PostgREST/Supabase)
Dashboard HTML estático (Cloudflare Pages) — app.lemeit.ar/emas
```

Hasta agosto de 2026 la base de datos era Supabase (PostgreSQL), con 4 tablas separadas (una por estación). Se migró todo el historial (~30.200 filas) a una tabla D1 unificada, y se agregó el Worker como capa de compatibilidad para no tener que reescribir el dashboard. Ver `worker/` y `d1/schema.sql`, y "Historia técnica" abajo para el resto de la evolución (dominio único, API pública, quinta estación).

## Historia técnica

**Del Programador de Tareas de Windows a GitHub Actions (marzo 2026)**: el sistema original de scraping dependía de una PC física con el Programador de Tareas de Windows — si se apagaba, se perdían datos. Se migró a GitHub Actions (cron horario), sin depender de ningún equipo prendido. La causa de que las tareas de Windows fallaran en ese contexto era un `PATH` sin la instalación de Python del usuario.

**De Supabase a Cloudflare D1 (agosto 2026)**: hasta entonces la base era Supabase (PostgreSQL), con 4 tablas separadas (una por estación). Se migró todo el historial (~30.200 filas, 9 descartadas por un timestamp corrupto de un error de OCR histórico) a una tabla D1 unificada (`mediciones`, columna `estacion`), como parte de la armonización de infraestructura entre los tres portales de la red. El Worker de compatibilidad (`ema-saladillo-api`) expone las mismas rutas y forma de consulta que usaba Supabase/PostgREST, así el dashboard no necesitó reescribirse.

**Quinta estación vía Weather Underground (septiembre 2026)**: se sumó EMA-25C, la primera estación de la red fuera de Saladillo — en el partido vecino de 25 de Mayo. A diferencia de las otras 4, no es una estación propia del proyecto: es la estación pública "25Clima" (operada por N-TecLab/SS Desarrollos), consultada vía la API pública de Weather Underground. Conseguir una API key propia de Weather Underground resultó tener un requisito no documentado: solo se genera si la cuenta tiene al menos un dispositivo "activo" (con datos reales recientes) — sin tener una estación física propia, se resolvió activando un dispositivo placeholder en WU con datos reales de un sensor PurpleAir ya existente en la red de Monitoreo Ambiental Escolar (ver `tools/subir_a_wu.py`).

**Dominio único y API pública bajo `api.lemeit.ar/emas` (septiembre 2026)**: como parte de unificar los 3 portales bajo `app.lemeit.ar` (ver el repo [`gateway`](https://github.com/lemeit/gateway)), se descubrió que la API de EMAS nunca había funcionado realmente en `emas.lemeit.ar` — el propio `index.html` y `api.html` siempre apuntaron directo al `*.workers.dev` del Worker, sin que ese dominio custom tuviera una Route configurada. Se agregó la Route de Cloudflare `api.lemeit.ar/emas/*` (con el prefijo `/emas` despojado en la primera línea del `fetch()` del Worker) para que la URL documentada funcione de verdad, sin tocar el `*.workers.dev` original (que sigue andando igual, como respaldo).

**El gotcha del DNS al sacar el Custom Domain (septiembre 2026)**: al migrar `emas.lemeit.ar` de Custom Domain de Pages a la Route del gateway, el subdominio quedó sin resolver (`NXDOMAIN`) — sacarle el Custom Domain a un proyecto de Pages no solo libera el hostname, también borra el registro DNS que Cloudflare había creado para él. Se resuelve con un registro `A` dummy (`192.0.2.1`, Proxied) agregado a mano — mismo patrón documentado en el README de [`gateway`](https://github.com/lemeit/gateway).

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
                    # (EMA-25C). Se genera gratis en wunderground.com — no es la key
                    # embebida en 25clima.ar, esa no se usa acá. Ver "Historia técnica"
                    # arriba para el paso extra que hizo falta (dispositivo placeholder).
```

`supabase_ping.py` sigue usando `SUPA_URL` / `SUPA_KEY` por separado — no tiene relación con EMA, solo mantiene activo el proyecto Supabase compartido con otras apps.

## Dashboard

El archivo `index.html` (raíz del repo) es un single-file HTML estático que consulta el Worker de Cloudflare vía REST. No requiere backend propio. Deployado en Cloudflare Pages (`wrangler pages deploy`).

**Logos de proveedor por estación** (agosto 2026): el footer muestra el logo del proveedor de la red/hardware de la estación seleccionada (o los 4 juntos en la vista "⇌ Comparativa") — TECMES para EMA-EET (deliberadamente el proveedor de la red RMET/SNIH, no la institución que aloja la estación), CFR Saladillo, Defensa Civil Saladillo y Clima Saladillo para las otras tres. Los mismos 4 logos también aparecen en el panel "Acerca de" (sección "📍 Estaciones de la red"), por ahora — más adelante podrían reemplazarse por fotos reales de cada estación/monitor. El tamaño de los logos del footer usa `logoSize: "md"` (20px) en vez del `"sm"` (15px) por defecto del footer compartido — ver `lemeit-design`.

**Fix: logo de EMA-25C desalineaba la fila en "Acerca de" (septiembre 2026)**: el logo de 25Clima es más ancho que los otros 4 — la caja que lo contiene (`.about-est-logo`) solo tenía el alto fijo (`height:22px`, ancho libre según la imagen), así que esa fila quedaba más ancha que el resto y se notaba desalineada, sobre todo en celular. Se le da a la caja tamaño fijo (ancho y alto) para las 5 estaciones, con cada logo achicándose (`object-fit:contain`) para entrar sin recortarse.

**Color de marca e ícono propio (septiembre 2026)**: el favicon (`#5B8DEF`, azul suelto) se reemplaza por un celeste de la misma familia tonal que el verde de Aire Escolar (`#0097A7`, token `--lm-site-emas` en `lemeit-design/lemeit-theme.css`), aplicado también al chip del header.

**Header mobile — selector de estaciones a su propia fila (septiembre 2026)**: el selector de 5 estaciones + botón de comparativa compartía fila con el estado de conexión, el switcher de portales y el botón de tema — en pantallas chicas esa fila no entraba y, al no poder achicarse, terminaba tapando o empujando controles fuera de pantalla. Pasa a su propia fila completa debajo del header en mobile, con scroll horizontal.

**Mapa**: los tiles se piden al propio Worker (`GET /tiles/:style/:z/:x/:y{@2x}.png`, `style` = `light_all` \| `dark_all`), que actúa de proxy hacia CARTO Basemaps agregando la API key del secret `CARTO_API_KEY` del lado del servidor — así la key nunca queda expuesta en el HTML público. Configurar con `npx wrangler secret put CARTO_API_KEY` desde `worker/`. Key gratuita (tope 5M tiles/mes) en [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey).

## Base de datos (Cloudflare D1)

Base: `ema-saladillo-db` — tabla unificada `mediciones` (columna `estacion` distingue EMA-EET/CFR/DC/CS). Ver `d1/schema.sql` para el esquema completo, incluyendo el índice único que evita filas duplicadas.

El Worker `worker/src/index.js` expone rutas compatibles con el formato PostgREST que usaba el dashboard (`mediciones_ema`, `mediciones_cfr`, `mediciones_dc`, `mediciones_cs`, `v_ema_armonizada`, `v_temperatura_comparativa`), calculadas sobre la tabla unificada.

**API pública (agosto 2026)**: las mismas rutas de arriba están pensadas para que cualquiera las consuma directo — CORS abierto, sin autenticación ni token, son de solo lectura. Todas aceptan `desde`/`hasta` (`YYYY-MM-DD[ HH:MM:SS]`, UTC) como rango de fechas absoluto (pisa a `horas` si viene alguno de los dos) y `&formato=csv` para bajar CSV en vez de JSON. Documentación con ejemplos: [`app.lemeit.ar/emas/api.html`](https://app.lemeit.ar/emas/api.html) (fuente: `api.html` en la raíz de este repo).

## Proyecto educativo

Red de estaciones meteorológicas automáticas de Saladillo y 25 de Mayo, con fines de monitoreo ambiental y educación ambiental ciudadana.
Ing. Luciano Lamaita — docente de Física y Química en Saladillo, Buenos Aires — más proyectos y materiales en [profe.lemeit.ar](https://profe.lemeit.ar)

## Roadmap / bitácora de ideas

Ideas pendientes de evaluar e implementar, anotadas para no perderlas entre sesiones:

- ~~**Migrar de Supabase a Cloudflare D1**~~ — implementado en agosto 2026, ver "Historia técnica" arriba.
- ~~**API pública bajo dominio propio (`api.lemeit.ar/emas`)**~~ — implementado en septiembre 2026, ver "Historia técnica" arriba.
- ~~**Quinta estación (EMA-25C, 25 de Mayo)**~~ — implementado en septiembre 2026. Sigue pendiente confirmar con el operador de 25Clima su carácter permanente en el dashboard.
- **Reportes combinados EMA + Monitoreo Ambiental Escolar, con análisis espacial**: la meta de fondo del proyecto — hoy limitada porque las estaciones EMA y los sensores de aire no están co-ubicados geográficamente. El primer análisis de este tipo, con los datos iniciales de las 4 estaciones originales, encontró un efecto de isla de calor urbano nocturno de +2 a +4°C en EMA-CS (zona urbana) respecto a las estaciones periurbanas/rurales — evaluado, sin una integración formal implementada todavía.
- **Backend propio para `lemeit-wq`**: no es parte de este repo, pero mismo patrón D1 + Worker ya probado acá y en `lemeit-aq`, pendiente de replicar ahí para que las 3 redes tengan la misma arquitectura.

## Contexto institucional y proyectos futuros

Notas para retomar en próximas sesiones de desarrollo (no son parte de la funcionalidad actual del dashboard):

- EMAS es uno de tres proyectos hermanos de la misma iniciativa de ciencia ciudadana ambiental del autor en instituciones educativas de Saladillo y la región — junto con Monitoreo Ambiental Escolar (calidad de aire) y Calidad del Agua Saladillo.
- El autor (Ing. Luciano Lamaita, Ing. Químico) es Embajador Comunitario de OpenAQ (2023), integra el Grupo de Trabajo de Air Quality de la ECSA (European Citizen Science Association) y participa de los proyectos CanAirIO, AireCiudadano y Sensor.Community.
- El autor trabajó anteriormente en el **Ministerio de Ambiente de la Provincia de Buenos Aires**, y mantiene buena sinergia y contacto con el **CEMCA** (Centro de Monitoreo de Calidad de Aire), un área de ese mismo Ministerio, con intención de seguir trabajando en conjunto a futuro.
- Al retomar este tema, conviene revisar si hay wiki, contactos o documentación adicional que el autor quiera sumar antes de planificar la integración espacial EMA + AQ.

## Licencia

Datos meteorológicos: Creative Commons (EMA-EET/SNIH), uso público (EMA-CFR, EMA-DC, EMA-CS).  
Código: MIT.

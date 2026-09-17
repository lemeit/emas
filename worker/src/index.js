/**
 * API para el dashboard de EMA Saladillo, sobre Cloudflare D1.
 *
 * Compatibilidad: expone las mismas rutas y forma de consulta que el
 * dashboard ya usaba contra Supabase/PostgREST (select=, order=, limit=,
 * columna=eq.valor), así que index.html no necesita reescribirse — solo
 * apuntar SUPA_URL a esta API. Internamente las 4 tablas viejas
 * (mediciones_ema/cfr/dc/cs) viven unificadas en una sola tabla D1
 * `mediciones` con columna `estacion`.
 *
 * Rutas (API pública, de solo lectura — sin autenticación, CORS abierto):
 *   GET /rest/v1/mediciones_ema | mediciones_cfr | mediciones_dc | mediciones_cs | mediciones_25c
 *       ?select=...&order=campo.asc|desc&limit=N&codigo=eq.X&parametro=eq.X&horas=N
 *       ?desde=YYYY-MM-DD[ HH:MM:SS]&hasta=...  (rango de fechas absoluto en UTC,
 *        pisa a "horas" si viene alguno de los dos)
 *       &formato=csv  (devuelve CSV en vez de JSON)
 *   GET /rest/v1/v_temperatura_comparativa?select=hora,eet,cfr,dc,cs,m25&order=hora.asc&limit=N&horas=N&formato=csv
 *   GET /rest/v1/v_ema_armonizada?select=hora,estacion,valor&parametro=eq.CANON&order=hora.asc&limit=N&horas=N&formato=csv
 * Documentación con ejemplos: /api.html en emas.lemeit.ar
 *
 * "horas" (opcional, en las 3 rutas): filtra por ventana de calendario real
 * (fecha_hora_utc >= ahora - N horas) en vez de por cantidad de filas. Lo usan
 * los selectores "48 h / 7 días / 30 días" del dashboard — sin esto, una
 * estación con huecos de datos puede devolver "las últimas N filas que
 * existan" aunque eso signifique remontarse meses atrás. `limit` sigue
 * aplicando como tope de fila cuando no se manda "horas" (comportamiento
 * legado, para no romper otros consumidores de la API).
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, apikey, Authorization",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...CORS_HEADERS },
  });
}

// ── Exportación CSV ─────────────────────────────────────────────────────────
// Las columnas del CSV se toman de las claves del primer objeto del array
// (mismas claves que ya arma buildRow()/los handlers de vistas), así que
// nunca se desincroniza de lo que cada ruta ya devuelve en JSON.
function toCsv(rows) {
  if (!rows.length) return "";
  const headers = Object.keys(rows[0]);
  const escape = (v) => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers.join(",")];
  for (const r of rows) lines.push(headers.map((h) => escape(r[h])).join(","));
  return lines.join("\n");
}

function csvResponse(rows, filename) {
  return new Response(toCsv(rows), {
    status: 200,
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename}"`,
      ...CORS_HEADERS,
    },
  });
}

function wantsCsv(params) {
  return (params.get("formato") || "").toLowerCase() === "csv";
}

// ── Proxy de tiles del mapa (CARTO Basemaps) ────────────────────────────────
// CARTO ahora exige una API key para servir tiles. En vez de poner esa key
// en el HTML público (cualquiera podría copiarla de "ver código fuente" y
// gastar la cuota gratuita de 5M tiles/mes), el frontend le pide los tiles a
// este Worker y es el Worker quien agrega la key en el secret CARTO_API_KEY
// (`wrangler secret put CARTO_API_KEY`, nunca en este archivo ni en
// wrangler.toml) al pedirle el tile a CARTO. Así la key nunca queda expuesta
// del lado del cliente.
const TILE_STYLES = new Set(["light_all", "dark_all"]);
const TILE_SUBDOMAINS = "abcd";

async function proxyCartoTile(env, style, z, x, y, retina) {
  if (!TILE_STYLES.has(style)) return json({ error: "Estilo de tile inválido" }, 400);
  if (!env.CARTO_API_KEY) return json({ error: "Falta configurar el secret CARTO_API_KEY" }, 500);
  const sub = TILE_SUBDOMAINS[Math.floor(Math.random() * TILE_SUBDOMAINS.length)];
  const cartoUrl = `https://${sub}.basemaps.cartocdn.com/${style}/${z}/${x}/${y}${retina}.png?key=${env.CARTO_API_KEY}`;
  const resp = await fetch(cartoUrl, { cf: { cacheTtl: 604800, cacheEverything: true } });
  if (!resp.ok) return new Response("Error obteniendo el tile de CARTO", { status: resp.status });
  const headers = new Headers();
  headers.set("Content-Type", resp.headers.get("Content-Type") || "image/png");
  headers.set("Cache-Control", "public, max-age=604800");
  headers.set("Access-Control-Allow-Origin", "*");
  return new Response(resp.body, { status: 200, headers });
}

// tabla vieja de Supabase -> código de estación en la tabla unificada `mediciones`
const TABLE_MAP = {
  mediciones_ema: "EMA-EET",
  mediciones_cfr: "EMA-CFR",
  mediciones_dc: "EMA-DC",
  mediciones_cs: "EMA-CS",
  mediciones_25c: "EMA-25C",
};

// nombre de estación que usaba la vista v_ema_armonizada (distinto del código interno)
const ARMONIZADA_NOMBRE = {
  "EMA-EET": "EET N°1",
  "EMA-CFR": "CFR",
  "EMA-DC": "DC",
  "EMA-CS": "CS",
  "EMA-25C": "25 de Mayo",
};

// parámetro canónico (el que usaba v_ema_armonizada/v_temperatura_comparativa)
// -> identificador propio de cada estación (codigo numérico para EET, texto para el resto)
const CANON_MAP = {
  Temperatura: { eet: 14, cfr: "Temperatura", dc: "Temperatura", cs: "Temperatura", m25: "Temperatura" },
  Humedad: { eet: 18, cfr: "Humedad", dc: "Humedad", cs: "Humedad", m25: "Humedad" },
  Presion: { eet: 218, cfr: "Presion Barometrica", dc: "Presion", cs: "Presion", m25: "Presion" },
  "Velocidad Viento": { eet: 4, cfr: "Velocidad del Viento", dc: "Velocidad Viento", cs: "Velocidad Viento", m25: "Velocidad Viento" },
  Rafaga: { eet: null, cfr: null, dc: "Rafaga", cs: "Rafaga", m25: "Rafaga" },
  "Direccion Viento": { eet: 5, cfr: "Direccion del Viento", dc: "Direccion Viento", cs: null, m25: "Direccion del Viento" },
  Lluvia: { eet: 20, cfr: "Lluvia Diaria", dc: "Lluvia Acumulada", cs: "Lluvia Diaria", m25: "Lluvia Diaria" },
  "Punto de Rocio": { eet: null, cfr: "Punto de Rocio", dc: "Punto de Rocio", cs: "Punto de Rocio", m25: "Punto de Rocio" },
  "Radiacion Solar": { eet: null, cfr: "Radiacion Solar", dc: null, cs: "Radiacion Solar", m25: "Radiacion Solar" },
};

function pad(n) {
  return String(n).padStart(2, "0");
}

// 'YYYY-MM-DD HH:MM:SS' (UTC) -> 'DD/MM/YYYY HH:MM' (hora Argentina, UTC-3, sin horario de verano)
function utcAFechaAr(utcStr) {
  if (!utcStr) return null;
  const [datePart, timePart] = utcStr.split(" ");
  const [y, m, d] = datePart.split("-").map(Number);
  const [hh, mm] = (timePart || "00:00:00").split(":").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d, hh, mm));
  dt.setUTCHours(dt.getUTCHours() - 3);
  return `${pad(dt.getUTCDate())}/${pad(dt.getUTCMonth() + 1)}/${dt.getUTCFullYear()} ${pad(dt.getUTCHours())}:${pad(dt.getUTCMinutes())}`;
}

function buildRow(r, selectFields) {
  const full = {
    codigo: r.codigo,
    parametro: r.parametro,
    unidad: r.unidad,
    valor: r.valor,
    valor_texto: r.valor_texto,
    fecha_hora_ar: utcAFechaAr(r.fecha_hora_utc),
    fecha_hora_utc: r.fecha_hora_utc,
    insertado_en: r.fecha_hora_utc, // alias: mismo instante, ya no distinguimos hora de inserción
  };
  if (!selectFields || selectFields.length === 0) return full;
  const out = {};
  for (const f of selectFields) out[f] = full[f];
  return out;
}

function parseOrderDir(orderParam) {
  const [, dir] = (orderParam || "").split(".");
  return dir && dir.toLowerCase() === "asc" ? "ASC" : "DESC";
}

async function handleMediciones(env, tabla, params) {
  const estacion = TABLE_MAP[tabla];
  const selectFields = (params.get("select") || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const dir = parseOrderDir(params.get("order"));
  const limit = Math.min(parseInt(params.get("limit") || "100", 10) || 100, 5000);

  let where = "estacion = ?";
  const binds = [estacion];

  const codigoEq = params.get("codigo");
  if (codigoEq && codigoEq.startsWith("eq.")) {
    where += " AND codigo = ?";
    binds.push(Number(codigoEq.slice(3)));
  }
  const parametroEq = params.get("parametro");
  if (parametroEq && parametroEq.startsWith("eq.")) {
    where += " AND parametro = ?";
    binds.push(decodeURIComponent(parametroEq.slice(3)));
  }

  // Rango de fechas absoluto en UTC (ej. ?desde=2026-08-01&hasta=2026-08-15),
  // pensado para quien quiera bajar un período puntual en vez de una ventana
  // relativa a "ahora". Si viene desde y/o hasta, pisa a "horas".
  const desde = params.get("desde");
  const hasta = params.get("hasta");
  if (desde) { where += " AND fecha_hora_utc >= ?"; binds.push(desde); }
  if (hasta) { where += " AND fecha_hora_utc <= ?"; binds.push(hasta); }

  // "horas": ventana de calendario real (ej. últimas 720 horas = últimos 30
  // días), en vez de "las últimas N filas que existan" — con estaciones que
  // tienen huecos de datos, "últimas N filas" puede terminar trayendo datos
  // de varios meses atrás en vez de del rango pedido. Solo aplica si no se
  // especificó un rango de fechas absoluto.
  const horas = parseInt(params.get("horas") || "", 10);
  if (!desde && !hasta && horas > 0) {
    where += " AND fecha_hora_utc >= datetime('now', '-' || ? || ' hours')";
    binds.push(horas);
  }

  const sql = `SELECT codigo, parametro, unidad, valor, valor_texto, fecha_hora_utc
               FROM mediciones WHERE ${where} ORDER BY fecha_hora_utc ${dir} LIMIT ?`;
  binds.push(limit);

  const { results } = await env.DB.prepare(sql).bind(...binds).all();
  const filas = results.map((r) => buildRow(r, selectFields));
  if (wantsCsv(params)) return csvResponse(filas, `${tabla}.csv`);
  return json(filas);
}

async function handleTemperaturaComparativa(env, params) {
  const limit = Math.min(parseInt(params.get("limit") || "500", 10) || 500, 20000);
  // "horas": ventana de calendario real (ver handleMediciones). Cuando viene
  // presente, filtra por fecha en vez de "las últimas N horas que existan" —
  // así una estación con huecos de datos no termina trayendo meses de atrás.
  const horasVentana = parseInt(params.get("horas") || "", 10);
  const stations = [
    ["EMA-EET", "eet", "codigo", 14],
    ["EMA-CFR", "cfr", "parametro", "Temperatura"],
    ["EMA-DC", "dc", "parametro", "Temperatura"],
    ["EMA-CS", "cs", "parametro", "Temperatura"],
    ["EMA-25C", "m25", "parametro", "Temperatura"],
  ];

  const porEstacion = {};
  for (const [estacion, clave, columna, valor] of stations) {
    let sql, binds;
    if (horasVentana > 0) {
      sql = `SELECT strftime('%Y-%m-%d %H', fecha_hora_utc) AS hora, AVG(valor) AS valor
             FROM mediciones
             WHERE estacion = ? AND ${columna} = ?
               AND fecha_hora_utc >= datetime('now', '-' || ? || ' hours')
             GROUP BY hora ORDER BY hora ASC`;
      binds = [estacion, valor, horasVentana];
    } else {
      // Sin "horas": traer solo las horas MÁS RECIENTES por estación (antes
      // se agrupaba TODO el historial y se tomaban las primeras `limit`
      // horas del conjunto ordenado ascendente, que siempre eran las más
      // viejas — ver commit anterior).
      sql = `SELECT hora, valor FROM (
               SELECT strftime('%Y-%m-%d %H', fecha_hora_utc) AS hora, AVG(valor) AS valor
               FROM mediciones WHERE estacion = ? AND ${columna} = ?
               GROUP BY hora ORDER BY hora DESC LIMIT ?
             ) ORDER BY hora ASC`;
      binds = [estacion, valor, limit];
    }
    const { results } = await env.DB.prepare(sql).bind(...binds).all();
    porEstacion[clave] = {};
    for (const r of results) porEstacion[clave][r.hora] = r.valor;
  }

  const todasHoras = [
    ...new Set([
      ...Object.keys(porEstacion.eet),
      ...Object.keys(porEstacion.cfr),
      ...Object.keys(porEstacion.dc),
      ...Object.keys(porEstacion.cs),
      ...Object.keys(porEstacion.m25),
    ]),
  ].sort();

  // Sin "horas" (fallback legado): nos quedamos con las `limit` horas más
  // recientes del conjunto combinado. Con "horas", ya viene acotado por fecha.
  const horasFinales = horasVentana > 0 ? todasHoras : todasHoras.slice(-limit);
  const filas = horasFinales.map((h) => ({
    hora: h,
    eet: porEstacion.eet[h] ?? null,
    cfr: porEstacion.cfr[h] ?? null,
    dc: porEstacion.dc[h] ?? null,
    cs: porEstacion.cs[h] ?? null,
    m25: porEstacion.m25[h] ?? null,
  }));
  if (wantsCsv(params)) return csvResponse(filas, "temperatura_comparativa.csv");
  return json(filas);
}

async function handleArmonizada(env, params) {
  const parametroEq = params.get("parametro");
  const canon = parametroEq && parametroEq.startsWith("eq.") ? decodeURIComponent(parametroEq.slice(3)) : null;
  const limit = Math.min(parseInt(params.get("limit") || "2000", 10) || 2000, 40000);
  // "horas": ventana de calendario real (ver handleMediciones).
  const horasVentana = parseInt(params.get("horas") || "", 10);
  const mapa = canon ? CANON_MAP[canon] : null;
  if (!mapa) return json([]);

  const stations = [
    ["EMA-EET", "codigo", mapa.eet],
    ["EMA-CFR", "parametro", mapa.cfr],
    ["EMA-DC", "parametro", mapa.dc],
    ["EMA-CS", "parametro", mapa.cs],
    ["EMA-25C", "parametro", mapa.m25],
  ];

  let filas = [];
  for (const [estacion, columna, valor] of stations) {
    if (valor === null || valor === undefined) continue;
    let sql, binds;
    if (horasVentana > 0) {
      sql = `SELECT strftime('%Y-%m-%d %H', fecha_hora_utc) AS hora, AVG(valor) AS valor, COUNT(*) AS n_registros
             FROM mediciones
             WHERE estacion = ? AND ${columna} = ?
               AND fecha_hora_utc >= datetime('now', '-' || ? || ' hours')
             GROUP BY hora ORDER BY hora ASC`;
      binds = [estacion, valor, horasVentana];
    } else {
      // Sin "horas" (fallback legado): traer solo las horas MÁS RECIENTES
      // por estación (antes traía todo el historial agrupado y cortaba las
      // primeras `limit` filas del total combinado — que, al estar ordenado
      // ascendente, eran siempre las más viejas — ver commit anterior).
      sql = `SELECT hora, valor, n_registros FROM (
               SELECT strftime('%Y-%m-%d %H', fecha_hora_utc) AS hora, AVG(valor) AS valor, COUNT(*) AS n_registros
               FROM mediciones WHERE estacion = ? AND ${columna} = ?
               GROUP BY hora ORDER BY hora DESC LIMIT ?
             ) ORDER BY hora ASC`;
      binds = [estacion, valor, limit];
    }
    const { results } = await env.DB.prepare(sql).bind(...binds).all();
    for (const r of results) {
      filas.push({ hora: r.hora, estacion: ARMONIZADA_NOMBRE[estacion], valor: r.valor, n_registros: r.n_registros });
    }
  }
  filas.sort((a, b) => (a.hora < b.hora ? -1 : a.hora > b.hora ? 1 : 0));
  if (wantsCsv(params)) return csvResponse(filas, "ema_armonizada.csv");
  return json(filas);
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);

    // api.lemeit.ar/emas/* (Route de Cloudflare, ver worker/wrangler.toml)
    // llega con el prefijo /emas incluido — se lo sacamos acá, una sola
    // vez, para que el resto del código no sepa ni le importe si vino por
    // ahí o por el *.workers.dev directo (sin /emas), que sigue andando
    // igual sin tocar nada.
    let path = url.pathname;
    if (path.startsWith("/emas/")) path = path.slice(5);

    const tileMatch = path.match(/^\/tiles\/(light_all|dark_all)\/(\d+)\/(-?\d+)\/(-?\d+)(@2x)?\.png$/);
    if (tileMatch) {
      const [, style, z, x, y, retina] = tileMatch;
      return proxyCartoTile(env, style, z, x, y, retina || "");
    }

    const match = path.match(/^\/rest\/v1\/([a-z0-9_]+)$/);
    if (!match) return json({ error: "Not found" }, 404);

    const tabla = match[1];
    const params = url.searchParams;

    try {
      if (tabla === "v_temperatura_comparativa") return await handleTemperaturaComparativa(env, params);
      if (tabla === "v_ema_armonizada") return await handleArmonizada(env, params);
      if (TABLE_MAP[tabla]) return await handleMediciones(env, tabla, params);
      return json({ error: `tabla desconocida: ${tabla}` }, 404);
    } catch (err) {
      return json({ error: err.message }, 500);
    }
  },
};

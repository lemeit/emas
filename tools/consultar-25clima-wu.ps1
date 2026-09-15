<#
  Consulta de prueba/análisis para ver qué datos se pueden tomar de la estación
  "25Clima" (25 de Mayo, Bs As, https://25clima.ar/) para eventualmente sumarla
  a EMA Saladillo como una 5ta estación (partido de 25 de Mayo).

  QUÉ ENCONTRÉ (inspeccionando el sitio, no hice scraping del HTML):
  - 25clima.ar NO tiene una API propia pública. Los datos que muestra su
    dashboard vienen de Weather Underground (WU / api.weather.com), la
    estación está registrada ahí como PWS con ID: IDEMAY14
    (ver https://www.wunderground.com/dashboard/pws/IDEMAY14).
  - Coordenadas de la estación: -35.44, -60.17 (25 de Mayo, Bs As), elevación
    ~16 m. Se puede confirmar/afinar contactando al operador (ver más abajo).
  - Variables que expone (mismos nombres que ya usa este proyecto en EMA):
    temperatura, sensación térmica, punto de rocío, humedad, presión,
    velocidad/ráfaga/dirección de viento, lluvia (diaria/mensual/anual).
    Actualización cada 5 min según el propio sitio.
  - El frontend de 25clima.ar SÍ tiene una API key de Weather Underground
    embebida en su JS para pedirle los datos a WU directamente desde el
    navegador del visitante — no la copié ni la uso acá. No corresponde
    reusar la key de otro sitio (aparte de no ser buena práctica, WU liga
    esas keys a la cuenta que las generó).

  CÓMO CONSEGUIR ACCESO PROPIO (gratis, sin necesitar una estación física):
  1. Crear una cuenta en https://www.wunderground.com/
  2. En el perfil, la sección de "estación personal" permite cargar una PWS
     aunque no sea una estación real tuya (WU lo aclara: esa PWS "de mentira"
     no da datos válidos SI la consultás a ella misma — pero sirve para que
     el sistema te habilite una API key).
  3. Esa cuenta te da acceso a "API Keys", con una key que SÍ sirve para
     consultar OTRAS estaciones públicas de la red WU (como IDEMAY14) — no
     solo la tuya.

  MEJOR AÚN: como esto es para sumar la estación de otra persona/operador a
  tu propia red (EMA Saladillo), tiene sentido contactar a quien mantiene
  25Clima (pie de página: "N-TecLab" / "SS Desarrollos") y avisarle / pedirle
  el OK — mismo espíritu de colaboración institucional que ya tenés
  documentado con CEMCA/OpenAQ en el README de purpleair-saladillo. Aparte
  de ser correcto, evita que el día de mañana cambien de PWS ID o de
  proveedor y tu ingesta se rompa sin aviso.

  Uso (una vez que tengas tu propia API key de WU):
    $env:WU_API_KEY = "tu_clave"
    .\consultar-25clima-wu.ps1                    # condiciones actuales
    .\consultar-25clima-wu.ps1 -Modo dia           # lecturas del día (cada ~5 min)
    .\consultar-25clima-wu.ps1 -Modo historico -Fecha 2026-09-14   # un día específico

  También podés pasar la key con -Key en vez de la variable de entorno, o
  dejarla puesta una vez en la sesión de PowerShell así no la volvés a tipear.
#>
param(
    [ValidateSet("actual", "dia", "historico")]
    [string]$Modo = "actual",

    [string]$StationId = "IDEMAY14",

    [string]$Fecha,   # solo para -Modo historico, formato AAAA-MM-DD

    [string]$Key = $env:WU_API_KEY
)

if (-not $Key) {
    $secure = Read-Host "API key de Weather Underground" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $Key = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$Base = "https://api.weather.com/v2/pws"

switch ($Modo) {
    "actual" {
        $url = "$Base/observations/current?stationId=$StationId&format=json&units=m&numericPrecision=decimal&apiKey=$Key"
    }
    "dia" {
        # Todas las lecturas del día en curso (cada ~5 min, según reporte la estación)
        $url = "$Base/observations/all/1day?stationId=$StationId&format=json&units=m&numericPrecision=decimal&apiKey=$Key"
    }
    "historico" {
        if (-not $Fecha) {
            Write-Host "Con -Modo historico hace falta -Fecha AAAA-MM-DD" -ForegroundColor Red
            exit 1
        }
        $fechaCompacta = (Get-Date $Fecha).ToString("yyyyMMdd")
        $url = "$Base/history/all?stationId=$StationId&format=json&units=m&date=$fechaCompacta&numericPrecision=decimal&apiKey=$Key"
    }
}

try {
    $resp = Invoke-RestMethod -Uri $url -ErrorAction Stop
} catch {
    Write-Host "Error consultando la API de Weather Underground: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "(si da 401/403, la key todavía no está activa o el stationId no es válido/público)" -ForegroundColor Yellow
    exit 1
}

if (-not $resp.observations -or $resp.observations.Count -eq 0) {
    Write-Host "La API respondió pero sin lecturas (observations vacío) — la estación puede estar offline en este momento." -ForegroundColor Yellow
    $resp | ConvertTo-Json -Depth 6
    exit 0
}

Write-Host ""
Write-Host "Estación: $StationId — $($resp.observations.Count) lectura(s)" -ForegroundColor Cyan
Write-Host ""

foreach ($obs in $resp.observations) {
    [PSCustomObject]@{
        hora           = $obs.obsTimeLocal
        lat            = $obs.lat
        lon            = $obs.lon
        elevacion_m    = $obs.elev
        temp_C         = $obs.metric.temp
        sensacion_C    = $obs.metric.heatIndex
        rocio_C        = $obs.metric.dewpt
        humedad_pct    = $obs.humidity
        presion_hPa    = $obs.metric.pressure
        viento_kmh     = $obs.metric.windSpeed
        rafaga_kmh     = $obs.metric.windGust
        direccion_grados = $obs.winddir
        lluvia_dia_mm  = $obs.metric.precipTotal
    }
}

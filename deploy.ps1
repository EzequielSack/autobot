# deploy.ps1 — Build, empaqueta y publica AUTOBOT en GitHub Releases
# Uso: .\deploy.ps1 -Token "ghp_TU_TOKEN"
param(
    [Parameter(Mandatory=$true)]
    [string]$Token
)

$ErrorActionPreference = "Stop"
$repo = "EzequielSack/autobot"
$root = $PSScriptRoot

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "    ERROR: $msg" -ForegroundColor Red; exit 1 }

# ── 1. Leer version ───────────────────────────────────────────────────────────
Write-Step "Leyendo VERSION.txt"
$versionFile = Join-Path $root "VERSION.txt"
if (-not (Test-Path $versionFile)) { "1.0" | Set-Content $versionFile -Encoding utf8 }
$version = (Get-Content $versionFile -Raw).Trim()
Write-Ok "Version actual: $version"

# ── 2. Compilar con PyInstaller ───────────────────────────────────────────────
Write-Step "Compilando ejecutable con PyInstaller"
$specFile = Join-Path $root "AUTOBOT.spec"
if (-not (Test-Path $specFile)) { Write-Fail "No se encontro AUTOBOT.spec en $root" }

Push-Location $root
try {
    py -m PyInstaller --noconfirm $specFile
    if ($LASTEXITCODE -ne 0) { Write-Fail "PyInstaller fallo (exit $LASTEXITCODE)" }
} finally { Pop-Location }

$exeSrc = Join-Path $root "dist\AUTOBOT.exe"
if (-not (Test-Path $exeSrc)) { Write-Fail "dist\AUTOBOT.exe no encontrado tras compilacion" }
Write-Ok "Ejecutable generado: $exeSrc ($([math]::Round((Get-Item $exeSrc).Length/1MB,1)) MB)"

# ── 3. Copiar exe y crear zip ─────────────────────────────────────────────────
Write-Step "Empaquetando ZIP para distribucion"
$publicarDir = Join-Path $root "publicar"
$exeDst      = Join-Path $publicarDir "AUTOBOT.exe"
$leemeSrc    = Join-Path $publicarDir "LEEME - INSTRUCCIONES.txt"
$zipPath     = Join-Path $publicarDir "AUTOBOT.zip"

Copy-Item $exeSrc $exeDst -Force

if (-not (Test-Path $leemeSrc)) { Write-Fail "No se encontro '$leemeSrc'" }
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

Compress-Archive -Path $exeDst, $leemeSrc -DestinationPath $zipPath -CompressionLevel Optimal
Write-Ok "ZIP creado: $zipPath ($([math]::Round((Get-Item $zipPath).Length/1MB,1)) MB)"

# ── 4. Crear GitHub Release ───────────────────────────────────────────────────
Write-Step "Creando GitHub Release v$version"
$headers = @{
    Authorization = "token $Token"
    "Content-Type" = "application/json"
    "User-Agent"   = "autobot-deploy"
}
$releaseBody = [System.Text.Encoding]::UTF8.GetBytes(
    "{`"tag_name`":`"v$version`",`"name`":`"AUTOBOT v$version`",`"body`":`"Release automatico v$version`",`"draft`":false,`"prerelease`":false}"
)

try {
    $release = Invoke-RestMethod -Method Post `
        -Uri "https://api.github.com/repos/$repo/releases" `
        -Headers $headers -Body $releaseBody
} catch {
    Write-Fail "No se pudo crear el release: $_"
}
$releaseId = $release.id
Write-Ok "Release creado: $($release.html_url)"

# ── 5. Subir AUTOBOT.zip como asset ───────────────────────────────────────────
Write-Step "Subiendo AUTOBOT.zip al release"
$zipBytes = [System.IO.File]::ReadAllBytes($zipPath)
$uploadHeaders = @{
    Authorization  = "token $Token"
    "Content-Type" = "application/zip"
    "User-Agent"   = "autobot-deploy"
}
$uploadUrl = "https://uploads.github.com/repos/$repo/releases/$releaseId/assets?name=AUTOBOT.zip"

try {
    $asset = Invoke-RestMethod -Method Post -Uri $uploadUrl -Headers $uploadHeaders -Body $zipBytes
} catch {
    Write-Fail "No se pudo subir el asset: $_"
}
Write-Ok "Asset subido: $($asset.browser_download_url)"

# ── 6. Incrementar version para el proximo deploy ─────────────────────────────
Write-Step "Incrementando version para proximo deploy"
$parts = $version -split '\.'
$parts[-1] = [string]([int]$parts[-1] + 1)
$nextVersion = $parts -join '.'
$nextVersion | Set-Content $versionFile -Encoding utf8 -NoNewline
Write-Ok "VERSION.txt actualizado: $version -> $nextVersion"

# ── 7. Resumen ────────────────────────────────────────────────────────────────
Write-Host "`n================================================" -ForegroundColor Green
Write-Host "  Deploy completado: AUTOBOT v$version" -ForegroundColor Green
Write-Host "  Release: $($release.html_url)" -ForegroundColor Green
Write-Host "  Descarga directa:" -ForegroundColor Green
Write-Host "  https://github.com/$repo/releases/latest/download/AUTOBOT.zip" -ForegroundColor Yellow
Write-Host "================================================`n" -ForegroundColor Green

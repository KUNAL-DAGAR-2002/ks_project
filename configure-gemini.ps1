$ErrorActionPreference = "Stop"
$secureKey = Read-Host "Paste your Gemini API key" -AsSecureString
$key = [System.Net.NetworkCredential]::new("", $secureKey).Password.Trim()

if (-not $key) {
    throw "Gemini API key cannot be empty."
}

$envPath = Join-Path $PSScriptRoot "backend\.env"
@(
    "KIRANA_GEMINI_API_KEY=$key"
    "KIRANA_GEMINI_MODEL=gemini-2.5-flash"
) | Set-Content -LiteralPath $envPath -Encoding UTF8

Write-Host "Gemini is configured. Restart start-backend.cmd before using AI features." -ForegroundColor Green

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$envFile = Join-Path $repoRoot ".env.public-dashboard.ps1"
$activateScript = Join-Path $repoRoot "venv\Scripts\Activate.ps1"
$appFile = Join-Path $repoRoot "hil_scheduler.py"

if (-not (Test-Path $envFile)) {
    Write-Error "Missing $envFile. Create it from $repoRoot\.env.public-dashboard.ps1.example and set credentials."
}

. $envFile

if ([string]::IsNullOrWhiteSpace($env:HIL_PUBLIC_DASH_USER) -or [string]::IsNullOrWhiteSpace($env:HIL_PUBLIC_DASH_PASS)) {
    Write-Error "HIL_PUBLIC_DASH_USER and HIL_PUBLIC_DASH_PASS must be set in $envFile."
}

if (-not (Test-Path $activateScript)) {
    Write-Error "Missing virtual environment activate script: $activateScript. Create venv and install requirements first."
}

. $activateScript
python $appFile

# 9router-manager launcher for Windows (PowerShell).
# Finds a Python 3.8+ interpreter and hands over to the engine.
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$engine = Join-Path $here "9router-manager.py"
if (-not (Test-Path $engine)) {
    Write-Error "engine tidak ditemukan: $engine"
    exit 1
}

function Test-Python([string]$exe, [string[]]$prefix) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $null }
    $cmdArgs = @()
    $cmdArgs += $prefix
    $cmdArgs += @("-c", "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)")
    try {
        & $exe @cmdArgs *> $null
        if ($LASTEXITCODE -eq 0) { return ,(@($exe) + $prefix) }
    } catch { }
    return $null
}

$python = $null
if ($env:NINEROUTER_MANAGER_PYTHON) {
    $python = @($env:NINEROUTER_MANAGER_PYTHON)
} else {
    $candidates = @(
        @("py", @("-3")),
        @("python", @()),
        @("python3", @())
    )
    foreach ($candidate in $candidates) {
        $found = Test-Python $candidate[0] $candidate[1]
        if ($found) { $python = $found; break }
    }
}

if (-not $python) {
    Write-Host "[x] Python 3.8+ tidak ditemukan di PATH." -ForegroundColor Red
    Write-Host "    Install:  winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}

$exe = $python[0]
$pre = @()
if ($python.Count -gt 1) { $pre = $python[1..($python.Count - 1)] }
& $exe @pre $engine @args
exit $LASTEXITCODE

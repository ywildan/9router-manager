# 9router-manager installer for Windows (PowerShell).
# Creates a small shim in %LOCALAPPDATA%\9router-manager\bin and tells you how to put it on PATH.
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$engine = Join-Path $here "9router-manager.py"

Write-Host "9router-manager installer"
Write-Host "-------------------------"

if (-not (Test-Path $engine)) {
    Write-Host "[x] engine tidak ditemukan: $engine" -ForegroundColor Red
    exit 1
}

$pythonExe = $null
$pythonPre = @()
foreach ($candidate in @(@("py", @("-3")), @("python", @()), @("python3", @()))) {
    $exe = $candidate[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $pre = $candidate[1]
    try {
        & $exe @pre -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" *> $null
        if ($LASTEXITCODE -eq 0) { $pythonExe = $exe; $pythonPre = $pre; break }
    } catch { }
}

if (-not $pythonExe) {
    Write-Host "[x] Python 3.8+ tidak ditemukan di PATH." -ForegroundColor Red
    Write-Host "    winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
Write-Host "[ok] python: $pythonExe $($pythonPre -join ' ')" -ForegroundColor Green

if (Get-Command 9router -ErrorAction SilentlyContinue) {
    Write-Host "[ok] 9router ditemukan di PATH" -ForegroundColor Green
} else {
    Write-Host "[!] 9router tidak ada di PATH (tool tetap jalan kalau server sudah hidup)" -ForegroundColor Yellow
}

$binDir = Join-Path $env:LOCALAPPDATA "9router-manager\bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$shim = Join-Path $binDir "9router-manager.cmd"
$lines = @("@echo off", "setlocal")
$lines += ("`"$pythonExe`" " + ($pythonPre -join " ") + " `"$engine`" %*").Trim()
$lines += "exit /b %ERRORLEVEL%"
Set-Content -Path $shim -Value $lines -Encoding ASCII
Write-Host "[ok] shim: $shim" -ForegroundColor Green

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -split ';' -contains $binDir) {
    Write-Host "[ok] $binDir sudah ada di PATH user" -ForegroundColor Green
} else {
    Write-Host "[!] tambahkan ke PATH user (sekali saja):" -ForegroundColor Yellow
    Write-Host "    setx PATH `"$userPath;$binDir`"" -ForegroundColor Yellow
}

Write-Host ""
& $pythonExe @pythonPre $engine info --no-color

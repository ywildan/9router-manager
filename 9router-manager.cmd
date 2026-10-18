@echo off
rem 9router-manager launcher for Windows (cmd.exe).
setlocal
set "HERE=%~dp0"
set "ENGINE=%HERE%9router-manager.py"

if not exist "%ENGINE%" (
  echo [x] engine tidak ditemukan: %ENGINE%
  exit /b 1
)

where py >nul 2>nul && (
  py -3 "%ENGINE%" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul && (
  python "%ENGINE%" %*
  exit /b %ERRORLEVEL%
)

echo [x] Python 3.8+ tidak ditemukan di PATH.
echo     Install: winget install Python.Python.3.12
exit /b 1

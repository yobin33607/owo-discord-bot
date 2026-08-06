@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Limey Obfuscator (Windows)
cd /d "%~dp0"
chcp 65001 >nul

color 0B

echo.
echo  [SYSTEM] Limey Obfuscation Builder (Windows)
echo.

set "PY_CMD="

call :FindPython py
if defined PY_CMD goto PythonFound

call :FindPython python
if defined PY_CMD goto PythonFound

call :FindPython python3
if defined PY_CMD goto PythonFound

echo.
echo  [X] Python 3.10+ not found.
echo  Please install it from https://www.python.org/downloads/
echo  (make sure to tick "Add Python to PATH" during install).
pause
exit /b 1

:PythonFound

echo  [OK] Using Python: %PY_CMD%

if not exist "obfuscate_limey.py" (
    echo.
    echo  [X] obfuscate_limey.py not found next to this script.
    echo  Make sure obfuscate_limey.bat stays in the Limey project folder.
    pause
    exit /b 1
)

echo.
echo  [#] Running obfuscate_limey.py %*
echo.

"%PY_CMD%" obfuscate_limey.py %*

if errorlevel 1 (
    echo.
    echo  [X] Obfuscation failed.
    pause
    exit /b 1
)

echo.
echo  [OK] Obfuscation complete.
echo  See the generated limey-obfuscated-*.zip next to this script.
echo.
pause
exit /b 0


:FindPython

where %~1 >nul 2>&1 || exit /b

for /f "tokens=2" %%V in ('%~1 --version 2^>^&1') do (
    set "VER=%%V"
)

echo !VER! | findstr /R "^3\.1[0-9]\." >nul

if not errorlevel 1 (
    set "PY_CMD=%~1"
)

exit /b

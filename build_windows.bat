@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

%PY% -m pip install --upgrade pip
if errorlevel 1 exit /b 1

%PY% -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1

%PY% -m PyInstaller --onefile --clean --name mediahider mediahider.py
if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\mediahider.exe
echo Put ffmpeg.exe and ffprobe.exe next to mediahider.exe, or make sure they are in PATH.

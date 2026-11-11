@echo off
rem ---------------------------------------------------------------------------
rem  Flow_Plot - Startskript (Windows)
rem
rem  Erster Start: legt die Python-Umgebung ".venv" im Projektordner an und
rem  installiert die Pakete aus requirements.txt. Danach wird die GUI immer mit
rem  genau dieser Umgebung gestartet. Hat sich requirements.txt geaendert (z.B.
rem  nach einem Update), werden die Pakete automatisch nachinstalliert.
rem
rem  Komplett neu einrichten: den Ordner ".venv" loeschen und erneut starten.
rem  Zusaetzliche Argumente werden an die GUI durchgereicht, z.B.:
rem      start.bat --input-dir "D:\Messungen\2026-11"
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "STAMP=%VENV%\requirements.installed"

if exist "%VENV_PY%" goto check_packages

rem --- Python suchen (bevorzugt den Python-Launcher "py") --------------------
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY goto no_python

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto old_python

%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 goto no_tkinter

echo Lege Python-Umgebung an (.venv) ...
%PY% -m venv "%VENV%"
if errorlevel 1 goto failed

:check_packages
if not exist "%STAMP%" goto install
fc /b "requirements.txt" "%STAMP%" >nul 2>&1
if errorlevel 1 goto install
goto run

:install
echo Installiere Pakete (beim ersten Mal dauert das einige Minuten) ...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto failed
copy /y "requirements.txt" "%STAMP%" >nul

:run
echo Starte Flow_Plot ...
"%VENV_PY%" "pipeline\gating_gui.py" %*
if errorlevel 1 (
    echo.
    echo Die GUI wurde mit einem Fehler beendet - siehe Meldung oben.
    pause
)
exit /b

:no_python
echo.
echo Python wurde nicht gefunden.
echo Bitte Python 3.10 oder neuer von https://www.python.org/downloads/ installieren
echo und bei der Installation "Add python.exe to PATH" anhaken. Danach erneut starten.
pause
exit /b 1

:old_python
echo.
echo Die gefundene Python-Version ist zu alt - benoetigt wird Python 3.10 oder neuer:
%PY% --version
pause
exit /b 1

:no_tkinter
echo.
echo Diese Python-Installation enthaelt kein Tkinter (wird fuer die GUI benoetigt).
echo Bitte Python von https://www.python.org/downloads/ installieren - dort ist es enthalten.
pause
exit /b 1

:failed
echo.
echo Einrichtung fehlgeschlagen - siehe Meldung oben.
echo Tipp: den Ordner ".venv" loeschen und start.bat erneut ausfuehren.
pause
exit /b 1

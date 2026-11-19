@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
if exist "dist\NekoConneViewer\NekoConneViewer.exe" goto packaged
if exist ".venv\Scripts\python.exe" goto venv
where.exe python.exe >nul 2>nul
if not errorlevel 1 goto python
where.exe py.exe >nul 2>nul
if errorlevel 1 goto missing
py.exe -3 viewer_gui.py
goto finished

:python
python.exe viewer_gui.py
goto finished

:venv
".venv\Scripts\python.exe" viewer_gui.py
goto finished

:packaged
if not defined NEKOCONNE_CACHE if exist "exports\" if not exist "exports\builtin\" set "NEKOCONNE_CACHE=%CD%\exports"
start "" "dist\NekoConneViewer\NekoConneViewer.exe"
goto finished

:missing
echo No Python found. Use NekoConneViewer.exe from the extracted Windows release.
pause
exit /b 1

:finished
set "result=%errorlevel%"
popd
if not "%result%"=="0" pause
exit /b %result%

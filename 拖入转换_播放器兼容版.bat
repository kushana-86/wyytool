@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" goto checkdeps
py -3 -m venv .venv
if not errorlevel 1 goto checkdeps
python -m venv .venv
if errorlevel 1 goto failed
:checkdeps
".venv\Scripts\python.exe" -c "import Crypto, mutagen, PIL, imageio_ffmpeg" >nul 2>&1
if not errorlevel 1 goto run
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:run
chcp 65001 >nul
".venv\Scripts\python.exe" "%~dp0ncm_to_flac.py" --compatible %*
set "NCM_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %NCM_EXIT%
:failed
echo Setup failed. Install Python 3.10+ and check your network connection.
pause
exit /b 1
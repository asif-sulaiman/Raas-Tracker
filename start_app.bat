@echo off
cd /d "%~dp0"
echo ============================================
echo   RAAS Tracker
echo ============================================
echo.
echo  Start mode:
echo    [1] Development  (Flask dev server, localhost only)
echo    [2] Production   (Waitress WSGI server, all interfaces)
echo.
set /p choice="Select (1 or 2): "

if "%choice%"=="2" (
    echo.
    echo Starting production server with Waitress...
    echo Press Ctrl+C to stop.
    echo.
    set PRODUCTION=1
    set FORCE_HTTPS=1
    waitress-serve --host=0.0.0.0 --port=5000 --threads=4 wsgi:app
) else (
    echo.
    echo Starting development server (localhost only)...
    echo Press Ctrl+C to stop.
    echo.
    set HOST=127.0.0.1
    python flask_app.py
)
pause

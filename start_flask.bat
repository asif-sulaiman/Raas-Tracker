@echo off
cd /d "O:\Study  Career\Python\ChemCalc"
if "%DATABASE_URL%"=="" (
  echo ============================================================
  echo   ERROR: DATABASE_URL is not set. The app cannot start.
  echo.
  echo   Paste this line first, with your real DB-PASSWORD, then rerun:
  echo   set "DATABASE_URL=postgresql://postgres.njanawhckyhyaduxcodr:DB-PASSWORD@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require"
  echo.
  echo   NOTE: keep the double-quotes. The password contains ^& which
  echo   breaks the command without them.
  echo.
  echo   To set it permanently instead, run once:
  echo   setx DATABASE_URL "postgresql://postgres.njanawhckyhyaduxcodr:DB-PASSWORD@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require"
  echo   ...then close and reopen this window before starting the app.
  echo ============================================================
  pause
  exit /b 1
)
echo ============================================
echo   RAAS Tracker - Flask Web App
echo   Opening browser at http://localhost:5000
echo   (keep this window open while using the app)
echo ============================================
echo.
start http://localhost:5000
python flask_app.py

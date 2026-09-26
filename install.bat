@echo off
title RAAS Tracker - Installer
color 0A

echo ============================================
echo   RAAS Tracker - Installer
echo ============================================
echo.
echo This will install RAAS Tracker on your computer.
echo.
echo Installation location: C:\ChemicalStockTracker
echo.
echo Press any key to start installation...
pause >nul

echo.
echo [1/4] Creating installation folder...
if not exist "C:\ChemicalStockTracker" mkdir "C:\ChemicalStockTracker"

echo [2/4] Copying files...
copy /Y "%~dp0dist\ChemicalStockTracker.exe" "C:\ChemicalStockTracker\" >nul

echo [3/4] Creating launcher...
(
    echo @echo off
    echo cd /d "C:\ChemicalStockTracker"
    echo start "" "C:\ChemicalStockTracker\ChemicalStockTracker.exe"
) > "C:\ChemicalStockTracker\Start.bat"

echo [4/4] Creating desktop shortcut...
(
    echo @echo off
    echo Set WshShell = CreateObject^("WScript.Shell"^)
    echo Set shortcut = WshShell.CreateShortCut^("%USERPROFILE%\Desktop\RAAS Tracker.lnk"^)
    echo shortcut.TargetPath = "C:\ChemicalStockTracker\ChemicalStockTracker.exe"
    echo shortcut.WorkingDirectory = "C:\ChemicalStockTracker"
    echo shortcut.Save
) > "%TEMP%\create_shortcut.vbs"
cscript //nologo "%TEMP%\create_shortcut.vbs" >nul
del "%TEMP%\create_shortcut.vbs"

echo.
echo ============================================
echo   Installation Complete!
echo ============================================
echo.
echo You can now:
echo   - Launch from Desktop: "RAAS Tracker"
echo   - Or run: C:\ChemicalStockTracker\Start.bat
echo.
echo Press any key to exit...
pause >nul
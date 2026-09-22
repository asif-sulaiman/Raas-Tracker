@echo off
title RAAS Tracker - Uninstaller
color 0C

echo ============================================
echo   RAAS Tracker - Uninstaller
echo ============================================
echo.
echo This will uninstall RAAS Tracker from your computer.
echo.
echo Press any key to start uninstallation...
pause >nul

echo.
echo [1/3] Removing desktop shortcut...
del "%USERPROFILE%\Desktop\RAAS Tracker.lnk" 2>nul

echo [2/3] Removing Start Menu shortcuts...
rmdir /s /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\RAAS Tracker" 2>nul

echo [3/3] Removing installation folder...
rmdir /s /q "C:\ChemicalStockTracker" 2>nul

echo.
echo ============================================
echo   Uninstallation Complete!
echo ============================================
echo.
echo RAAS Tracker has been removed from your computer.
echo.
echo Press any key to exit...
pause >nul
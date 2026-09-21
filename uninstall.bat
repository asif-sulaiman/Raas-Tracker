@echo off
title Chemical Stock Tracker - Uninstaller
color 0C

echo ============================================
echo   Chemical Stock Tracker - Uninstaller
echo ============================================
echo.
echo This will uninstall Chemical Stock Tracker from your computer.
echo.
echo Press any key to start uninstallation...
pause >nul

echo.
echo [1/3] Removing desktop shortcut...
del "%USERPROFILE%\Desktop\Chemical Stock Tracker.lnk" 2>nul

echo [2/3] Removing Start Menu shortcuts...
rmdir /s /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Chemical Stock Tracker" 2>nul

echo [3/3] Removing installation folder...
rmdir /s /q "C:\ChemicalStockTracker" 2>nul

echo.
echo ============================================
echo   Uninstallation Complete!
echo ============================================
echo.
echo Chemical Stock Tracker has been removed from your computer.
echo.
echo Press any key to exit...
pause >nul
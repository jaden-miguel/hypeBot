@echo off
title HypeBot - Live Logs
echo.
echo  ========================================
echo    HYPEBOT - Live Logs (Ctrl+C to exit)
echo  ========================================
echo.
cd /d "%~dp0"
docker logs -f hypebot
pause

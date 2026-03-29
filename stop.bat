@echo off
title HypeBot - Stopping...
echo.
echo  ========================================
echo    HYPEBOT - Shutting down...
echo  ========================================
echo.
cd /d "%~dp0"
docker-compose down
echo.
echo  Bot is STOPPED. No more alerts until you start again.
echo.
pause

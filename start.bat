@echo off
title HypeBot - Starting...
echo.
echo  ========================================
echo    HYPEBOT - Starting up...
echo  ========================================
echo.
cd /d "%~dp0"
docker-compose up -d --build
echo.
echo  Bot is RUNNING. Deals will hit your Telegram.
echo  Close this window anytime - bot keeps running.
echo.
echo  ========================================
echo    Showing live logs (Ctrl+C to stop viewing)
echo  ========================================
echo.
docker logs -f hypebot
pause

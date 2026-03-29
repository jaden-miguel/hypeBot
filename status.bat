@echo off
title HypeBot - Status
echo.
echo  ========================================
echo    HYPEBOT - Status Check
echo  ========================================
echo.
cd /d "%~dp0"
docker ps --filter "name=hypebot" --filter "name=open-webui" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo.
echo  ----------------------------------------
echo    Last 10 log lines:
echo  ----------------------------------------
echo.
docker logs hypebot --tail 10 2>nul || echo  Bot is not running.
echo.
pause

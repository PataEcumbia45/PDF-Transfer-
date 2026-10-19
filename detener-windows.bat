@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Deteniendo PDF Transfer...
docker compose down
echo Listo. La aplicacion esta apagada.
pause

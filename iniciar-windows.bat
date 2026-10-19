@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PDF Transfer
echo.
echo   ==========================================
echo     PDF Transfer - iniciando...
echo   ==========================================
echo.

docker info >nul 2>&1
if errorlevel 1 (
  echo   Docker Desktop no esta abierto o no esta instalado.
  echo.
  echo   1. Instala Docker Desktop: https://www.docker.com/products/docker-desktop/
  echo   2. Abrelo y espera a que abajo a la izquierda diga "Engine running".
  echo   3. Vuelve a hacer doble clic en este archivo.
  echo.
  pause
  exit /b 1
)

if not exist ".env" (
  copy ".env.personal" ".env" >nul
  echo   Creado el archivo de configuracion .env
)

echo   Preparando la aplicacion. La primera vez tarda varios minutos
echo   porque descarga lo necesario; las siguientes es casi inmediato.
echo.
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo   No se pudo iniciar. Revisa los mensajes de arriba.
  pause
  exit /b 1
)

echo.
echo   Esperando a que la aplicacion responda...
rem 127.0.0.1 y no "localhost": Windows prueba primero IPv6 y el contenedor escucha en IPv4.
for /l %%i in (1,1,90) do (
  curl.exe -fs -m 3 http://127.0.0.1:8000/api/health >nul 2>&1 && goto listo
  powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:8000/api/health; exit 0 } catch { exit 1 }" >nul 2>&1 && goto listo
  timeout /t 2 /nobreak >nul
)
echo   La aplicacion tarda en responder. Prueba a abrir http://localhost:8000
pause
exit /b 1

:listo
start "" http://localhost:8000
echo.
echo   Lista: http://localhost:8000
echo   Puedes cerrar esta ventana; la aplicacion sigue funcionando.
echo   Para apagarla, haz doble clic en "detener-windows.bat".
echo.
pause

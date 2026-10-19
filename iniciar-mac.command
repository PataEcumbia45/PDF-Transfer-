#!/bin/bash
# PDF Transfer: doble clic en el Finder (Mac) o "bash iniciar-mac.command" (Mac/Linux).
cd "$(dirname "$0")" || exit 1
echo
echo "  =========================================="
echo "    PDF Transfer - iniciando..."
echo "  =========================================="
echo

if ! docker info >/dev/null 2>&1; then
  echo "  Docker no está abierto o no está instalado."
  echo
  echo "  1. Instala Docker Desktop: https://www.docker.com/products/docker-desktop/"
  echo "  2. Ábrelo y espera a que diga \"Engine running\"."
  echo "  3. Vuelve a abrir este archivo."
  echo
  read -r -p "  Pulsa Enter para cerrar." _
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.personal .env
  echo "  Creado el archivo de configuración .env"
fi

echo "  Preparando la aplicación. La primera vez tarda varios minutos"
echo "  porque descarga lo necesario; las siguientes es casi inmediato."
echo
if ! docker compose up -d --build; then
  echo
  echo "  No se pudo iniciar. Revisa los mensajes de arriba."
  read -r -p "  Pulsa Enter para cerrar." _
  exit 1
fi

echo
echo "  Esperando a que la aplicación responda..."
for _ in $(seq 1 90); do
  if curl -fsS -m 2 http://localhost:8000/api/health >/dev/null 2>&1; then
    (open http://localhost:8000 || xdg-open http://localhost:8000) >/dev/null 2>&1
    echo
    echo "  Lista: http://localhost:8000"
    echo "  Para apagarla, abre \"detener-mac.command\"."
    echo
    exit 0
  fi
  sleep 2
done
echo "  La aplicación tarda en responder. Prueba a abrir http://localhost:8000"
exit 1

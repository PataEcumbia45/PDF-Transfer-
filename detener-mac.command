#!/bin/bash
cd "$(dirname "$0")" || exit 1
echo "Deteniendo PDF Transfer..."
docker compose down
echo "Listo. La aplicación está apagada."

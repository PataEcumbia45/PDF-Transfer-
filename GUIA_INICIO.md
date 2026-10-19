# Guía rápida: usar PDF Transfer en tu computador

Con esta guía tendrás la aplicación funcionando en tu propio computador, solo para ti, en unos 15 minutos (la mayor parte es esperar descargas). No hace falta saber programar.

## Qué necesitas

- Windows 10/11 (64 bits) o un Mac, con unos 4 GB de RAM libres y 3 GB de disco.
- Conexión a internet la primera vez.

## Paso 1. Instalar Docker Desktop (una sola vez)

Docker es el programa que "empaqueta" la aplicación con todo lo que necesita, para que funcione igual en cualquier computador.

1. Descárgalo de <https://www.docker.com/products/docker-desktop/> e instálalo con las opciones por defecto. En Windows puede pedirte reiniciar y activar "WSL 2": acepta.
2. Ábrelo. Espera a que en la esquina inferior izquierda aparezca **Engine running** (en verde).

Docker Desktop es gratuito para uso personal y para empresas pequeñas.

## Paso 2. Descargar la aplicación

1. Descarga el ZIP: <https://github.com/PataEcumbia45/PDF-Transfer-/archive/refs/heads/claude/pdf-markdown-converter-sc38ew.zip>
2. Descomprímelo en una carpeta fácil de encontrar, por ejemplo `Documentos\PDF-Transfer`.

## Paso 3. Iniciar

- **Windows:** doble clic en **`iniciar-windows.bat`**. Si Windows muestra "Windows protegió su PC", pulsa *Más información → Ejecutar de todas formas*.
- **Mac:** clic derecho en **`iniciar-mac.command`** → *Abrir* (la primera vez macOS pide confirmación).

La primera vez tarda entre 5 y 10 minutos porque descarga e instala todo. Al terminar se abre el navegador en **<http://localhost:8000>**. Las siguientes veces arranca en segundos.

> Puedes cerrar la ventana negra: la aplicación sigue funcionando. Mientras Docker Desktop esté abierto, la tendrás disponible en <http://localhost:8000> (puedes guardarla en marcadores).

## Paso 4. Apagar

Doble clic en **`detener-windows.bat`** (Windows) o **`detener-mac.command`** (Mac). También se apaga al cerrar Docker Desktop.

## Configuración (opcional)

La primera vez se crea un archivo **`.env`** en la carpeta de la aplicación (en Windows ábrelo con el Bloc de notas). Después de cambiarlo, vuelve a ejecutar `iniciar`.

| Opción | Para qué |
|---|---|
| `ANTHROPIC_API_KEY=` | Activa el **Modo IA**: transcribe fórmulas pegadas como imagen, PDF escaneados y escritura a mano. Crea la clave en <https://console.anthropic.com> (tiene coste por uso, unos 0,03–0,06 USD por página). |
| `DISABLE_LIMITS=true` | Sin límites de tamaño, páginas ni conversiones (ya viene así, porque es para ti). |
| `APP_PORT=8000` | Cambia el puerto si el 8000 ya lo usa otro programa (entonces entra en `http://localhost:NUEVO_PUERTO`). |

La aplicación solo es accesible desde tu computador: nadie de tu red puede entrar. Tus archivos se procesan en tu equipo y no se guardan.

## Actualizar a una versión nueva

Descarga de nuevo el ZIP del paso 2, descomprímelo encima de la carpeta anterior (conserva tu `.env`) y ejecuta `iniciar` otra vez.

## Problemas frecuentes

| Problema | Solución |
|---|---|
| "Docker Desktop no está abierto o no está instalado" | Abre Docker Desktop y espera a **Engine running**. |
| "port is already allocated" | Otro programa usa el puerto 8000: pon `APP_PORT=8001` en `.env` y entra en <http://localhost:8001>. |
| La primera vez se queda mucho rato | Es normal mientras descarga (unos 1-2 GB). Si falla por la conexión, vuelve a ejecutar `iniciar`. |
| No se abre el navegador | Abre tú mismo <http://localhost:8000>. |

## Después: publicarla en internet

Cuando quieras usarla desde cualquier lugar o abrirla a otras personas, se publica la misma aplicación en un servidor (AWS, Render, Railway…). Para la primera etapa, solo para ti, basta con poner una contraseña en `.env`:

```
ACCESS_USER=tu_usuario
ACCESS_PASSWORD=una-contraseña-larga
```

El navegador la pedirá al entrar. Los pasos detallados para AWS los preparamos en la siguiente etapa.

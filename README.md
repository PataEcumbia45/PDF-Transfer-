# PDF Transfer

Aplicación web para convertir **PDF → Markdown** y **Markdown → PDF** sin perder contenido, con interfaz moderna, vista previa en vivo, conversión por lotes y una API REST preparada para ofrecerse como servicio de pago.

## Características

| | |
|---|---|
| **Ida y vuelta sin pérdidas** | Cada PDF generado lleva dentro, como adjunto PDF estándar, su Markdown original y sus imágenes. Al volver a convertirlo se recupera el archivo **exacto**, byte a byte. Si alguien modifica el PDF después, se detecta y se hace una reconstrucción normal. |
| **Fórmulas matemáticas** | Markdown → PDF: LaTeX con `$...$` (en línea), `$$...$$` (en bloque), entornos `\begin{align}` y bloques ` ```math `; se dibujan con MathJax (fracciones, raíces, integrales, límites, matrices, sistemas…). PDF → Markdown: potencias, subíndices, letras griegas y símbolos se leen localmente; fracciones, matrices, escaneos y escritura a mano, con el **Modo IA**. |
| **PDF → Markdown** | Con cualquier PDF se analiza el diseño: títulos (por tamaño de letra), **negrita**, *cursiva*, `código`, listas con viñetas o numeradas (incluidas anidadas), tablas, bloques de código, enlaces, imágenes y texto a dos columnas. Quita encabezados y pies de página repetidos y los números de página, y une los párrafos cortados entre páginas. |
| **Markdown → PDF** | CommonMark con tablas, tachado, notas al pie, listas de tareas, front matter y resaltado de código. Tres temas (Moderno, Clásico, Técnico), varios tamaños de papel, numeración de páginas y marcadores de navegación. |
| **Interfaz** | Arrastrar y soltar, varios archivos a la vez, editor con barra de formato y vista previa idéntica al PDF, descarga en .md, .zip o .pdf, modo claro/oscuro y diseño adaptado a móvil. |
| **Listo para cobrar** | Planes (Gratis, Pro, Empresas) con límites de tamaño, páginas, peticiones por hora y lote; claves de API por cliente; página de precios. |
| **Seguro** | Los archivos se procesan en memoria y no se guardan. El Markdown no puede leer ficheros del servidor ni, por defecto, hacer peticiones a la red (evita ataques SSRF). |

## Puesta en marcha

### Con Docker (recomendado)

```bash
docker compose up --build
```

Abre <http://localhost:8000>. La documentación interactiva de la API está en <http://localhost:8000/docs>.

### Sin Docker

Necesitas Python 3.11 o superior, Node.js 18 o superior (para dibujar las fórmulas con MathJax) y las librerías de Pango (lo que usa WeasyPrint para maquetar):

- Debian/Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu fonts-liberation`
- macOS: `brew install pango`
- Windows: consulta la [guía de WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
npm ci --prefix app/mathjax
uvicorn app.main:app --reload
pytest            # ejecutar los tests
```

## Configuración

Todo se configura con variables de entorno (ver `.env.example`):

| Variable | Descripción |
|---|---|
| `APP_NAME` | Nombre comercial (web, API y metadatos de los PDF). |
| `CONTACT_EMAIL` | Correo al que llegan las solicitudes de planes de pago. |
| `API_KEYS` | Claves de clientes: `clave1:pro,clave2:business`. |
| `ALLOW_REMOTE_IMAGES` | Permite imágenes `http(s)` en el Markdown. Mantener en `false` en un servicio público. |
| `DISABLE_LIMITS` | Quita todos los límites (uso personal o interno). |
| `CORS_ORIGINS` | Dominios que pueden usar la API desde el navegador. |
| `ANTHROPIC_API_KEY` | Activa el Modo IA (ver abajo). |
| `AI_MODEL` / `AI_EFFORT` / `AI_CONCURRENCY` | Modelo (`claude-opus-5` por defecto), esfuerzo (`medium`) y páginas en paralelo (`4`) del Modo IA. |

Los límites de cada plan están en `app/config.py` (`PLANS`) y los precios que se muestran en `app/static/app.js` (`PRICES`).

## Fórmulas

### Escribir fórmulas (Markdown → PDF)

```markdown
La ecuación $ax^2 + bx + c = 0$ se resuelve con:

$$
x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}
$$
```

Se admite LaTeX completo de MathJax: `\frac`, `\sqrt`, `\int`, `\sum`, `\lim`, `pmatrix`, `cases`, `align`, `\mathbb`… Los importes como "$5 y $10" no se confunden con fórmulas (tras el `$` de apertura no puede haber un espacio, y tras el de cierre no puede haber un dígito). Si una fórmula tiene un error, se muestra el LaTeX en rojo en vez de perderla. El editor tiene botones para insertar fórmulas, fracciones y raíces.

### Leer fórmulas de un PDF (PDF → Markdown)

| Tipo de PDF | Cómo se lee |
|---|---|
| Creado con esta app | Se recupera el Markdown original exacto, con todas sus fórmulas. |
| Fórmulas sencillas (Word, LaTeX, web) | Lectura local: potencias, subíndices, letras griegas y símbolos pasan a LaTeX (`$x^{2} + 3x - 4 = 0$`). |
| Fracciones, matrices, sistemas; escaneos; escritura a mano | **Modo IA**: Claude transcribe cada página a Markdown con las fórmulas en LaTeX. |

El selector **Fórmulas y escaneos** de la interfaz (campo `mode` de la API) tiene tres opciones:

- `auto` (por defecto): usa el Modo IA solo si el PDF tiene fórmulas o está escaneado.
- `ai`: siempre Modo IA.
- `standard`: nunca Modo IA.

### Activar el Modo IA

1. Crea una clave en <https://console.anthropic.com>.
2. Defínela en la variable `ANTHROPIC_API_KEY` del servidor.

Coste orientativo con `claude-opus-5`: unos 0,03–0,06 USD por página (depende de la densidad de texto). Con `AI_MODEL=claude-sonnet-5` baja aproximadamente a la mitad, con algo menos de precisión. Cada plan limita las páginas por documento en Modo IA (`ai_max_pages` en `app/config.py`), para que el coste quede cubierto por la suscripción. Revisa siempre el resultado: la IA puede equivocarse en fórmulas muy densas o en letra poco legible.

## API

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/api/convert/pdf-to-md` | `file` (PDF). `output` = `json` \| `md` \| `zip`. `images` = `embed` \| `none`. `mode` = `auto` \| `ai` \| `standard`. |
| `POST` | `/api/convert/md-to-pdf` | `file` (.md o .zip con imágenes) o `markdown` (texto), `assets` (imágenes), `theme`, `page_size`, `embed_source`. |
| `POST` | `/api/preview` | HTML de vista previa con el mismo aspecto que el PDF. |
| `POST` | `/api/unpack` | Abre un .zip (Markdown + imágenes). |
| `POST` | `/api/bundle` | Empaqueta varios resultados en un .zip. |
| `GET` | `/api/config` | Plan actual, límites y opciones. |

Los clientes de pago envían su clave en la cabecera `X-API-Key`:

```bash
curl -H "X-API-Key: TU_CLAVE" -F file=@informe.pdf -F output=md \
     https://tu-dominio.com/api/convert/pdf-to-md -o informe.md
```

## Despliegue

La imagen Docker funciona en cualquier plataforma de contenedores: Railway, Render, Fly.io, Google Cloud Run, AWS App Runner, DigitalOcean App Platform o un VPS con Docker. Escucha en el puerto de la variable `PORT` (8000 por defecto) y ya viene preparada para trabajar detrás de un proxy HTTPS.

Recomendaciones para producción:

1. Pon el servicio detrás de HTTPS (lo incluyen las plataformas anteriores; en un VPS usa Caddy o Nginx).
2. Ajusta `WORKERS` según los núcleos de CPU: la conversión usa CPU de forma intensiva.
3. El límite de peticiones por hora se guarda en memoria de cada proceso. Si ejecutas varias réplicas, cámbialo por Redis (`app/limits.py`, clase `RateLimiter`).

## Hoja de ruta para monetizar

Ya está hecho: planes con límites, claves de API, página de precios y botón de contacto.

Siguientes pasos sugeridos:

1. **Cuentas de usuario**: registro e inicio de sesión (por ejemplo con Supabase Auth, Clerk o Auth0).
2. **Pagos**: Stripe Checkout + webhooks para asignar el plan al usuario y generar su clave de API. Cambia el diccionario `API_KEYS` por una tabla en base de datos (`resolve_plan` en `app/limits.py` es el único punto que hay que tocar).
3. **Contador de uso** en base de datos o Redis, con panel para el cliente.
4. **Páginas legales**: términos de uso, política de privacidad (RGPD) y aviso de cookies.

## Licencias

Todas las dependencias tienen licencias permisivas que permiten el uso comercial sin publicar tu código: FastAPI (MIT), pdfplumber (MIT), pdfminer.six (MIT), pypdf (BSD), pypdfium2 (Apache-2.0/BSD), WeasyPrint (BSD), markdown-it-py (MIT), Pygments (BSD), MathJax (Apache-2.0) y el SDK de Anthropic (MIT). El Modo IA usa la API de Anthropic según sus condiciones comerciales. Se evitó a propósito PyMuPDF, cuya licencia AGPL obligaría a publicar el código del servicio o a comprar una licencia comercial.

## Estructura

```
app/
  main.py              API y servidor web
  config.py            configuración y planes
  limits.py            planes, claves de API y límite de peticiones
  converters/
    pdf_to_md.py       PDF → Markdown (análisis del diseño)
    md_to_pdf.py       Markdown → PDF (markdown-it + WeasyPrint)
    embed.py           Markdown incrustado para la ida y vuelta sin pérdidas
    math_render.py     fórmulas LaTeX → SVG (MathJax en un proceso Node)
    math_text.py       lectura local de fórmulas sencillas
    ai_transcribe.py   Modo IA: transcripción de páginas con Claude
  mathjax/             MathJax (Node) para dibujar fórmulas
  themes/              estilos de los PDF
  static/              interfaz web (HTML, CSS y JS sin dependencias)
tests/                 tests automáticos (pytest)
```

## Limitaciones conocidas

- Sin el Modo IA, los PDF escaneados no se convierten a texto (se extraen como imágenes con un aviso) y las fórmulas con varias líneas (fracciones, matrices) no se reconstruyen bien.
- En PDF ajenos, las tablas sin bordes y los diseños muy complejos (revistas, varias columnas irregulares) se reconstruyen de forma aproximada. El editor permite corregir el resultado antes de descargarlo.

"""Modo IA: transcripción de páginas PDF a Markdown + LaTeX con Claude.

Para lo que el análisis del diseño no puede leer: fórmulas complejas
(fracciones, raíces, matrices, sistemas, integrales), PDF escaneados y texto
escrito a mano. Cada página se convierte en imagen y Claude la transcribe;
las páginas se procesan en paralelo.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import pdfplumber
import pypdfium2 as pdfium

from ..config import settings
from .pdf_to_md import ConversionResult, open_pdf, page_images

LOGGER = logging.getLogger(__name__)

RENDER_SCALE = 2.0  # 144 ppp: suficiente para leer subíndices y exponentes
MAX_TOKENS = 16000
FIGURE_MARK = "[[FIGURA]]"
# Modelos que admiten el respaldo automático en el servidor ante un rechazo.
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}

SYSTEM_PROMPT = f"""Transcribes páginas de documentos (talleres, exámenes, guías, apuntes) a Markdown.
El resultado se edita y se vuelve a convertir a PDF, así que debe conservar todo el contenido de la
página y su estructura, sin añadir nada.

- Transcribe el texto tal cual, en su idioma original, incluidos los enunciados, la numeración de
  ejercicios y los espacios para responder (como "________"). No resuelvas ni corrijas ejercicios.
- Escribe toda expresión matemática, química o física en LaTeX: en línea con $...$ y en bloque con
  $$...$$ en líneas propias. Usa \\frac, \\sqrt, \\int, \\sum, \\lim, \\begin{{pmatrix}},
  \\begin{{cases}}, \\begin{{aligned}}, etc. según corresponda.
- Usa # para títulos, listas de Markdown, **negrita**, *cursiva* y tablas de Markdown.
- Donde haya una figura, gráfica, diagrama o dibujo, escribe {FIGURE_MARK} en una línea propia.
- Transcribe también el texto escrito a mano. Si algo es ilegible, escribe [ilegible].
- Omite encabezados y pies de página repetidos y los números de página.

Responde solo con el Markdown de la página, sin comentarios ni bloques de código que lo envuelvan."""


class AIError(Exception):
    """Error del Modo IA con un mensaje apto para el usuario."""


def _client():
    import anthropic

    return anthropic.Anthropic(api_key=settings.ai_api_key, max_retries=3, timeout=300.0)


def _render_page(pdf: pdfium.PdfDocument, index: int) -> tuple[str, str]:
    """Página como imagen en base64: ``(tipo MIME, datos)``."""
    image = pdf[index].render(scale=RENDER_SCALE).to_pil().convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    if buf.tell() > 4_500_000:  # límite de 5 MB por imagen de la API
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=88)
        return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()
    return "image/png", base64.standard_b64encode(buf.getvalue()).decode()


def _clean(text: str) -> str:
    text = text.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", text, re.S)
    return (fenced.group(1) if fenced else text).strip()


def _transcribe_page(client, media_type: str, data: str, number: int, total: int) -> tuple[str, str | None]:
    """Devuelve ``(markdown, aviso)`` de una página."""
    import anthropic

    params = dict(
        model=settings.ai_model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": settings.ai_effort},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                {"type": "text", "text": f"Página {number} de {total}."},
            ],
        }],
    )
    try:
        if settings.ai_model in FALLBACK_MODELS:
            # Si el modelo rechaza la petición, el servidor la repite en el modelo recomendado.
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"}, **params
            )
        else:
            response = client.messages.create(**params)
    except anthropic.AuthenticationError as exc:
        raise AIError("La clave del Modo IA no es válida. Revisa ANTHROPIC_API_KEY.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AIError("La clave del Modo IA no tiene permiso para usar este modelo.") from exc
    except anthropic.RateLimitError:
        return "", f"Página {number}: el servicio de IA está saturado; inténtalo de nuevo en unos minutos."
    except anthropic.APIStatusError as exc:
        LOGGER.warning("Modo IA, página %s: %s", number, exc)
        return "", f"Página {number}: el servicio de IA devolvió un error ({exc.status_code})."
    except anthropic.APIConnectionError:
        return "", f"Página {number}: no se pudo conectar con el servicio de IA."

    if response.stop_reason == "refusal":
        return "", f"Página {number}: la IA no pudo transcribir esta página."
    text = _clean("".join(b.text for b in response.content if b.type == "text"))
    warning = None
    if response.stop_reason == "max_tokens":
        warning = f"Página {number}: la transcripción es muy larga y puede estar incompleta."
    return text, warning


def _place_figures(markdown: str, figures: list[tuple[float, str, bytes]]) -> tuple[str, dict[str, bytes]]:
    """Sustituye cada marca de figura por la imagen correspondiente de la página, en orden."""
    assets: dict[str, bytes] = {}
    queue = list(figures)

    def replace(_match: re.Match) -> str:
        if not queue:
            return ""
        _, name, png = queue.pop(0)
        assets[name] = png
        return f"![Figura](images/{name})"

    markdown = re.sub(r"^[ \t]*" + re.escape(FIGURE_MARK) + r"[ \t]*$", replace, markdown, flags=re.M)
    markdown = markdown.replace(FIGURE_MARK, "")
    for _, name, png in queue:  # figuras que la IA no marcó: al final de la página
        assets[name] = png
        markdown += f"\n\n![Figura](images/{name})"
    return re.sub(r"\n{3,}", "\n\n", markdown).strip(), assets


def transcribe_pdf(data: bytes, images: str = "embed") -> ConversionResult:
    if not settings.ai_enabled:
        raise AIError("El Modo IA no está configurado en este servidor.")
    reader = open_pdf(data)
    total = len(reader.pages)

    pdf = pdfium.PdfDocument(data)
    try:
        rendered = [_render_page(pdf, i) for i in range(total)]
    finally:
        pdf.close()

    figures: list[list[tuple[float, str, bytes]]] = [[] for _ in range(total)]
    if images != "none":
        with pdfplumber.open(io.BytesIO(data)) as doc:
            for i, page in enumerate(doc.pages):
                figures[i] = page_images(page, i, skip_full_page=True)

    client = _client()
    with ThreadPoolExecutor(max_workers=max(1, settings.ai_concurrency)) as pool:
        futures = [pool.submit(_transcribe_page, client, mt, b64, i + 1, total)
                   for i, (mt, b64) in enumerate(rendered)]
        results = [f.result() for f in futures]  # AIError (clave no válida) se propaga

    parts, warnings, assets = [], [], {}
    for i, (text, warning) in enumerate(results):
        if warning:
            warnings.append(warning)
        if not text:
            parts.append(f"> **Página {i + 1}:** no se pudo transcribir.")
            continue
        text, page_assets = _place_figures(text, figures[i])
        assets.update(page_assets)
        parts.append(text)
    if all(not text for text, _ in results):
        raise AIError(warnings[0] if warnings else "No se pudo transcribir el documento con IA.")

    warnings.append("Transcrito con IA: revisa el resultado antes de usarlo, sobre todo las fórmulas.")
    markdown = "\n\n".join(parts).strip() + "\n"
    return ConversionResult(markdown, assets, "ai", total, warnings, has_math="$" in markdown)

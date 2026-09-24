"""Markdown → PDF.

Markdown (CommonMark + tablas, tachado, notas al pie, listas de tareas y
front matter) se convierte a HTML con markdown-it y se maqueta como PDF con
WeasyPrint. El Markdown original se incrusta en el PDF resultante para poder
recuperarlo sin pérdidas (ver ``embed.py``).
"""

from __future__ import annotations

import base64
import html
import mimetypes
import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt
from mdit_py_plugins.amsmath import amsmath_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from .embed import embed_source
from .math_render import render_math_html

THEMES_DIR = Path(__file__).resolve().parent.parent / "themes"
THEMES = {"moderno": "Moderno", "clasico": "Clásico", "tecnico": "Técnico"}
PAGE_SIZES = {"A4": "A4", "Letter": "letter", "Legal": "legal", "A5": "A5"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}


@dataclass
class PdfOptions:
    theme: str = "moderno"
    page_size: str = "A4"
    embed_source: bool = True
    allow_remote_images: bool = False


def _highlight(code: str, lang: str, _attrs: str) -> str:
    if not lang:
        return ""
    try:
        lexer = get_lexer_by_name(lang.split()[0])
    except ClassNotFound:
        return ""
    return f'<span class="hl">{pygments_highlight(code, lexer, HtmlFormatter(nowrap=True))}</span>'


def _normalize_asset_name(name: str) -> str:
    name = unquote(name.split("?")[0].split("#")[0]).replace("\\", "/")
    return posixpath.normpath(name).lstrip("./")


def _build_parser(assets: dict[str, bytes]) -> MarkdownIt:
    md = (
        MarkdownIt("commonmark", {"html": True, "highlight": _highlight})
        .enable(["table", "strikethrough"])
        .use(front_matter_plugin)
        .use(footnote_plugin)
        .use(tasklists_plugin)
        # Fórmulas: $...$ en línea, $$...$$ en bloque y entornos \begin{align}...
        # Sin espacios junto a los $ ni dígitos tras el $ de cierre, para que
        # importes como "$5 y $10" no se confundan con fórmulas.
        .use(dollarmath_plugin, allow_space=False, allow_digits=False, double_inline=True,
             renderer=lambda tex, cfg: render_math_html(tex, cfg["display_mode"]))
        .use(amsmath_plugin, renderer=lambda tex: render_math_html(tex, True))
    )
    normalized = {_normalize_asset_name(k): v for k, v in assets.items()}
    default_image = md.renderer.rules.get("image")

    def render_image(self, tokens, idx, options, env):
        # Las imágenes subidas junto al Markdown se incrustan como data URI,
        # así el PDF no depende de ficheros externos.
        token = tokens[idx]
        src = token.attrGet("src") or ""
        data = normalized.get(_normalize_asset_name(src)) if not src.startswith("data:") else None
        if data is not None:
            mime = mimetypes.guess_type(src)[0] or "application/octet-stream"
            token.attrSet("src", f"data:{mime};base64,{base64.b64encode(data).decode()}")
        return default_image(tokens, idx, options, env)

    md.add_render_rule("image", render_image)

    default_fence = md.renderer.rules.get("fence")

    def render_fence(self, tokens, idx, options, env):
        # Bloques ```math también se dibujan como fórmula.
        token = tokens[idx]
        if token.info.strip().lower() in {"math", "latex", "tex"}:
            return f'<div class="math block">{render_math_html(token.content, True)}</div>\n'
        return default_fence(tokens, idx, options, env)

    md.add_render_rule("fence", render_fence)
    return md


def front_matter_title(markdown: str) -> str | None:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", markdown, re.S)
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "title" and value.strip():
                return value.strip().strip("\"'")
    heading = re.search(r"^#\s+(.+?)\s*#*\s*$", markdown, re.M)
    return heading.group(1).strip() if heading else None


@lru_cache(maxsize=None)
def _theme_css(theme: str) -> str:
    base = (THEMES_DIR / "base.css").read_text(encoding="utf-8")
    extra = (THEMES_DIR / f"{theme}.css").read_text(encoding="utf-8")
    return base + "\n" + extra


def render_html(markdown: str, assets: dict[str, bytes] | None = None, theme: str = "moderno",
                page_size: str = "A4", for_preview: bool = False) -> str:
    """Documento HTML completo. Se usa tanto para el PDF como para la vista previa."""
    theme = theme if theme in THEMES else "moderno"
    size = PAGE_SIZES.get(page_size, "A4")
    body = _build_parser(assets or {}).render(markdown)
    title = html.escape(front_matter_title(markdown) or "Documento")
    css = _theme_css(theme) + f"\n@page {{ size: {size}; }}\n"
    if for_preview:
        # Simula la hoja en pantalla: ancho de página y márgenes aproximados.
        css += (
            "\nhtml{background:#e9ecf2}"
            "body{background:#fff;max-width:170mm;margin:24px auto;padding:22mm 20mm;"
            "box-shadow:0 2px 14px rgba(20,30,60,.12);border-radius:4px}"
            "@media (max-width:700px){body{margin:0;padding:18px;border-radius:0;box-shadow:none}}"
        )
    return (
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title><style>{css}</style></head>"
        f"<body>{body}</body></html>"
    )


def markdown_to_pdf(markdown: str, assets: dict[str, bytes] | None = None,
                    options: PdfOptions | None = None, creator: str = "PDF Transfer") -> bytes:
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    options = options or PdfOptions()
    assets = assets or {}
    document = render_html(markdown, assets, options.theme, options.page_size)

    # Solo se permiten data URIs (y http/https si se habilita). Nunca file://:
    # un Markdown malicioso no debe poder leer ficheros del servidor.
    protocols = {"data"} | ({"http", "https"} if options.allow_remote_images else set())
    fetcher = URLFetcher(timeout=8, allowed_protocols=protocols)
    pdf = HTML(string=document, url_fetcher=fetcher).write_pdf()

    if options.embed_source:
        pdf = embed_source(pdf, markdown, assets, title=front_matter_title(markdown), creator=creator)
    return pdf


def is_image_name(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTENSIONS

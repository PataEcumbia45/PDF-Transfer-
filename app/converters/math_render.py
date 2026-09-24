"""Fórmulas LaTeX → SVG con MathJax.

MathJax se ejecuta en un proceso Node persistente (``app/mathjax/tex2svg.js``)
para no pagar el arranque en cada fórmula. Los resultados se guardan en caché,
así la vista previa en vivo no vuelve a renderizar fórmulas que no cambian.
"""

from __future__ import annotations

import json
import logging
import re
import selectors
import shutil
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)
MATHJAX_DIR = Path(__file__).resolve().parent.parent / "mathjax"
WORKER = MATHJAX_DIR / "tex2svg.js"
TIMEOUT_SECONDS = 15
CACHE_SIZE = 2000
EX_TO_EM = 0.5  # MathJax mide en ex suponiendo ex = em / 2


@dataclass(frozen=True)
class MathResult:
    svg: str | None
    error: str | None = None


class MathRenderer:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, bool], MathResult] = OrderedDict()
        self._seq = 0
        self._unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        with self._lock:
            return self._ensure_process()

    def _ensure_process(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        node = shutil.which("node")
        if node is None:
            self._unavailable_reason = "Node.js no está instalado"
            return False
        if not (MATHJAX_DIR / "node_modules" / "mathjax-full").exists():
            self._unavailable_reason = "falta instalar MathJax (npm ci en app/mathjax)"
            return False
        self._proc = subprocess.Popen(
            [node, str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
        )
        return True

    def _kill(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc = None

    def render(self, tex: str, display: bool = False) -> MathResult:
        key = (tex, display)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            if not self._ensure_process():
                return MathResult(None, f"Fórmulas no disponibles: {self._unavailable_reason}.")
            self._seq += 1
            request_id = self._seq
            try:
                assert self._proc and self._proc.stdin and self._proc.stdout
                self._proc.stdin.write(json.dumps({"id": request_id, "tex": tex, "display": display}) + "\n")
                self._proc.stdin.flush()
                result = self._read_response(request_id)
            except (OSError, ValueError, TimeoutError) as exc:
                LOGGER.warning("MathJax falló: %s", exc)
                self._kill()
                return MathResult(None, "No se pudo dibujar la fórmula.")
            self._cache[key] = result
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
            return result

    def _read_response(self, request_id: int) -> MathResult:
        assert self._proc and self._proc.stdout
        sel = selectors.DefaultSelector()
        sel.register(self._proc.stdout, selectors.EVENT_READ)
        try:
            while True:
                if not sel.select(timeout=TIMEOUT_SECONDS):
                    raise TimeoutError("MathJax no respondió a tiempo")
                line = self._proc.stdout.readline()
                if not line:
                    raise OSError("El proceso de MathJax terminó")
                data = json.loads(line)
                if data.get("id") == request_id:
                    return MathResult(data.get("svg"), data.get("error"))
        finally:
            sel.close()

    def close(self) -> None:
        with self._lock:
            self._kill()


renderer = MathRenderer()


_DIM_RE = re.compile(r'\s(width|height)="([\d.]+)ex"')
_VALIGN_RE = re.compile(r'style="vertical-align:\s*(-?[\d.]+)ex;?"')


def svg_to_img(svg: str, tex: str, display: bool) -> str:
    """Etiqueta ``<img>`` con el SVG incrustado y el tamaño correcto en el texto.

    Las medidas de MathJax (en ex) se pasan a em para que la fórmula escale con
    la letra del tema y quede alineada con la línea de base del texto.
    """
    import base64
    import html

    dims = {k: float(v) * EX_TO_EM for k, v in _DIM_RE.findall(svg)}
    valign = _VALIGN_RE.search(svg)
    style = []
    if "width" in dims:
        style.append(f"width:{dims['width']:.3f}em")
    if "height" in dims and not display:  # en bloque, alto proporcional (se puede encoger)
        style.append(f"height:{dims['height']:.3f}em")
    if valign and not display:
        style.append(f"vertical-align:{float(valign.group(1)) * EX_TO_EM:.3f}em")
    data = base64.b64encode(svg.encode("utf-8")).decode()
    cls = "math math-display" if display else "math math-inline"
    return (f'<img class="{cls}" alt="{html.escape(tex, quote=True)}" '
            f'style="{";".join(style)}" src="data:image/svg+xml;base64,{data}">')


def render_math_html(tex: str, display: bool) -> str:
    import html

    tex = tex.strip()
    result = renderer.render(tex, display)
    if result.svg:
        return svg_to_img(result.svg, tex, display)
    # Si falla, se muestra el LaTeX tal cual y el motivo, sin perder el contenido.
    delim = "$$" if display else "$"
    title = html.escape(result.error or "", quote=True)
    return f'<code class="math-error" title="{title}">{html.escape(delim + tex + delim)}</code>'

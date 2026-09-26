"""PDF → Markdown.

Dos caminos:

1. **Sin pérdidas**: si el PDF lo generó esta aplicación, lleva el Markdown
   original incrustado y se devuelve tal cual (ver ``embed.py``).
2. **Reconstrucción**: para cualquier otro PDF se analiza el diseño con
   pdfplumber: tamaños y estilos de fuente (títulos, negrita, cursiva,
   código), sangrías y viñetas (listas), tablas con bordes, enlaces, imágenes,
   texto a dos columnas, fórmulas sencillas (potencias, subíndices, letras
   griegas y símbolos, que se escriben en LaTeX), y se eliminan
   encabezados/pies de página repetidos y números de página.

Las fórmulas complejas (fracciones, matrices, escritura a mano, escaneos) las
transcribe el Modo IA (``ai_transcribe.py``).
"""

from __future__ import annotations

import io
import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import pdfplumber
from pypdf import PdfReader

from .embed import extract_source
from .math_layout import baseline_of, build_structures, join_latex, script_kind
from .math_text import is_glue, is_math_font, is_variable, looks_like_math, to_latex

BULLET_RE = re.compile(r"^([•●○◦▪▫■□‣⁃∙·◆◇►▸➢✓✔\-–—*+])\s*(?=\S)")
NUMBERED_RE = re.compile(r"^(\d{1,3}|[a-zA-Z]|[ivxlcdm]{1,6})([.)])\s+(?=\S)")
PAGE_NUMBER_RE = re.compile(
    r"^(?:(?:p[áa]g(?:ina)?|page|p\.)\s*)?\d{1,4}(?:\s*(?:/|de|of)\s*\d{1,4})?$", re.I
)
SENTENCE_END = (".", "!", "?", ":", ";", "…", '"', "»", ")")
MD_ESCAPE_RE = re.compile(r"([\\`*_\[\]<>|])")


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    bold: bool
    italic: bool
    mono: bool
    link: str | None = None
    math: bool = False  # fuente matemática o símbolos matemáticos
    latex: bool = False  # el texto ya es LaTeX (fracción, raíz… reconstruida)


@dataclass
class Line:
    words: list[Word]
    page: int

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def size(self) -> float:
        sizes = [w.size for w in self.words for _ in w.text]
        return statistics.median(sizes) if sizes else 0.0

    @property
    def all_bold(self) -> bool:
        return all(w.bold for w in self.words)

    @property
    def all_mono(self) -> bool:
        return all(w.mono for w in self.words)


@dataclass
class Block:
    """Elemento de salida ya ordenado en la página."""

    kind: str  # heading | para | list | code | table | image
    top: float
    page: int
    lines: list[Line] = field(default_factory=list)
    level: int = 0
    markdown: str = ""
    ordered: bool = False
    number: str = "1"


@dataclass
class ConversionResult:
    markdown: str
    assets: dict[str, bytes]
    source: str  # "embedded" (sin pérdidas) | "extracted" (reconstruido) | "ai" (Modo IA)
    pages: int
    warnings: list[str]
    has_math: bool = False  # se detectaron fórmulas
    scanned: bool = False  # apenas hay texto seleccionable


# ---------------------------------------------------------------- utilidades


def _font_flags(fontname: str) -> tuple[bool, bool, bool]:
    name = (fontname or "").split("+", 1)[-1].lower()
    bold = bool(re.search(r"bold|black|heavy|semibold|demibold|extrabold|,b\b|-b\b", name))
    italic = bool(re.search(r"italic|oblique|,i\b|-i\b|slanted", name))
    mono = bool(re.search(r"mono|courier|consol|menlo|code|typewriter|fixed|inconsolata", name))
    return bold, italic, mono


def _escape(text: str) -> str:
    escaped = MD_ESCAPE_RE.sub(r"\\\1", text)
    # Las rayas para rellenar ("______") no necesitan escape: no forman énfasis.
    return re.sub(r"(?:\\_){3,}", lambda m: "_" * (len(m.group(0)) // 2), escaped)


def _escape_line_start(text: str) -> str:
    # Evita que un párrafo que empieza por "#", "> ", "- " o "1. " cambie de tipo.
    if re.match(r"^(#{1,6}\s|>|[-+]\s|\d+[.)]\s)", text):
        return "\\" + text
    return text


def _inside(word_box: tuple[float, float, float, float], box: tuple[float, float, float, float],
            margin: float = 1.0) -> bool:
    x0, top, x1, bottom = word_box
    cx, cy = (x0 + x1) / 2, (top + bottom) / 2
    return box[0] - margin <= cx <= box[2] + margin and box[1] - margin <= cy <= box[3] + margin


# ----------------------------------------------------------- extracción base


def _page_words(page, exclude: list[tuple], links: list[tuple[tuple, str]]) -> list[Word]:
    raw = page.extract_words(
        extra_attrs=["fontname", "size", "non_stroking_color"], keep_blank_chars=False,
        x_tolerance=1.5, y_tolerance=2
    )
    words: list[Word] = []
    for w in raw:
        box = (w["x0"], w["top"], w["x1"], w["bottom"])
        if any(_inside(box, ex) for ex in exclude):
            continue
        fontname = w.get("fontname", "")
        bold, italic, mono = _font_flags(fontname)
        link = next((uri for lbox, uri in links if _inside(box, lbox, 0.0)), None)
        math = is_math_font(fontname) or looks_like_math(w["text"])
        words.append(Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"],
                          round(float(w.get("size", 0)), 1), bold, italic, mono and not math, link, math))
    return words


def _group_lines(words: list[Word], page_no: int) -> list[Line]:
    words = sorted(words, key=lambda w: (round(w.top), w.x0))
    lines: list[Line] = []
    for w in words:
        if lines:
            cur = lines[-1]
            ref = cur.words[-1]
            tol = max(2.0, min(ref.size, w.size) * 0.45)
            # Misma línea: alineación vertical similar, o solapada en más de la mitad
            # de su altura (potencias y subíndices, más pequeños y desplazados).
            top, bottom = min(x.top for x in cur.words), max(x.bottom for x in cur.words)
            overlap = min(w.bottom, bottom) - max(w.top, top)
            if (abs(w.top - cur.words[0].top) <= tol or abs(w.bottom - cur.words[0].bottom) <= tol
                    or overlap >= 0.5 * (w.bottom - w.top)):
                cur.words.append(w)
                continue
        lines.append(Line([w], page_no))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return _split_wide_gaps(lines)


def _split_wide_gaps(lines: list[Line]) -> list[Line]:
    """Separa en dos líneas las que atraviesan un hueco enorme (columnas)."""
    result: list[Line] = []
    for line in lines:
        start = 0
        for i in range(1, len(line.words)):
            gap = line.words[i].x0 - line.words[i - 1].x1
            if gap > max(line.words[i].size, 6) * 3 and not line.all_mono:
                result.append(Line(line.words[start:i], line.page))
                start = i
        result.append(Line(line.words[start:], line.page))
    return result


def _order_columns(lines: list[Line], page_width: float) -> list[Line]:
    """Ordena el texto a dos columnas: primero la izquierda y luego la derecha.

    Solo se consideran columnas las zonas donde hay líneas a izquierda y
    derecha del centro a la misma altura. El resto (títulos, párrafos a ancho
    completo, figuras) actúa como separador entre zonas.
    """
    if len(lines) < 8:
        return lines
    mid = page_width / 2
    left = [ln for ln in lines if ln.x1 <= mid + 4]
    right = [ln for ln in lines if ln.x0 >= mid - 4]
    if len(left) < 4 or len(right) < 4:
        return lines

    def overlaps(a: Line, others: list[Line]) -> bool:
        tol = (a.bottom - a.top) * 0.8
        return any(o.top <= a.bottom + tol and o.bottom >= a.top - tol for o in others)

    paired_left = [ln for ln in left if overlaps(ln, right)]
    paired_right = [ln for ln in right if overlaps(ln, left)]
    if len(paired_left) < 3 or len(paired_right) < 3:
        return lines  # texto alineado a la derecha, no columnas

    column_ids = {id(ln) for ln in paired_left + paired_right}
    cuts = sorted((ln for ln in lines if id(ln) not in column_ids), key=lambda ln: ln.top)
    rest = sorted(paired_left + paired_right, key=lambda ln: ln.top)
    ordered: list[Line] = []
    for cut in cuts + [None]:
        limit = cut.top if cut else float("inf")
        section = [ln for ln in rest if ln.top < limit]
        rest = [ln for ln in rest if ln.top >= limit]
        ordered += [ln for ln in section if ln.x1 <= mid + 4]
        ordered += [ln for ln in section if ln.x1 > mid + 4]
        if cut:
            ordered.append(cut)
    return ordered


def _table_markdown(rows: list[list[str | None]]) -> str | None:
    rows = [[(c or "").strip() for c in row] for row in rows if row and any((c or "").strip() for c in row)]
    if len(rows) < 2:
        return None
    width = max(len(r) for r in rows)
    if width < 2:
        return None

    def cell(value: str) -> str:
        value = re.sub(r"\s*\n\s*", " ", value)
        return value.replace("\\", "\\\\").replace("|", "\\|")

    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(cell(c) for c in rows[0]) + " |", "|" + "|".join(["---"] * width) + "|"]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows[1:]]
    return "\n".join(out)


# ---------------------------------------------------------- formato en línea


def _inline(lines: list[Line], join_hyphens: bool = True) -> str:
    """Convierte palabras con estilo en Markdown en línea.

    Negrita, cursiva, código, enlaces y fórmulas sencillas: las palabras en
    fuente matemática, con símbolos matemáticos o elevadas/rebajadas
    (potencias/subíndices) se agrupan en un mismo ``$...$`` en LaTeX.
    """
    # (texto, estilo, separador, tipo, ya_en_latex) con tipo = "text" | "math" | "glue"
    tokens: list[tuple[str, tuple, str, str, bool]] = []
    for li, line in enumerate(lines):
        baseline, main_size = baseline_of(line.words)
        for wi, w in enumerate(line.words):
            text = w.text
            last_in_line = wi == len(line.words) - 1
            nxt = lines[li + 1].words[0].text if last_in_line and li + 1 < len(lines) else ""
            if last_in_line:
                glue = " "
            else:
                # Sin hueco real entre palabras (p. ej. "negrita" + ","): no añadir espacio.
                gap = line.words[wi + 1].x0 - w.x1
                glue = " " if gap > w.size * 0.12 else ""
            if last_in_line and join_hyphens and text.endswith("-") and len(text) > 1 and nxt[:1].islower():
                text, glue = text[:-1], ""
            script = script_kind(w, baseline, main_size) if not w.mono else ""
            if w.latex:
                kind = "math"
            elif script:
                kind, text = "math", f"{script}{{{to_latex(text)}}}"
            elif w.math:
                kind = "math"
            elif is_glue(text) or is_variable(text):
                kind = "glue"
            else:
                kind = "text"
            tokens.append((text, (w.bold, w.italic, w.mono, w.link), glue, kind, w.latex or bool(script)))

    # Las piezas "glue" (números, operadores, paréntesis, variables como "x" o "f(x)")
    # junto a una fórmula pasan a formar parte de ella.
    kinds = [t[3] for t in tokens]
    i = 0
    while i < len(kinds):
        if kinds[i] != "glue":
            i += 1
            continue
        j = i
        while j < len(kinds) and kinds[j] == "glue":
            j += 1
        touches_math = (i > 0 and kinds[i - 1] == "math") or (j < len(kinds) and kinds[j] == "math")
        for k in range(i, j):
            kinds[k] = "math" if touches_math else "text"
        i = j

    out: list[str] = []
    i = 0
    while i < len(tokens):
        if kinds[i] == "math":
            j = i
            while j < len(tokens) and kinds[j] == "math":
                j += 1
            parts, spaces, trailing = [], [], ""
            for k in range(i, j):
                text, _, _, _, is_latex = tokens[k]
                parts.append(text if is_latex else to_latex(text))
                spaces.append(k > i and tokens[k - 1][2] == " " and not parts[-1].startswith(("^", "_")))
            formula = join_latex(parts, spaces).strip()
            # La puntuación final pertenece a la frase, no a la fórmula.
            while formula and formula[-1] in ".,;:":
                trailing = formula[-1] + trailing
                formula = formula[:-1].rstrip()
            glue_after = tokens[j - 1][2] if j < len(tokens) else ""
            out.append((f"${formula}$" if formula else "") + trailing + glue_after)
            i = j
            continue
        style = tokens[i][1]
        run, j = [], i
        while j < len(tokens) and kinds[j] != "math" and tokens[j][1] == style:
            run.append(tokens[j])
            j += 1
        bold, italic, mono, link = style
        text = "".join(t + (g if k < len(run) - 1 else "") for k, (t, _, g, _, _) in enumerate(run))
        glue_after = run[-1][2] if j < len(tokens) else ""
        if mono:
            ticks = "``" if "`" in text else "`"
            piece = f"{ticks}{text}{ticks}"
        else:
            piece = _escape(text)
            if bold and italic:
                piece = f"***{piece}***"
            elif bold:
                piece = f"**{piece}**"
            elif italic:
                piece = f"*{piece}*"
        if link:
            piece = f"[{piece}]({link.replace(' ', '%20').replace(')', '%29')})"
        out.append(piece + glue_after)
        i = j
    return "".join(out).strip()


# ------------------------------------------------------------- clasificación


def _classify(lines: list[Line], body_size: float, heading_levels: dict[float, int]) -> list[Block]:
    blocks: list[Block] = []
    list_base_x: float | None = None

    for idx, line in enumerate(lines):
        text = line.text.strip()
        prev = lines[idx - 1] if idx else None
        gap = line.top - prev.bottom if prev and prev.page == line.page else 999.0
        size = line.size or body_size
        new_para_gap = gap > max(size * 0.65, 3.5) or gap < -size  # también saltos de columna
        last = blocks[-1] if blocks else None

        # Código: toda la línea en fuente monoespaciada (y no un título).
        if line.all_mono and size <= body_size * 1.1:
            if last and last.kind == "code" and gap < size * 2.5 and gap > -size:
                last.lines.append(line)
            else:
                blocks.append(Block("code", line.top, line.page, [line]))
            continue

        level = heading_levels.get(round(size * 2) / 2)
        if level and len(text) < 200:
            if last and last.kind == "heading" and last.level == level and 0 <= gap < size * 0.8:
                last.lines.append(line)  # título que ocupa dos líneas
            else:
                blocks.append(Block("heading", line.top, line.page, [line], level=level))
            continue

        bullet = BULLET_RE.match(text)
        numbered = NUMBERED_RE.match(text)
        if (bullet or numbered) and not (numbered and len(text) < 4):
            if list_base_x is None or (last and last.kind != "list"):
                list_base_x = line.x0
            depth = max(0, round((line.x0 - list_base_x) / max(size * 1.4, 8)))
            first = line.words[0]
            marker_len = len((bullet or numbered).group(0).rstrip())
            # Quitar el marcador del primer "word" conservando el resto.
            remainder = first.text[marker_len:].lstrip()
            words = ([Word(remainder, first.x0, first.x1, first.top, first.bottom, first.size,
                           first.bold, first.italic, first.mono, first.link)] if remainder else []) + line.words[1:]
            item = Line(words or line.words, line.page)
            number = numbered.group(1) if numbered and numbered.group(1).isdigit() else "1"
            blocks.append(Block("list", line.top, line.page, [item], level=depth,
                                ordered=bool(numbered), number=number))
            continue

        # Continuación de un elemento de lista (texto sangrado bajo el marcador).
        if last and last.kind == "list" and not new_para_gap and line.x0 > last.lines[0].x0 - 2:
            last.lines.append(line)
            continue

        # Línea corta en negrita aislada = subtítulo menor.
        nxt = lines[idx + 1] if idx + 1 < len(lines) else None
        gap_after = nxt.top - line.bottom if nxt and nxt.page == line.page else 999.0
        if (line.all_bold and len(text) < 90 and not text.endswith((".", ",")) and new_para_gap
                and gap_after > size * 0.65 and heading_levels):
            blocks.append(Block("heading", line.top, line.page, [line], level=min(6, max(heading_levels.values()) + 1)))
            continue

        # Una línea que acaba en un espacio para rellenar ("Nombre: ____") cierra el párrafo.
        ends_blank = bool(last and last.lines and last.lines[-1].text.rstrip().endswith("___"))
        if (last and last.kind == "para" and not new_para_gap and not ends_blank
                and abs(size - last.lines[-1].size) < 1.5):
            last.lines.append(line)
        else:
            blocks.append(Block("para", line.top, line.page, [line]))
    return blocks


def _code_markdown(block: Block) -> str:
    base_x = min(ln.x0 for ln in block.lines)
    char_ws = [(w.x1 - w.x0) / len(w.text) for ln in block.lines for w in ln.words if w.text]
    cw = statistics.median(char_ws) if char_ws else 6.0
    out: list[str] = []
    prev: Line | None = None
    for ln in block.lines:
        if prev is not None:
            height = max(prev.bottom - prev.top, 1)
            blank = round((ln.top - prev.bottom) / height)
            out.extend([""] * max(0, min(blank, 3)))
        text = " " * max(0, round((ln.x0 - base_x) / cw))
        for i, w in enumerate(ln.words):
            if i:
                text += " " * max(0, round((w.x0 - ln.words[i - 1].x1) / cw))
            text += w.text
        out.append(text)
        prev = ln
    body = "\n".join(out)
    fence = "````" if "```" in body else "```"
    return f"{fence}\n{body}\n{fence}"


def _render_block(block: Block) -> str:
    if block.kind in {"table", "image"}:
        return block.markdown
    if block.kind == "code":
        return _code_markdown(block)
    if block.kind == "heading":
        text = " ".join(ln.text for ln in block.lines)
        return "#" * block.level + " " + _escape(text).strip()
    if block.kind == "list":
        marker = f"{block.number}." if block.ordered else "-"
        return "  " * block.level + f"{marker} " + _inline(block.lines)
    text = _inline(block.lines)
    if re.fullmatch(r"\$[^$]+\$", text):  # párrafo que es solo una fórmula: ecuación en bloque
        return f"$$\n{text[1:-1]}\n$$"
    return _escape_line_start(text)


# ---------------------------------------------------------------- principal


def _detect_repeated(all_lines: list[list[Line]], heights: list[float]) -> set[tuple[int, int]]:
    """Índices (página, línea) de encabezados/pies repetidos y números de página."""
    n_pages = len(all_lines)
    drop: set[tuple[int, int]] = set()
    counts: Counter = Counter()
    keyed: dict[tuple, list[tuple[int, int]]] = {}
    for p, lines in enumerate(all_lines):
        h = heights[p]
        for i, ln in enumerate(lines):
            in_margin = ln.top < h * 0.1 or ln.bottom > h * 0.9
            if not in_margin:
                continue
            text = ln.text.strip()
            if PAGE_NUMBER_RE.match(text):
                drop.add((p, i))
                continue
            key = (re.sub(r"\d+", "#", text.lower()), round(ln.top / 8))
            counts[key] += 1
            keyed.setdefault(key, []).append((p, i))
    threshold = max(2, int(n_pages * 0.5)) if n_pages >= 3 else 99
    for key, c in counts.items():
        if c >= threshold:
            drop.update(keyed[key])
    return drop


def page_images(page, p: int, skip_full_page: bool = False) -> list[tuple[float, str, bytes]]:
    """Imágenes de una página como PNG: ``[(posición vertical, nombre, bytes)]``."""
    found = []
    page_area = float(page.width * page.height) or 1.0
    for k, im in enumerate(page.images):
        x0, top = max(im["x0"], 0), max(im["top"], 0)
        x1, bottom = min(im["x1"], page.width), min(im["bottom"], page.height)
        if x1 - x0 < 24 or bottom - top < 24:
            continue  # iconos y adornos
        if skip_full_page and (x1 - x0) * (bottom - top) > page_area * 0.7:
            continue  # página escaneada completa, no una figura
        try:
            pil = page.crop((x0, top, x1, bottom)).to_image(resolution=150).original
            buf = io.BytesIO()
            pil.save(buf, format="PNG", optimize=True)
        except Exception:
            continue
        found.append((float(top), f"imagen-p{p + 1}-{k + 1}.png", buf.getvalue()))
    return sorted(found)


def open_pdf(data: bytes) -> PdfReader:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            pass
        if reader.is_encrypted:
            raise ValueError("El PDF está protegido con contraseña.")
    return reader


def pdf_to_markdown(data: bytes, images: str = "embed", prefer_embedded: bool = True) -> ConversionResult:
    """Convierte un PDF a Markdown.

    ``images``: ``"embed"`` (guardar como assets referenciados en ``images/``),
    o ``"none"`` (omitir imágenes).
    """
    warnings: list[str] = []
    reader = open_pdf(data)
    if prefer_embedded:
        embedded = extract_source(reader)
        if embedded:
            md, assets = embedded
            return ConversionResult(md, assets, "embedded", len(reader.pages), [])

    assets: dict[str, bytes] = {}
    page_lines: list[list[Line]] = []
    page_extras: list[list[Block]] = []
    heights: list[float] = []
    total_chars = 0
    image_pages = 0  # páginas ocupadas casi por completo por una imagen (escaneos, fotos)

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for p, page in enumerate(pdf.pages):
            heights.append(float(page.height))
            area = float(page.width * page.height) or 1.0
            if any((im["x1"] - im["x0"]) * (im["bottom"] - im["top"]) > area * 0.5 for im in page.images):
                image_pages += 1
            extras: list[Block] = []
            exclude: list[tuple] = []

            try:
                tables = page.find_tables()
            except Exception:
                tables = []
            for t in tables:
                md_table = _table_markdown(t.extract())
                if md_table:
                    extras.append(Block("table", t.bbox[1], p, markdown=md_table))
                    exclude.append(t.bbox)

            if images != "none":
                for top, name, png in page_images(page, p):
                    assets[name] = png
                    extras.append(Block("image", top, p, markdown=f"![Imagen](images/{name})"))

            links = []
            for h in page.hyperlinks or []:
                uri = h.get("uri")
                if uri:
                    links.append(((h["x0"], h["top"], h["x1"], h["bottom"]), uri))

            words = build_structures(page, _page_words(page, exclude, links), exclude)
            total_chars += sum(len(w.text) for w in words)
            lines = _order_columns(_group_lines(words, p), float(page.width))
            page_lines.append(lines)
            page_extras.append(extras)

    n_pages = len(page_lines)
    scanned = bool(n_pages) and image_pages > 0 and total_chars < 20 * n_pages
    if scanned:
        warnings.append(
            "El PDF apenas contiene texto seleccionable (escaneado o escrito a mano). "
            "Usa el Modo IA para transcribirlo; mientras tanto se han extraído las páginas como imágenes."
        )
    has_math = any(w.math for lines in page_lines for ln in lines for w in ln.words)

    drop = _detect_repeated(page_lines, heights)
    page_lines = [[ln for i, ln in enumerate(lines) if (p, i) not in drop] for p, lines in enumerate(page_lines)]

    # Tamaño del texto normal = el más frecuente; los mayores son títulos.
    size_counter: Counter = Counter()
    for lines in page_lines:
        for ln in lines:
            for w in ln.words:
                size_counter[round(w.size * 2) / 2] += len(w.text)
    body_size = size_counter.most_common(1)[0][0] if size_counter else 10.0
    heading_sizes = sorted(
        (s for s, c in size_counter.items() if s >= body_size * 1.15 and c >= 3), reverse=True
    )[:5]
    heading_levels = {s: i + 1 for i, s in enumerate(heading_sizes)}

    blocks: list[Block] = []
    for p in range(n_pages):
        page_blocks = _classify(page_lines[p], body_size, heading_levels)
        # Intercalar tablas e imágenes según su posición vertical.
        for extra in sorted(page_extras[p], key=lambda b: b.top):
            pos = next((i for i, b in enumerate(page_blocks) if b.top > extra.top), len(page_blocks))
            page_blocks.insert(pos, extra)
        # Párrafo partido entre páginas: unirlo con el de la página anterior.
        if (blocks and page_blocks and blocks[-1].kind == "para" and page_blocks[0].kind == "para"
                and not blocks[-1].lines[-1].text.rstrip().endswith(SENTENCE_END)
                and page_blocks[0].lines[0].text[:1].islower()):
            blocks[-1].lines.extend(page_blocks.pop(0).lines)
        blocks.extend(page_blocks)

    parts: list[str] = []
    prev: Block | None = None
    for block in blocks:
        rendered = _render_block(block)
        if not rendered.strip():
            continue
        sep = "\n" if prev is not None and prev.kind == "list" and block.kind == "list" else "\n\n"
        parts.append((sep if parts else "") + rendered)
        prev = block
    markdown = "".join(parts).strip() + "\n"
    if has_math and not scanned:
        warnings.append(
            "Este PDF contiene fórmulas y se han reconstruido en LaTeX (potencias, fracciones, raíces, "
            "sumatorias, integrales, sistemas y matrices). Revísalas en la vista previa; si alguna no "
            "quedó bien, vuelve a convertir el PDF con el Modo IA."
        )
    return ConversionResult(markdown, assets, "extracted", n_pages, warnings, has_math, scanned)

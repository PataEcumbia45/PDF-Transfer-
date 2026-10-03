"""Recupera los caracteres que un PDF no traduce a Unicode.

Algunos generadores de PDF, como "Microsoft: Imprimir a PDF" (muy habitual
para guardar documentos de Word), no incluyen la tabla que traduce cada glifo
a su carácter. El texto normal se recupera con la tabla ``cmap`` de la fuente
incrustada, pero las variantes que usa el editor de ecuaciones de Word
(dígitos y letras de exponente, signo menos pequeño, paréntesis y llaves
grandes) no están en esa tabla y salen como ``(cid:2870)``.

Como la fuente va incrustada, la equivalencia se reconstruye desde ella misma:

- ``cmap``: glifo → carácter;
- ``GSUB``: variante → glifo base (p. ej. el "2" de exponente → "2");
- ``MATH``: variante grande o pieza → delimitador base (llave de 3 filas → "{").

También se recupera el nombre real de la fuente ("Cambria Math"), que estos
PDF sustituyen por "F1", "F2"…, para poder detectar las fórmulas.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

from pypdf import PdfReader

LOGGER = logging.getLogger(__name__)
CID_RE = re.compile(r"^\(cid:(\d+)\)$")
UNKNOWN = "□"


@dataclass
class FontInfo:
    name: str = ""
    glyphs: dict[int, str] = field(default_factory=dict)
    # Glifos estirables (llaves, paréntesis grandes): extensión vertical en "em" (yMin, yMax).
    tall: dict[int, tuple[float, float]] = field(default_factory=dict)


def _base_map(font) -> dict[str, str]:
    """Variante → glifo base, según GSUB y MATH."""
    base: dict[str, str] = {}
    if "GSUB" in font:
        for lookup in font["GSUB"].table.LookupList.Lookup or []:
            for sub in lookup.SubTable:
                kind = lookup.LookupType
                if kind == 7:  # extensión
                    kind, sub = sub.ExtensionLookupType, sub.ExtSubTable
                if kind == 1:
                    for a, b in sub.mapping.items():
                        base.setdefault(b, a)
                elif kind == 3:
                    for a, alternatives in sub.alternates.items():
                        for b in alternatives:
                            base.setdefault(b, a)
    if "MATH" in font and font["MATH"].table.MathVariants:
        mv = font["MATH"].table.MathVariants
        for coverage, constructions in ((mv.VertGlyphCoverage, mv.VertGlyphConstruction),
                                        (mv.HorizGlyphCoverage, mv.HorizGlyphConstruction)):
            if coverage is None:
                continue
            for glyph, cons in zip(coverage.glyphs, constructions):
                for rec in cons.MathGlyphVariantRecord or []:
                    if rec.VariantGlyph != glyph:
                        base.setdefault(rec.VariantGlyph, glyph)
                if cons.GlyphAssembly:
                    for part in cons.GlyphAssembly.PartRecords:
                        base.setdefault(part.glyph, glyph)
    return base


def _font_info(data: bytes) -> FontInfo:
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(data), lazy=True)
    name = ""
    if "name" in font:
        name = font["name"].getDebugName(4) or font["name"].getDebugName(1) or ""
    order = font.getGlyphOrder()
    to_char: dict[str, str] = {}
    for codepoint, glyph in (font.getBestCmap() or {}).items():
        to_char.setdefault(glyph, chr(codepoint))
    base = _base_map(font)

    stretchy: set[str] = set()
    if "MATH" in font and font["MATH"].table.MathVariants and font["MATH"].table.MathVariants.VertGlyphCoverage:
        stretchy = set(font["MATH"].table.MathVariants.VertGlyphCoverage.glyphs)

    def resolve(glyph: str) -> str | None:
        seen = set()
        while glyph not in to_char and glyph in base and glyph not in seen:
            seen.add(glyph)
            glyph = base[glyph]
        if glyph in to_char:
            return to_char[glyph]
        if glyph in stretchy:
            # Delimitador estirable que el documento nunca usa "en tamaño normal", así que la
            # fuente recortada no conserva su carácter. En un taller o un examen es casi
            # siempre la llave de una función a trozos o de un sistema de ecuaciones.
            return "{"
        return None

    glyphs = {}
    for gid, glyph in enumerate(order):
        char = resolve(glyph)
        if char:
            glyphs[gid] = char

    # Altura real de las variantes grandes de los delimitadores: el PDF las coloca con el
    # tamaño de letra normal, pero ocupan varias líneas.
    tall: dict[int, tuple[float, float]] = {}
    variant_bases = {g for g in base if base[g] in stretchy}
    if variant_bases and "glyf" in font:
        upem = font["head"].unitsPerEm
        for glyph in variant_bases:
            g = font["glyf"][glyph]
            if getattr(g, "numberOfContours", 0) and hasattr(g, "yMax"):
                tall[order.index(glyph)] = (g.yMin / upem, g.yMax / upem)
    return FontInfo(name=name, glyphs=glyphs, tall=tall)


class GlyphFixer:
    """Corrige el texto y el nombre de fuente de los caracteres de pdfplumber."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._fonts: dict[str, FontInfo] | None = None
        self.unknown = 0

    def _load(self) -> dict[str, FontInfo]:
        fonts: dict[str, FontInfo] = {}
        try:
            reader = PdfReader(io.BytesIO(self._data))
            for page in reader.pages:
                resources = page.get("/Resources")
                if not resources or "/Font" not in resources:
                    continue
                for ref in resources["/Font"].values():
                    font = ref.get_object()
                    base_name = str(font.get("/BaseFont", "")).lstrip("/")
                    if not base_name or base_name in fonts:
                        continue
                    descriptor = font
                    if "/DescendantFonts" in font:
                        descendant = font["/DescendantFonts"][0].get_object()
                        if str(descendant.get("/CIDToGIDMap", "/Identity")) != "/Identity":
                            continue  # el código del carácter no es el número de glifo
                        descriptor = descendant
                    desc = descriptor.get("/FontDescriptor")
                    if desc is None:
                        continue
                    stream = desc.get_object().get("/FontFile2")
                    if stream is None:
                        continue
                    try:
                        fonts[base_name] = _font_info(stream.get_object().get_data())
                    except Exception as exc:  # fuente dañada o no TrueType
                        LOGGER.debug("No se pudo leer la fuente %s: %s", base_name, exc)
        except Exception as exc:
            LOGGER.debug("No se pudieron leer las fuentes: %s", exc)
        return fonts

    def fix(self, page) -> None:
        chars = page.chars
        needs = any(c["text"].startswith("(cid:") for c in chars) or any(
            re.search(r"(^|\+)F\d+$", c.get("fontname", "")) for c in chars[:50])
        if not needs:
            return
        if self._fonts is None:
            self._fonts = self._load()
        for c in chars:
            info = self._fonts.get(c.get("fontname", ""))
            if info is None:
                continue
            match = CID_RE.match(c["text"])
            if match:
                gid = int(match.group(1))
                char = info.glyphs.get(gid)
                if char is None:
                    self.unknown += 1
                c["text"] = char or UNKNOWN
                extent = info.tall.get(gid)
                if extent and c.get("matrix"):
                    # Caja real del delimitador grande, a partir de su línea de base.
                    size = float(c["size"])
                    baseline = float(page.height) - float(c["matrix"][5])
                    c["top"] = baseline - extent[1] * size
                    c["bottom"] = baseline - extent[0] * size
                    c["size"] = round((extent[1] - extent[0]) * size, 2)
            if info.name:
                c["fontname"] = info.name.replace(" ", "")

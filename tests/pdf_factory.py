"""PDF de prueba maquetados como los de Word o LaTeX.

Las fórmulas se componen igual que en esos programas: el texto se coloca por
coordenadas, las rayas de fracción y de raíz se dibujan como líneas, y las
potencias y los límites van en letra más pequeña y desplazada. Así se prueba
la lectura de fórmulas con PDF realistas sin depender de Word ni de LaTeX.
"""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_DIR = "/usr/share/fonts/truetype/dejavu/"
TEXT_FONT = "DejaVuSerif"
MATH_FONT = "CambriaMath"  # el nombre que usa Word para las ecuaciones

pdfmetrics.registerFont(TTFont(TEXT_FONT, FONT_DIR + "DejaVuSerif.ttf"))
pdfmetrics.registerFont(TTFont(MATH_FONT, FONT_DIR + "DejaVuSerif.ttf"))

WIDTH, HEIGHT = A4


class MathPage:
    """Lienzo con coordenadas desde arriba (como pdfplumber) y ayudas para fórmulas."""

    def __init__(self) -> None:
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4)

    def width(self, text: str, size: float, font: str = MATH_FONT) -> float:
        return pdfmetrics.stringWidth(text, font, size)

    def text(self, x: float, baseline: float, text: str, size: float = 12, font: str = TEXT_FONT) -> float:
        self.c.setFont(font, size)
        self.c.drawString(x, HEIGHT - baseline, text)
        return x + self.width(text, size, font)

    def rule(self, x0: float, x1: float, y: float, thickness: float = 0.6) -> None:
        self.c.setLineWidth(thickness)
        self.c.line(x0, HEIGHT - y, x1, HEIGHT - y)

    # --- piezas de fórmula: listas de (texto, "sup" | "sub" | None) ---------------

    def run_width(self, parts, size: float) -> float:
        return sum(self.width(t, size * (0.7 if k else 1)) for t, k in parts)

    def run(self, x: float, baseline: float, parts, size: float = 12) -> float:
        for t, kind in parts:
            if kind == "sup":
                x = self.text(x, baseline - size * 0.38, t, size * 0.7, MATH_FONT)
            elif kind == "sub":
                x = self.text(x, baseline + size * 0.2, t, size * 0.7, MATH_FONT)
            else:
                x = self.text(x, baseline, t, size, MATH_FONT)
        return x

    def fraction(self, x: float, baseline: float, num, den, size: float = 12) -> float:
        """Fracción con numerador y denominador (listas de piezas o funciones de dibujo)."""
        num_w = num[0](None) if callable(num[0]) else self.run_width(num, size)
        den_w = self.run_width(den, size)
        width = max(num_w, den_w) + 4
        axis = baseline - size * 0.3
        self.rule(x, x + width, axis)
        if callable(num[0]):
            num[0](x + (width - num_w) / 2, axis - size * 0.35)
        else:
            self.run(x + (width - num_w) / 2, axis - size * 0.35, num, size)
        self.run(x + (width - den_w) / 2, axis + size * 0.95, den, size)
        return x + width + 2

    def sqrt(self, x: float, baseline: float, body, size: float = 12) -> float:
        x_body = self.text(x, baseline, "√", size, MATH_FONT)
        end = self.run(x_body + 1, baseline, body, size)
        self.rule(x_body, end + 1, baseline - size * 0.9)
        return end + 2

    def big_op(self, x: float, baseline: float, symbol: str, below: str, above: str, size: float = 12) -> float:
        big = size * 1.6
        w = self.width(symbol, big)
        center = x + w / 2
        self.text(x, baseline + size * 0.25, symbol, big, MATH_FONT)
        small = size * 0.7
        self.text(center - self.width(below, small) / 2, baseline + size * 1.1, below, small, MATH_FONT)
        self.text(center - self.width(above, small) / 2, baseline - size * 1.25, above, small, MATH_FONT)
        return x + w + 3

    def pdf(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def taller_pdf() -> bytes:
    """Un taller de matemáticas de una página, con fórmulas típicas."""
    p = MathPage()
    p.text(60, 70, "Taller de matemáticas", 20)
    p.text(60, 110, "Nombre:", 12)
    p.rule(110, 260, 112)  # línea para escribir el nombre (no es una fracción)
    p.text(60, 128, "Lea con atención cada enunciado antes de responder.", 12)

    # 1. Ecuación con potencias
    x = p.text(60, 170, "1. Resuelva la ecuación ", 12)
    p.run(x, 170, [("x", None), ("2", "sup"), (" + 5x + 6 = 0", None)])

    # Fórmula general (fracción con una raíz en el numerador)
    x = p.run(200, 225, [("x = ", None)])

    radicand = [("b", None), ("2", "sup"), (" − 4ac", None)]

    def numerator(nx, baseline=None):
        """Sin posición devuelve su ancho; con posición dibuja "−b ± √(b² − 4ac)"."""
        parts = [("−b ± ", None)]
        if nx is None:
            return p.run_width(parts, 12) + p.width("√", 12) + p.run_width(radicand, 12) + 4
        after = p.run(nx, baseline, parts)
        return p.sqrt(after, baseline, radicand)

    p.fraction(x, 225, [numerator], [("2a", None)])

    # 2. Suma de fracciones en el texto
    x = p.text(60, 285, "2. Simplifique ", 12)
    x = p.fraction(x, 285, [("3", None)], [("4", None)])
    x = p.text(x, 285, " + ", 12, MATH_FONT)
    x = p.fraction(x, 285, [("1", None)], [("2", None)])
    p.text(x, 285, " y exprese el resultado.", 12)

    # 3. Sumatoria con límites
    x = p.text(60, 350, "3. Calcule ", 12)
    x = p.run(x, 350, [("S = ", None)])
    x = p.big_op(x, 350, "∑", "k=1", "n", 12)
    x = p.run(x, 350, [("k", None), ("2", "sup")])
    p.text(x, 350, ".", 12)

    # 4. Integral definida
    x = p.text(60, 410, "4. Evalúe ", 12)
    x = p.big_op(x, 410, "∫", "0", "π", 12)
    x = p.run(x, 410, [("sen(x) dx", None)])
    p.text(x, 410, ".", 12)

    p.text(60, 460, "Texto final sin fórmulas: entregar el viernes.", 12)
    return p.pdf()


# Nombres de fuente como los de LaTeX (Computer Modern), con el mismo archivo de glifos.
for _name in ("CMMI10", "CMR10", "CMEX10"):
    pdfmetrics.registerFont(TTFont(_name, FONT_DIR + "DejaVuSerif.ttf"))


def algebra_pdf() -> bytes:
    """Sistema de ecuaciones, matriz, determinante y fracción anidada, compuestos como en LaTeX."""
    p = MathPage()
    size = 12
    p.text(60, 70, "Taller de álgebra lineal", 18, "CMR10")

    # 1. Sistema de ecuaciones: llave del tamaño normal centrada entre las filas (como Word).
    p.text(60, 130, "1. Resuelva el sistema", size, "CMR10")
    base = 130
    p.text(220, base, "{", size * 2.2, "CMEX10")
    p.run(236, base - size * 0.65, [("2x + 3y = 7", None)])
    p.run(236, base + size * 0.75, [("x − y = 1", None)])

    # 2. Matriz entre paréntesis grandes (como LaTeX) y su determinante con barras.
    x = p.text(60, 215, "2. Sea ", size, "CMR10")
    x = p.run(x, 215, [("A = ", None)])
    p.text(x, 215 + size * 0.35, "(", size * 2.4, "CMEX10")
    col1, col2 = x + 14, x + 34
    p.run(col1, 215 - size * 0.65, [("1", None)])
    p.run(col2, 215 - size * 0.65, [("2", None)])
    p.run(col1, 215 + size * 0.75, [("3", None)])
    p.run(col2, 215 + size * 0.75, [("4", None)])
    x = p.text(col2 + 12, 215 + size * 0.35, ")", size * 2.4, "CMEX10")
    x = p.text(x + 4, 215, ", calcule ", size, "CMR10")
    p.text(x, 215 + size * 0.35, "|", size * 2.4, "CMEX10")
    c1, c2 = x + 8, x + 28
    p.run(c1, 215 - size * 0.65, [("a", None)])
    p.run(c2, 215 - size * 0.65, [("b", None)])
    p.run(c1, 215 + size * 0.75, [("c", None)])
    p.run(c2, 215 + size * 0.75, [("d", None)])
    x = p.text(c2 + 12, 215 + size * 0.35, "|", size * 2.4, "CMEX10")
    p.text(x + 2, 215, ".", size, "CMR10")

    # 3. Fracción anidada: 1 / (1 + 1/x). La raya de la fracción de fuera es un
    #    rectángulo relleno, como las que dibuja LaTeX.
    x = p.text(60, 300, "3. Simplifique ", size, "CMR10")
    inner_w = p.width("x", size) + 4
    den_w = p.width("1 + ", size) + inner_w
    outer_w = den_w + 4
    axis = 300 - size * 0.3
    p.c.rect(x, HEIGHT - axis - 0.3, outer_w, 0.6, stroke=0, fill=1)
    p.text(x + (outer_w - p.width("1", size)) / 2, axis - size * 0.35, "1", size, "CMMI10")
    den_base = axis + size * 1.6
    dx = p.text(x + 2, den_base, "1 + ", size, "CMR10")
    p.fraction(dx, den_base, [("1", None)], [("x", None)], size * 0.85)
    p.text(x + outer_w + 4, 300, ".", size, "CMR10")

    p.text(60, 380, "Justifique cada paso de su respuesta.", size, "CMR10")
    return p.pdf()

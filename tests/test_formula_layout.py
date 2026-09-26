"""Lectura local de fórmulas "en dos pisos" en PDF maquetados como Word o LaTeX."""

import re

import pytest
from weasyprint import HTML

from app.converters.math_render import renderer
from app.converters.pdf_to_md import pdf_to_markdown

from .pdf_factory import MathPage, algebra_pdf, taller_pdf

FORMULA_RE = re.compile(r"\$\$\n(.+?)\n\$\$|\$([^$\n]+)\$", re.S)


def formulas(md: str) -> list[str]:
    return [a or b for a, b in FORMULA_RE.findall(md)]


def assert_valid_latex(md: str) -> None:
    if not renderer.available:
        pytest.skip("MathJax (Node) no disponible")
    for tex in formulas(md):
        result = renderer.render(tex, display=True)
        assert result.error is None, f"LaTeX no válido: {tex!r} → {result.error}"


def test_word_like_worksheet():
    md = pdf_to_markdown(taller_pdf()).markdown
    assert "$x^{2} + 5x + 6 = 0$" in md
    assert "$$\nx = \\frac{-b \\pm \\sqrt{b^{2} - 4ac}}{2a}\n$$" in md  # fracción con raíz dentro
    assert "$\\frac{3}{4} + \\frac{1}{2}$" in md  # dos fracciones seguidas en el texto
    assert "$S = \\sum_{k=1}^{n}k^{2}$" in md  # sumatoria con límites
    assert "$\\int_{0}^{\\pi} \\operatorname{sen}(x) dx$" in md  # integral definida
    assert re.search(r"Nombre: ?_{3,}\n", md)  # espacio para rellenar
    assert "Texto final sin fórmulas: entregar el viernes." in md
    assert_valid_latex(md)


def test_latex_like_worksheet():
    md = pdf_to_markdown(algebra_pdf()).markdown
    assert "$\\begin{cases} 2x + 3y = 7 \\\\ x - y = 1 \\end{cases}$" in md
    assert "$A = \\begin{pmatrix} 1 & 2 \\\\ 3 & 4 \\end{pmatrix}$" in md
    assert "$\\begin{vmatrix} a & b \\\\ c & d \\end{vmatrix}$" in md
    assert "$\\frac{1}{1 + \\frac{1}{x}}$" in md  # fracción anidada
    assert_valid_latex(md)


def test_delimiter_built_from_pieces():
    p = MathPage()
    x = p.text(60, 100, "Resuelva ", 12)
    for i, ch in enumerate("⎧⎨⎩"):
        p.text(x, 88 + i * 12, ch, 12)
    p.run(x + 10, 91, [("x + y = 3", None)])
    p.run(x + 10, 103, [("x − y = 1", None)])
    p.run(x + 10, 115, [("2x + z = 4", None)])
    md = pdf_to_markdown(p.pdf()).markdown
    assert "\\begin{cases} x + y = 3 \\\\ x - y = 1 \\\\ 2x + z = 4 \\end{cases}" in md
    assert_valid_latex(md)


def test_no_false_formulas_in_ordinary_documents():
    html = """<body style="font-family:'DejaVu Serif';font-size:11pt">
      <p style="padding-left:30px;text-indent:-22px">(a) Una pregunta larga que ocupa varias líneas para
      comprobar que la sangría francesa no se confunde con una matriz ni con un sistema de ecuaciones.</p>
      <p style="padding-left:30px;text-indent:-22px">(b) Otra pregunta (con paréntesis) y [corchetes] que
      también ocupa más de una línea de texto normal en el documento de prueba.</p>
      <p>Visite <a href="https://ej.com">nuestra página web</a> para más detalles
      y siga leyendo en la línea siguiente sin problemas.</p><hr>
      <p><u>Texto subrayado</u> en medio de una frase normal,<br>y otra línea justo debajo.</p>
      <table style="border-collapse:collapse"><tr><td style="border:1px solid">a</td>
      <td style="border:1px solid">b</td></tr><tr><td style="border:1px solid">1</td>
      <td style="border:1px solid">2</td></tr></table></body>"""
    result = pdf_to_markdown(HTML(string=html).write_pdf())
    md = result.markdown
    assert "$" not in md and "___" not in md
    assert not result.has_math
    assert "[nuestra página web](https://ej.com)" in md
    assert "Texto subrayado en medio de una frase normal, y otra línea justo debajo." in md
    assert "| a | b |" in md

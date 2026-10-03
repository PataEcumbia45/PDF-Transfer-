"""Lectura local de fórmulas "en dos pisos" en PDF maquetados como Word o LaTeX."""

import re

import pytest
from weasyprint import HTML

from app.converters.math_render import renderer
from app.converters.pdf_to_md import pdf_to_markdown

from .pdf_factory import MathPage, algebra_pdf, taller_pdf

FORMULA_RE = re.compile(r"\$\$\n\s*(.+?)\n\s*\$\$|\$([^$\n]+)\$", re.S)


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
    # Fracción con raíz dentro, como ecuación en bloque anidada en el ejercicio 1.
    assert "   $$\n   x = \\frac{-b \\pm \\sqrt{b^{2} - 4ac}}{2a}\n   $$" in md
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


def test_university_worksheet_structure():
    """Estructura típica de un taller: encabezado centrado, ejercicios, función a trozos e incisos."""
    from .pdf_factory import TEXT_FONT, WIDTH

    p = MathPage()
    for i, title in enumerate(["Universidad de Ejemplo", "Probabilidad y Estadística I"]):
        p.text((WIDTH - p.width(title, 11, TEXT_FONT)) / 2, 70 + i * 14, title, 11)
    p.text(60, 120, "1.", 11)
    p.text(80, 120, "Suponga que el tiempo de atención es una v.a. continua con la siguiente f.d.p.", 11)
    # f(x) = { k(x+1),  0 ≤ x ≤ 1   /   0,  otro caso   (centrada, llave del tamaño normal)
    x = p.run(220, 160, [("f(x) = ", None)], 11)
    p.text(x, 160 + 3, "{", 24)
    p.run(x + 12, 152, [("k(x + 1),", None)], 11)
    p.run(x + 80, 152, [("0 ≤ x ≤ 1", None)], 11)
    p.run(x + 12, 168, [("0,", None)], 11)
    p.run(x + 80, 168, [("otro caso", None)], 11)
    p.text(80, 200, "a)", 11)
    p.text(98, 200, "Encuentre el valor de k que hace que f(x) sea función de densidad", 11)
    x = p.text(80, 215, "b)", 11)
    x = p.text(98, 215, "Hallar ", 11)
    p.run(x, 215, [("E(X) y V(X)", None)], 11)
    x = p.text(60, 245, "2.", 11)
    x = p.text(80, 245, "Resuelva ", 11)
    p.run(x, 245, [("x + y = 3", None)], 11)

    md = pdf_to_markdown(p.pdf()).markdown
    assert "Universidad de Ejemplo\n\nProbabilidad y Estadística I" in md  # líneas centradas separadas
    assert md.count("1. Suponga") == 1
    assert "\\begin{cases} k(x + 1), & 0 \\leq x \\leq 1 \\\\ 0, & \\text{ otro caso } \\end{cases}" in md
    assert "   a) Encuentre el valor de k" in md  # inciso con su letra, dentro del ejercicio 1
    assert "\\\n   b) Hallar $E(X) \\text{ y } V(X)$" in md  # "y" entre expresiones es una palabra
    assert "$x + y = 3$" in md  # la variable y no se toca
    assert_valid_latex(md)

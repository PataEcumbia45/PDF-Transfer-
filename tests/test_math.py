from types import SimpleNamespace

import pytest
from weasyprint import HTML

from app import config
from app.converters import ai_transcribe
from app.converters.md_to_pdf import PdfOptions, markdown_to_pdf, render_html
from app.converters.math_render import renderer
from app.converters.pdf_to_md import pdf_to_markdown

TALLER = r"""# Taller de álgebra

Resuelva $ax^2 + bx + c = 0$ con la fórmula general:

$$
x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}
$$

Sistema: $\begin{cases} 2x + 3y = 7 \\ x - y = 1 \end{cases}$ y matriz
$A = \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}$.

\begin{align}
f(x) &= (x+1)^2 \\
     &= x^2 + 2x + 1
\end{align}

```math
\sum_{k=1}^{n} k = \frac{n(n+1)}{2}
```

El cuaderno cuesta $5 y el libro $10.
"""

pytestmark = pytest.mark.skipif(not renderer.available, reason="MathJax (Node) no disponible")


def test_formulas_render_as_svg():
    html = render_html(TALLER)
    assert html.count('class="math math-inline"') == 3
    assert html.count('class="math math-display"') == 3
    assert 'class="math-error"' not in html
    assert "cuesta $5 y el libro $10" in html  # los importes no son fórmulas


def test_invalid_formula_keeps_source():
    html = render_html(r"Mal: $\frac{1}{$ fin")
    assert "math-error" in html and r"$\frac{1}{$" in html


def test_math_round_trip_is_exact():
    pdf = markdown_to_pdf(TALLER)
    assert pdf_to_markdown(pdf).markdown == TALLER


def test_math_pdf_renders_pages():
    pdf = markdown_to_pdf(TALLER, options=PdfOptions(embed_source=False))
    assert pdf.startswith(b"%PDF") and len(pdf) > 5000


def test_simple_formulas_read_from_foreign_pdf():
    html = """<html><body style="font-family:'DejaVu Serif';font-size:12pt">
      <p>1. El área es A = πr<sup>2</sup> y el agua es H<sub>2</sub>O.</p>
      <p>2. Resuelva x<sup>2</sup> + 3x − 4 = 0 para x ∈ ℝ.</p>
      <p>3. Si α + β = 90°, calcule c ≈ 3 × 10<sup>8</sup> m/s.</p>
      <p>A veces el agua o el aceite. Hay 3 alumnos y a todos les gusta.</p>
    </body></html>"""
    result = pdf_to_markdown(HTML(string=html).write_pdf())
    md = result.markdown
    assert result.has_math
    assert r"$A = \pi r^{2}$" in md
    assert "$H_{2}O$" in md
    assert r"$x^{2} + 3x - 4 = 0$" in md
    assert r"$x \in \mathbb{R}$" in md
    assert r"$\alpha + \beta = 90^{\circ}$" in md
    assert r"$c \approx 3 \times 10^{8}$" in md
    assert "A veces el agua o el aceite. Hay 3 alumnos y a todos les gusta." in md
    assert any("Modo IA" in w for w in result.warnings)


# ------------------------------------------------------------------ Modo IA


class FakeMessages:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies[len(self.calls) - 1]
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class FakeClient:
    def __init__(self, replies):
        self.messages = FakeMessages(replies)
        self.beta = SimpleNamespace(messages=self.messages)


@pytest.fixture
def ai_on(monkeypatch):
    settings = config.Settings(ai_api_key="clave-de-prueba")
    monkeypatch.setattr(ai_transcribe, "settings", settings)
    monkeypatch.setattr("app.main.settings", settings)
    fake = FakeClient([r"# Página\n\n$$\frac{1}{2}$$\n\n[[FIGURA]]\n\nFin."] * 10)
    monkeypatch.setattr(ai_transcribe, "_client", lambda: fake)
    return fake


def _foreign_pdf(png_bytes=None):
    md = "# Ejercicio\n\nTexto con $x^2$.\n" + ("\n![f](f.png)\n" if png_bytes else "")
    return markdown_to_pdf(md, {"f.png": png_bytes} if png_bytes else {}, PdfOptions(embed_source=False))


def test_ai_transcription_places_figures(ai_on, png_bytes):
    result = ai_transcribe.transcribe_pdf(_foreign_pdf(png_bytes))
    assert result.source == "ai"
    assert r"$$\frac{1}{2}$$" in result.markdown
    assert "[[FIGURA]]" not in result.markdown
    name = next(iter(result.assets))
    assert f"![Figura](images/{name})" in result.markdown
    call = ai_on.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["extra_body"] == {"fallbacks": "default"}
    assert call["messages"][0]["content"][0]["type"] == "image"


def test_api_ai_mode(client, ai_on):
    res = client.post("/api/convert/pdf-to-md", files={"file": ("t.pdf", _foreign_pdf())}, data={"mode": "ai"})
    body = res.json()
    assert res.status_code == 200 and body["source"] == "ai"
    assert r"\frac{1}{2}" in body["markdown"]


def test_api_auto_uses_ai_only_for_math(client, ai_on):
    plain = markdown_to_pdf("# Hola\n\nSolo texto.", options=PdfOptions(embed_source=False))
    res = client.post("/api/convert/pdf-to-md", files={"file": ("p.pdf", plain)})
    assert res.json()["source"] == "extracted"
    assert not ai_on.messages.calls

    html = "<p style='font-size:12pt'>Resuelva x<sup>2</sup> − 4 = 0.</p>"
    res = client.post("/api/convert/pdf-to-md", files={"file": ("m.pdf", HTML(string=html).write_pdf())})
    assert res.json()["source"] == "ai"


def test_api_ai_page_limit(client, ai_on):
    long_md = "\n\n".join(f"# P{i}\n\n<div style='page-break-after: always'></div>" for i in range(7))
    pdf = markdown_to_pdf(long_md, options=PdfOptions(embed_source=False))
    res = client.post("/api/convert/pdf-to-md", files={"file": ("l.pdf", pdf)}, data={"mode": "ai"})
    assert res.status_code == 413
    assert "Modo IA" in res.json()["detail"]


def test_api_ai_mode_disabled(client):
    res = client.post("/api/convert/pdf-to-md", files={"file": ("t.pdf", _foreign_pdf())}, data={"mode": "ai"})
    assert res.status_code == 400


def test_scanned_pdf_goes_to_ai_in_auto_mode(client, ai_on):
    import io

    from PIL import Image, ImageDraw

    page = Image.new("RGB", (1240, 1754), "white")
    ImageDraw.Draw(page).text((100, 100), "x^2 + 1 = 0 (manuscrito)", fill="black")
    buf = io.BytesIO()
    page.save(buf, format="PDF", resolution=150)
    local = pdf_to_markdown(buf.getvalue())
    assert local.scanned
    res = client.post("/api/convert/pdf-to-md", files={"file": ("scan.pdf", buf.getvalue())})
    assert res.json()["source"] == "ai"

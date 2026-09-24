import io

from pypdf import PdfReader, PdfWriter
from weasyprint import HTML

from app.converters.md_to_pdf import PdfOptions, markdown_to_pdf, render_html
from app.converters.pdf_to_md import pdf_to_markdown

from .conftest import SAMPLE_MD


def test_round_trip_is_lossless(png_bytes):
    pdf = markdown_to_pdf(SAMPLE_MD, {"images/logo.png": png_bytes})
    result = pdf_to_markdown(pdf)
    assert result.source == "embedded"
    assert result.markdown == SAMPLE_MD  # byte a byte
    assert result.assets == {"images/logo.png": png_bytes}


def test_round_trip_every_theme_and_page_size():
    for theme in ("moderno", "clasico", "tecnico"):
        for size in ("A4", "Letter"):
            pdf = markdown_to_pdf(SAMPLE_MD, options=PdfOptions(theme=theme, page_size=size))
            assert pdf_to_markdown(pdf).markdown == SAMPLE_MD


def test_embedded_source_ignored_when_pdf_was_modified():
    pdf = markdown_to_pdf(SAMPLE_MD)
    reader = PdfReader(io.BytesIO(pdf))
    writer = PdfWriter(clone_from=reader)
    writer.add_blank_page()  # el documento ya no coincide con su fuente
    out = io.BytesIO()
    writer.write(out)
    result = pdf_to_markdown(out.getvalue())
    assert result.source == "extracted"


def test_reconstructs_structure_from_foreign_pdf():
    # Sin fuente incrustada: se reconstruye analizando el diseño.
    pdf = markdown_to_pdf(SAMPLE_MD, options=PdfOptions(embed_source=False))
    result = pdf_to_markdown(pdf, images="none")
    md = result.markdown
    assert result.source == "extracted"
    assert "# Documento de prueba" in md
    assert "## Lista" in md
    assert "**negrita**" in md
    assert "*cursiva*" in md
    assert "`código`" in md
    assert "[enlace](https://example.com/ruta)" in md
    assert "áéíóú ñ ¿? ¡! €" in md
    assert "- Primero" in md and "  - Anidado" in md
    assert "1. Uno" in md and "2. Dos" in md
    assert "| Nombre | Valor |" in md and "| beta | 2 |" in md
    assert "```\ndef suma(a, b):\n    return a + b\n```" in md


def test_headers_footers_and_columns():
    para = "Texto de ejemplo para rellenar la columna con contenido suficiente. " * 6
    html = f"""<html><head><style>
      @page {{ size: A4; margin: 25mm 20mm;
        @top-center {{ content: "Cabecera repetida"; font-size: 8pt; }}
        @bottom-center {{ content: "Página " counter(page); font-size: 8pt; }} }}
      body {{ font-family: 'DejaVu Serif'; font-size: 11pt; }}
      h1 {{ font-size: 22pt; }} h2 {{ font-size: 16pt; }}
      .cols {{ columns: 2; column-gap: 12mm; }}
    </style></head><body>
      <h1>Título</h1>
      <div class="cols"><p>INICIO {para}</p><p>MEDIO {para}</p><p>FINAL {para}</p></div>
      <h2>Después</h2>
      {''.join(f'<p>Relleno {i}. {para}</p>' for i in range(12))}
    </body></html>"""
    pdf = HTML(string=html).write_pdf()
    md = pdf_to_markdown(pdf).markdown
    assert "Cabecera repetida" not in md
    assert "Página" not in md
    assert md.index("INICIO") < md.index("MEDIO") < md.index("FINAL") < md.index("## Después")


def test_images_are_extracted(png_bytes):
    md = "# Con imagen\n\n![foto](foto.png)\n"
    pdf = markdown_to_pdf(md, {"foto.png": png_bytes}, PdfOptions(embed_source=False))
    result = pdf_to_markdown(pdf)
    assert len(result.assets) == 1
    name = next(iter(result.assets))
    assert f"](images/{name})" in result.markdown
    assert result.assets[name].startswith(b"\x89PNG")


def test_local_files_are_never_read(tmp_path):
    secret = tmp_path / "secreto.txt"
    secret.write_text("NO-DEBE-APARECER")
    md = f'<img src="file://{secret}">\n\n![x](file://{secret})\n\n<link rel="stylesheet" href="file://{secret}">'
    pdf = markdown_to_pdf(md, options=PdfOptions(embed_source=False))
    assert b"NO-DEBE-APARECER" not in pdf


def test_preview_html_contains_theme():
    html = render_html("# Hola", theme="clasico", for_preview=True)
    assert "<h1>Hola</h1>" in html
    assert "Liberation Serif" in html

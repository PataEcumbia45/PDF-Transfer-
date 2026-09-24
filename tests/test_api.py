import base64
import io
import zipfile

import pytest

from app import config, limits
from app.converters.md_to_pdf import markdown_to_pdf

from .conftest import SAMPLE_MD


def test_health_and_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    cfg = client.get("/api/config").json()
    assert cfg["plan"]["id"] == "free"
    assert {t["id"] for t in cfg["themes"]} == {"moderno", "clasico", "tecnico"}


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "PDF Transfer" in res.text


def test_md_to_pdf_and_back_via_api(client, png_bytes):
    res = client.post(
        "/api/convert/md-to-pdf",
        data={"markdown": SAMPLE_MD, "filename": "prueba", "theme": "tecnico"},
        files=[("assets", ("images/logo.png", png_bytes, "image/png"))],
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "prueba.pdf" in res.headers["content-disposition"]

    back = client.post("/api/convert/pdf-to-md", files={"file": ("prueba.pdf", res.content, "application/pdf")})
    body = back.json()
    assert body["source"] == "embedded"
    assert body["markdown"] == SAMPLE_MD
    assert base64.b64decode(body["assets"]["images/logo.png"]) == png_bytes


def test_md_file_upload_and_output_formats(client):
    res = client.post("/api/convert/md-to-pdf", files={"file": ("notas.md", SAMPLE_MD.encode(), "text/markdown")})
    assert res.status_code == 200
    pdf = res.content

    md = client.post("/api/convert/pdf-to-md", files={"file": ("n.pdf", pdf)}, data={"output": "md"})
    assert md.headers["content-type"].startswith("text/markdown")
    assert md.text == SAMPLE_MD

    z = client.post("/api/convert/pdf-to-md", files={"file": ("n.pdf", pdf)}, data={"output": "zip"})
    archive = zipfile.ZipFile(io.BytesIO(z.content))
    assert archive.read("n.md").decode() == SAMPLE_MD


def test_zip_with_images(client, png_bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("proyecto/README.md", SAMPLE_MD)
        zf.writestr("proyecto/images/logo.png", png_bytes)
    res = client.post("/api/convert/md-to-pdf", files={"file": ("proyecto.zip", buf.getvalue(), "application/zip")})
    assert res.status_code == 200
    back = client.post("/api/convert/pdf-to-md", files={"file": ("p.pdf", res.content)}).json()
    assert back["markdown"] == SAMPLE_MD
    assert "images/logo.png" in back["assets"]

    unpacked = client.post("/api/unpack", files={"file": ("proyecto.zip", buf.getvalue())}).json()
    assert unpacked["markdown"] == SAMPLE_MD
    assert "images/logo.png" in unpacked["assets"]


def test_invalid_pdf(client):
    res = client.post("/api/convert/pdf-to-md", files={"file": ("x.pdf", b"no soy un pdf")})
    assert res.status_code == 400
    assert "PDF" in res.json()["detail"]


def test_missing_markdown(client):
    assert client.post("/api/convert/md-to-pdf", data={"theme": "moderno"}).status_code == 400


def test_preview(client):
    res = client.post("/api/preview", json={"markdown": "# Hola\n\n**mundo**"})
    assert "<strong>mundo</strong>" in res.json()["html"]


def test_bundle_rejects_path_traversal(client):
    ok = client.post("/api/bundle", json={"name": "x", "files": [{"path": "a/b.md", "text": "hola"}]})
    assert zipfile.ZipFile(io.BytesIO(ok.content)).read("a/b.md") == b"hola"
    bad = client.post("/api/bundle", json={"files": [{"path": "../../etc/passwd", "text": "x"}]})
    assert bad.status_code == 400


def test_page_limit_for_free_plan(client):
    long_md = "\n\n".join(f"# Página {i}\n\n<div style='page-break-after: always'></div>" for i in range(35))
    pdf = markdown_to_pdf(long_md)
    res = client.post("/api/convert/pdf-to-md", files={"file": ("largo.pdf", pdf)})
    assert res.status_code == 413
    assert "páginas" in res.json()["detail"]


def test_rate_limit(client):
    plan = config.PLANS["free"]
    for _ in range(plan.requests_per_hour):
        assert client.post("/api/convert/md-to-pdf", data={"markdown": "hola"}).status_code == 200
    res = client.post("/api/convert/md-to-pdf", data={"markdown": "hola"})
    assert res.status_code == 429
    assert "Retry-After" in res.headers


@pytest.fixture
def paid_key(monkeypatch):
    settings = config.Settings(api_keys={"clave-pro": "pro"})
    monkeypatch.setattr(limits, "settings", settings)
    monkeypatch.setattr("app.main.settings", settings)
    return "clave-pro"


def test_api_keys(client, paid_key):
    cfg = client.get("/api/config", headers={"X-API-Key": paid_key}).json()
    assert cfg["plan"]["id"] == "pro"
    bad = client.post("/api/convert/md-to-pdf", data={"markdown": "x"}, headers={"X-API-Key": "falsa"})
    assert bad.status_code == 401

"""Servidor web: API REST de conversión + interfaz web.

Ejecutar en local:  uvicorn app.main:app --reload
Documentación interactiva de la API:  /docs
"""

from __future__ import annotations

import base64
import binascii
import io
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from . import __version__
from .config import PLANS, Plan, settings
from .converters.md_to_pdf import PAGE_SIZES, THEMES, PdfOptions, is_image_name, markdown_to_pdf, render_html
from .converters.ai_transcribe import AIError, transcribe_pdf
from .converters.pdf_to_md import pdf_to_markdown
from .limits import check_pages, check_size, current_plan, resolve_plan

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_PREVIEW_CHARS = 2_000_000

app = FastAPI(
    title=f"{settings.app_name} API",
    version=__version__,
    description="Convierte PDF a Markdown y Markdown a PDF sin perder contenido.",
)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"], allow_headers=["*"]
    )


MAX_REQUEST_BYTES = max(p.max_file_bytes for p in PLANS.values()) + 10 * 1024 * 1024


@app.middleware("http")
async def security_headers(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_REQUEST_BYTES:
        return JSONResponse({"detail": "La petición es demasiado grande."}, status_code=413)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response


# ------------------------------------------------------------------ utilidades


def _stem(filename: str | None, default: str = "documento") -> str:
    stem = Path(filename or default).stem
    stem = re.sub(r"[^\w\-. ]+", "", stem, flags=re.UNICODE).strip() or default
    return stem[:120]


def _download_headers(filename: str) -> dict[str, str]:
    ascii_name = filename.encode("ascii", "ignore").decode() or "documento"
    return {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"}


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(encoding)
            if encoding == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _read_zip(data: bytes, plan: Plan) -> tuple[str, dict[str, bytes], str]:
    """Extrae de un .zip el Markdown principal y sus imágenes."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, "El archivo .zip no es válido.") from exc
    total = sum(i.file_size for i in archive.infolist())
    if not settings.disable_limits and total > plan.max_file_bytes * 3:
        raise HTTPException(413, "El contenido descomprimido del .zip es demasiado grande.")
    md_files = [n for n in archive.namelist() if n.lower().endswith((".md", ".markdown")) and not n.startswith("__MACOSX")]
    if not md_files:
        raise HTTPException(400, "El .zip no contiene ningún archivo Markdown (.md).")
    main = sorted(md_files, key=lambda n: (n.count("/"), Path(n).name.lower() not in {"readme.md", "index.md"}, n))[0]
    root = posixpath.dirname(main)
    assets: dict[str, bytes] = {}
    for name in archive.namelist():
        if is_image_name(name) and not name.startswith("__MACOSX"):
            rel = posixpath.relpath(name, root) if root else name
            assets[rel] = archive.read(name)
    return _decode_text(archive.read(main)), assets, Path(main).stem


# ----------------------------------------------------------------------- API


@app.get("/api/health", tags=["sistema"])
def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/config", tags=["sistema"])
def config(request: Request):
    """Plan actual y opciones disponibles (usado por la interfaz)."""
    plan, _ = resolve_plan(request)
    return {
        "app_name": settings.app_name,
        "contact_email": settings.contact_email,
        "plan": plan.public(),
        "plans": [p.public() for p in PLANS.values()],
        "themes": [{"id": k, "name": v} for k, v in THEMES.items()],
        "page_sizes": list(PAGE_SIZES),
        "limits_enabled": not settings.disable_limits,
        "ai_enabled": settings.ai_enabled,
    }


@app.post("/api/convert/pdf-to-md", tags=["conversión"])
def convert_pdf_to_md(
    file: UploadFile = File(..., description="Archivo PDF"),
    images: str = Form("embed", description="'embed' (extraer imágenes) o 'none'"),
    output: str = Form("json", description="'json', 'md' o 'zip'"),
    mode: str = Form("auto", description="'auto', 'ai' (Modo IA) o 'standard'"),
    plan: Plan = Depends(current_plan),
):
    """Convierte un PDF a Markdown.

    Si el PDF fue generado por este servicio, se recupera el Markdown original
    exacto (``source = "embedded"``). En otro caso se reconstruye analizando el
    diseño del documento (``source = "extracted"``) o, en Modo IA, Claude
    transcribe cada página con sus fórmulas en LaTeX (``source = "ai"``).
    ``mode = "auto"`` usa el Modo IA solo si el PDF tiene fórmulas o está escaneado.
    """
    data = file.file.read()
    check_size(data, plan)
    if not data.startswith(b"%PDF") and b"%PDF" not in data[:1024]:
        raise HTTPException(400, "El archivo no parece un PDF válido.")
    try:
        pages = len(PdfReader(io.BytesIO(data)).pages)
    except (PdfReadError, ValueError, KeyError) as exc:
        raise HTTPException(400, "No se pudo leer el PDF: el archivo está dañado o no es compatible.") from exc
    check_pages(pages, plan)

    images = "none" if images == "none" else "embed"
    if mode == "ai" and not settings.ai_enabled:
        raise HTTPException(400, "El Modo IA no está configurado en este servidor.")
    try:
        result = pdf_to_markdown(data, images=images)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # PDFs rotos o exóticos
        raise HTTPException(422, "No se pudo convertir este PDF.") from exc

    wants_ai = result.source != "embedded" and (
        mode == "ai" or (mode == "auto" and settings.ai_enabled and (result.has_math or result.scanned))
    )
    if wants_ai:
        if not settings.disable_limits and pages > plan.ai_max_pages:
            message = (f"El Modo IA del plan {plan.name} admite hasta {plan.ai_max_pages} páginas "
                       f"y este PDF tiene {pages}.")
            if mode == "ai":
                raise HTTPException(413, message)
            result.warnings.insert(0, message + " Se ha usado la lectura estándar.")
        else:
            try:
                result = transcribe_pdf(data, images=images)
            except AIError as exc:
                if mode == "ai":
                    raise HTTPException(502, str(exc)) from exc
                result.warnings.insert(0, f"{exc} Se ha usado la lectura estándar.")

    name = _stem(file.filename)
    if output == "md":
        return Response(result.markdown, media_type="text/markdown; charset=utf-8",
                        headers=_download_headers(f"{name}.md"))
    if output == "zip":
        return Response(_zip_bundle(f"{name}.md", result.markdown, result.assets), media_type="application/zip",
                        headers=_download_headers(f"{name}.zip"))
    return {
        "filename": f"{name}.md",
        "markdown": result.markdown,
        "source": result.source,
        "pages": result.pages,
        "warnings": result.warnings,
        "has_math": result.has_math,
        "assets": {k: base64.b64encode(v).decode() for k, v in result.assets.items()},
    }


@app.post("/api/convert/md-to-pdf", tags=["conversión"])
def convert_md_to_pdf(
    file: UploadFile | None = File(None, description="Archivo .md o .zip (Markdown + imágenes)"),
    markdown: str | None = Form(None, description="Markdown como texto (alternativa a 'file')"),
    assets: list[UploadFile] = File(default=[], description="Imágenes referenciadas en el Markdown"),
    filename: str | None = Form(None),
    theme: str = Form("moderno"),
    page_size: str = Form("A4"),
    embed_source: bool = Form(True, description="Incrustar el Markdown original (ida y vuelta sin pérdidas)"),
    plan: Plan = Depends(current_plan),
):
    """Convierte Markdown a un PDF maquetado."""
    images: dict[str, bytes] = {}
    name = _stem(filename)
    if file is not None and file.filename:
        data = file.file.read()
        check_size(data, plan)
        name = _stem(filename or file.filename)
        if file.filename.lower().endswith(".zip") or data[:2] == b"PK":
            markdown, images, zip_name = _read_zip(data, plan)
            name = _stem(filename) if filename else _stem(file.filename, zip_name)
        else:
            markdown = _decode_text(data)
    if markdown is None:
        raise HTTPException(400, "Envía un archivo Markdown o el texto en el campo 'markdown'.")
    check_size(markdown.encode("utf-8"), plan)

    for upload in assets:
        content = upload.file.read()
        check_size(content, plan)
        images[upload.filename or f"imagen-{len(images)}"] = content

    options = PdfOptions(
        theme=theme if theme in THEMES else "moderno",
        page_size=page_size if page_size in PAGE_SIZES else "A4",
        embed_source=embed_source,
        allow_remote_images=settings.allow_remote_images,
    )
    try:
        pdf = markdown_to_pdf(markdown, images, options, creator=settings.app_name)
    except Exception as exc:
        raise HTTPException(422, "No se pudo generar el PDF a partir de este Markdown.") from exc
    return Response(pdf, media_type="application/pdf", headers=_download_headers(f"{name}.pdf"))


@app.post("/api/unpack", tags=["utilidades"])
def unpack_zip(file: UploadFile = File(..., description=".zip con un Markdown y sus imágenes"),
               plan: Plan = Depends(current_plan)):
    """Abre un .zip (Markdown + imágenes) para poder editarlo en la interfaz."""
    data = file.file.read()
    check_size(data, plan)
    markdown, assets, name = _read_zip(data, plan)
    return {
        "filename": f"{_stem(file.filename, name)}.md",
        "markdown": markdown,
        "assets": {k: base64.b64encode(v).decode() for k, v in assets.items()},
    }


class PreviewRequest(BaseModel):
    markdown: str = Field(..., max_length=MAX_PREVIEW_CHARS)
    theme: str = "moderno"
    page_size: str = "A4"


@app.post("/api/preview", tags=["conversión"])
def preview(body: PreviewRequest):
    """HTML con el mismo aspecto que tendrá el PDF (para la vista previa en vivo)."""
    return {"html": render_html(body.markdown, theme=body.theme, page_size=body.page_size, for_preview=True)}


class BundleFile(BaseModel):
    path: str
    text: str | None = None
    base64: str | None = None


class BundleRequest(BaseModel):
    name: str = "documentos"
    files: list[BundleFile] = Field(..., max_length=500)


def _zip_bundle(md_name: str, markdown: str, assets: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(md_name, markdown)
        for name, data in assets.items():
            zf.writestr(f"images/{name}", data)
    return buf.getvalue()


@app.post("/api/bundle", tags=["utilidades"])
def bundle(body: BundleRequest):
    """Empaqueta varios resultados en un .zip (descargas por lotes)."""
    buf = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in body.files:
            path = posixpath.normpath(f.path.replace("\\", "/")).lstrip("/")
            if path.startswith("..") or not path or path == ".":
                raise HTTPException(400, f"Ruta no válida: {f.path}")
            try:
                content = base64.b64decode(f.base64, validate=True) if f.base64 is not None else (f.text or "").encode()
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(400, f"Contenido base64 no válido en {f.path}") from exc
            total += len(content)
            if total > 300 * 1024 * 1024:
                raise HTTPException(413, "El paquete es demasiado grande.")
            zf.writestr(path, content)
    return Response(buf.getvalue(), media_type="application/zip", headers=_download_headers(f"{_stem(body.name)}.zip"))


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


# ------------------------------------------------------------------ interfaz


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

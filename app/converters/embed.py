"""Incrusta el Markdown original dentro de los PDF generados.

Es lo que permite el viaje de ida y vuelta sin pérdidas: un PDF creado por la
aplicación lleva dentro (como archivo adjunto estándar de PDF) el Markdown
exacto y sus imágenes. Al convertirlo de nuevo a Markdown se recupera byte a
byte, en lugar de reconstruirlo aproximadamente a partir del diseño.

Los adjuntos son visibles en cualquier lector de PDF (panel "Adjuntos"), así
que el usuario siempre puede acceder a su fuente.
"""

from __future__ import annotations

import hashlib
import io
import json

from pypdf import PdfReader, PdfWriter

MANIFEST_NAME = "pdf-transfer.json"
SOURCE_NAME = "source.md"
ASSET_PREFIX = "assets/"
FORMAT_VERSION = 1


def embed_source(pdf_bytes: bytes, markdown: str, assets: dict[str, bytes] | None = None,
                 title: str | None = None, creator: str = "PDF Transfer") -> bytes:
    assets = assets or {}
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter(clone_from=reader)

    source = markdown.encode("utf-8")
    manifest = {
        "format": "pdf-transfer",
        "version": FORMAT_VERSION,
        "pages": len(reader.pages),
        "source": SOURCE_NAME,
        "sha256": hashlib.sha256(source).hexdigest(),
        "assets": sorted(assets),
    }
    writer.add_attachment(SOURCE_NAME, source)
    for name, data in sorted(assets.items()):
        writer.add_attachment(ASSET_PREFIX + name, data)
    writer.add_attachment(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False).encode("utf-8"))

    metadata = {"/Creator": creator, "/Producer": f"{creator} (WeasyPrint + pypdf)"}
    if title:
        metadata["/Title"] = title
    writer.add_metadata(metadata)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def extract_source(reader: PdfReader) -> tuple[str, dict[str, bytes]] | None:
    """Devuelve ``(markdown, assets)`` si el PDF contiene una fuente íntegra."""
    try:
        attachments = reader.attachments
    except Exception:
        return None
    if MANIFEST_NAME not in attachments or SOURCE_NAME not in attachments:
        return None
    try:
        manifest = json.loads(attachments[MANIFEST_NAME][0].decode("utf-8"))
        source = attachments[SOURCE_NAME][0]
    except (ValueError, IndexError, UnicodeDecodeError):
        return None

    # Si el PDF se editó después (páginas añadidas/quitadas o fuente alterada),
    # la fuente ya no representa el documento: se ignora y se extrae del diseño.
    if manifest.get("format") != "pdf-transfer":
        return None
    if manifest.get("pages") != len(reader.pages):
        return None
    if hashlib.sha256(source).hexdigest() != manifest.get("sha256"):
        return None

    assets: dict[str, bytes] = {}
    for name in manifest.get("assets", []):
        data = attachments.get(ASSET_PREFIX + name)
        if data:
            assets[name] = data[0]
    return source.decode("utf-8"), assets

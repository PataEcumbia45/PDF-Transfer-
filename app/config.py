"""Configuración de la aplicación, leída de variables de entorno.

Todo lo que cambia entre desarrollo y producción (nombre comercial, límites,
claves de API de clientes de pago) se controla desde aquí sin tocar código.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


@dataclass(frozen=True)
class Plan:
    """Límites de un plan de suscripción."""

    id: str
    name: str
    max_file_mb: int
    max_pages: int
    requests_per_hour: int  # 0 = ilimitado
    batch_size: int
    ai_max_pages: int  # páginas por documento en Modo IA (fórmulas, escaneos, manuscritos)

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "max_file_mb": self.max_file_mb,
            "max_pages": self.max_pages,
            "requests_per_hour": self.requests_per_hour,
            "batch_size": self.batch_size,
            "ai_max_pages": self.ai_max_pages,
        }


PLANS: dict[str, Plan] = {
    "free": Plan("free", "Gratis", max_file_mb=10, max_pages=30, requests_per_hour=30, batch_size=3,
                 ai_max_pages=5),
    "pro": Plan("pro", "Pro", max_file_mb=50, max_pages=500, requests_per_hour=1000, batch_size=25,
                ai_max_pages=200),
    "business": Plan("business", "Empresas", max_file_mb=200, max_pages=3000, requests_per_hour=0,
                     batch_size=100, ai_max_pages=1000),
}


def _parse_api_keys(raw: str) -> dict[str, str]:
    """Convierte ``"clave1:pro,clave2:business"`` en ``{"clave1": "pro", ...}``."""
    keys: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        key, plan = item.rsplit(":", 1)
        if plan in PLANS:
            keys[key.strip()] = plan
    return keys


@dataclass(frozen=True)
class Settings:
    app_name: str = field(default_factory=lambda: os.getenv("APP_NAME", "PDF Transfer"))
    # Correo de contacto para contratar planes de pago (se muestra en "Precios").
    contact_email: str = field(default_factory=lambda: os.getenv("CONTACT_EMAIL", ""))
    # Claves de API de clientes de pago: "clave:plan,clave2:plan".
    api_keys: dict[str, str] = field(default_factory=lambda: _parse_api_keys(os.getenv("API_KEYS", "")))
    # Permitir que el Markdown cargue imágenes remotas (http/https) al generar PDFs.
    # Desactivado por defecto: en un servicio público evita ataques SSRF.
    allow_remote_images: bool = field(default_factory=lambda: _env_bool("ALLOW_REMOTE_IMAGES", False))
    # Desactiva los límites (útil en local o instalaciones privadas).
    disable_limits: bool = field(default_factory=lambda: _env_bool("DISABLE_LIMITS", False))
    # Modo IA (transcripción de fórmulas, escaneos y manuscritos con Claude).
    # Se activa al definir ANTHROPIC_API_KEY.
    ai_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", "claude-opus-5"))
    ai_effort: str = field(default_factory=lambda: os.getenv("AI_EFFORT", "medium"))
    ai_concurrency: int = field(default_factory=lambda: int(os.getenv("AI_CONCURRENCY", "4")))
    # Orígenes permitidos para CORS (API usada desde otros dominios). "*" o lista separada por comas.
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip())
    )


    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_key)


settings = Settings()

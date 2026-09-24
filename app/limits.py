"""Planes, claves de API y límite de peticiones.

Base para cobrar el servicio: cada petición se asocia a un plan (``free`` por
defecto, o el plan de la clave enviada en la cabecera ``X-API-Key``) y se
comprueban sus límites. El contador es en memoria; para varias réplicas en
producción conviene sustituirlo por Redis manteniendo la misma interfaz.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import PLANS, Plan, settings


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float = 3600.0) -> None:
        if limit <= 0:
            return
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                retry = int(window - (now - hits[0])) + 1
                raise HTTPException(
                    status_code=429,
                    detail=f"Has alcanzado el límite de {limit} conversiones por hora de tu plan. "
                    f"Inténtalo de nuevo en {retry // 60 + 1} min o mejora tu plan.",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def _client_id(request: Request) -> str:
    # Detrás de un proxy, uvicorn --proxy-headers ya pone aquí la IP real.
    # No se lee X-Forwarded-For a mano: el cliente podría falsificarla.
    return request.client.host if request.client else "anon"


def resolve_plan(request: Request) -> tuple[Plan, str]:
    """Devuelve el plan de la petición y la clave con la que se contabiliza."""
    api_key = request.headers.get("x-api-key", "").strip()
    if api_key:
        plan_id = settings.api_keys.get(api_key)
        if plan_id is None:
            raise HTTPException(status_code=401, detail="Clave de API no válida.")
        return PLANS[plan_id], f"key:{api_key}"
    return PLANS["free"], f"ip:{_client_id(request)}"


def current_plan(request: Request) -> Plan:
    """Dependencia de FastAPI: resuelve el plan y aplica el límite por hora."""
    plan, key = resolve_plan(request)
    if not settings.disable_limits:
        limiter.check(key, plan.requests_per_hour)
    return plan


def check_size(data: bytes, plan: Plan) -> None:
    if settings.disable_limits:
        return
    if len(data) > plan.max_file_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"El archivo supera el máximo de {plan.max_file_mb} MB del plan {plan.name}.",
        )


def check_pages(pages: int, plan: Plan) -> None:
    if settings.disable_limits:
        return
    if pages > plan.max_pages:
        raise HTTPException(
            status_code=413,
            detail=f"El PDF tiene {pages} páginas y el plan {plan.name} permite hasta {plan.max_pages}.",
        )

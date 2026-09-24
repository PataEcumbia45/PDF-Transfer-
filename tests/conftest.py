import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.limits import limiter
from app.main import app


@pytest.fixture(autouse=True)
def _reset_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), "#3366cc").save(buf, "PNG")
    return buf.getvalue()


SAMPLE_MD = """---
title: Documento de prueba
---

# Documento de prueba

Párrafo con **negrita**, *cursiva*, `código` y un [enlace](https://example.com/ruta).
Acentos y símbolos: áéíóú ñ ¿? ¡! € — “comillas”.

## Lista

- Primero
- Segundo
  - Anidado

1. Uno
2. Dos

| Nombre | Valor |
|--------|-------|
| alfa   | 1     |
| beta   | 2     |

```python
def suma(a, b):
    return a + b
```

> Una cita importante.

![Logo](images/logo.png)

Texto final con nota[^1].

[^1]: La nota al pie.
"""

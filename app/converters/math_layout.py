"""Reconstrucción de fórmulas "en dos pisos" a partir de la geometría del PDF.

En un PDF (Word, LaTeX, LibreOffice…) una fórmula no es texto lineal:

- una **fracción** es un numerador, una raya horizontal dibujada y un
  denominador, uno encima de otro;
- una **raíz** es el símbolo √ seguido de una raya sobre el radicando;
- un **sumatorio, productorio o integral con límites** tiene los límites en
  letra pequeña encima y debajo del símbolo (igual que ``lim`` con su
  ``n → ∞`` debajo).

Este módulo localiza esas estructuras y sustituye sus piezas por una sola
"palabra" con el LaTeX equivalente (``\\frac{…}{…}``, ``\\sqrt{…}``,
``\\sum_{…}^{…}``), que después se integra en la línea como cualquier otra
parte de la fórmula. Se procesan de dentro hacia fuera (las rayas más cortas
primero), así funcionan las estructuras anidadas, como la fórmula general de
segundo grado.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import replace
from typing import Any

from .math_text import is_glue, is_variable, looks_like_math, to_latex

Word = Any  # pdf_to_md.Word (evita la importación circular)

BIG_OPERATORS = {"∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∬": r"\iint", "∮": r"\oint",
                 "⋃": r"\bigcup", "⋂": r"\bigcap", "lim": r"\lim", "máx": r"\max", "mín": r"\min",
                 "max": r"\max", "min": r"\min"}
_SPLIT_CHARS = "√∑∏∫∬∮⋃⋂"


def join_latex(parts: list[str], spaces: list[bool] | None = None) -> str:
    """Une trozos de LaTeX sin pegar un comando a la letra siguiente (``\\pi r``, no ``\\pir``)."""
    out = ""
    for i, part in enumerate(parts):
        if not part:
            continue
        needs_space = bool(out) and re.search(r"\\[A-Za-z]+$", out) and part[:1].isalpha()
        if out and (needs_space or (spaces and spaces[i])):
            out += " "
        out += part
    return out


def main_size(words: list[Word]) -> float:
    """Tamaño de letra predominante (por número de caracteres), ignorando piezas ya en LaTeX."""
    counter: Counter = Counter()
    for w in words:
        if not getattr(w, "latex", False):
            counter[round(w.size * 2) / 2] += max(1, len(w.text))
    if not counter:
        return max((w.size for w in words), default=0.0)
    return counter.most_common(1)[0][0]


def script_kind(w: Word, baseline: float, size: float) -> str:
    """"^" si la palabra es una potencia, "_" si es un subíndice, "" si no."""
    if getattr(w, "latex", False) or not size or w.size > size * 0.95:
        return ""
    if w.bottom < baseline - size * 0.2:
        return "^"
    if w.bottom > baseline + size * 0.12:
        return "_"
    return ""


def baseline_of(words: list[Word]) -> tuple[float, float]:
    size = main_size(words)
    main = [w for w in words if not getattr(w, "latex", False) and w.size >= size * 0.85] or words
    return statistics.median(w.bottom for w in main), size


def band_latex(words: list[Word]) -> str:
    """LaTeX de un grupo de palabras dispuestas en una línea (numerador, radicando…)."""
    words = sorted(words, key=lambda w: w.x0)
    baseline, size = baseline_of(words)
    parts, spaces = [], []
    for i, w in enumerate(words):
        if getattr(w, "latex", False):
            piece = w.text
        else:
            kind = script_kind(w, baseline, size)
            piece = f"{kind}{{{to_latex(w.text)}}}" if kind else to_latex(w.text)
        gap = w.x0 - words[i - 1].x1 if i else 0.0
        parts.append(piece)
        spaces.append(i > 0 and gap > size * 0.15 and not piece.startswith(("^", "_")))
    return join_latex(parts, spaces).strip()


# ------------------------------------------------------------------ geometría


def _cx(w: Word) -> float:
    return (w.x0 + w.x1) / 2


def _cy(w: Word) -> float:
    return (w.top + w.bottom) / 2


def rules_of(page, exclude: list[tuple]) -> list[tuple[float, float, float]]:
    """Rayas horizontales finas de la página: ``(x0, x1, y)``, de la más corta a la más larga."""
    found: list[tuple[float, float, float]] = []
    for obj in list(page.lines or []) + list(page.rects or []):
        height = abs(obj["bottom"] - obj["top"])
        width = obj["x1"] - obj["x0"]
        if height > 2.0 or width < 3.0 or width > page.width * 0.9:
            continue
        y = (obj["top"] + obj["bottom"]) / 2
        if any(b[0] - 1 <= obj["x0"] and obj["x1"] <= b[2] + 1 and b[1] - 1 <= y <= b[3] + 1 for b in exclude):
            continue  # bordes de tabla
        if not any(abs(y - r[2]) < 0.6 and abs(obj["x0"] - r[0]) < 0.6 and abs(obj["x1"] - r[1]) < 0.6
                   for r in found):
            found.append((float(obj["x0"]), float(obj["x1"]), float(y)))
    return sorted(found, key=lambda r: r[1] - r[0])


# Piezas con las que se "construyen" paréntesis, corchetes y llaves grandes.
DELIMITER_PIECES = {
    "⎛": "(", "⎜": "(", "⎝": "(", "⎞": ")", "⎟": ")", "⎠": ")",
    "⎡": "[", "⎢": "[", "⎣": "[", "⎤": "]", "⎥": "]", "⎦": "]",
    "⎧": "{", "⎨": "{", "⎩": "{", "⎪": "{", "⎫": "}", "⎬": "}", "⎭": "}",
    "⎸": "|", "⎹": "|", "│": "|",
}


def merge_delimiter_pieces(words: list[Word]) -> list[Word]:
    """Une las piezas apiladas de un delimitador grande en una sola "palabra" ("⎧⎨⎩" → "{")."""
    pieces = [w for w in words if len(w.text) == 1 and w.text in DELIMITER_PIECES]
    if not pieces:
        return words
    used: set[int] = set()
    merged: list[Word] = []
    for w in sorted(pieces, key=lambda w: w.top):
        if id(w) in used:
            continue
        kind = DELIMITER_PIECES[w.text]
        column = [w]
        for other in sorted(pieces, key=lambda o: o.top):
            last = column[-1]
            if (id(other) in used or other is w or DELIMITER_PIECES[other.text] != kind
                    or other.top <= last.top or abs(other.x0 - last.x0) > 2
                    or other.top - last.bottom > last.size * 0.4):
                continue
            column.append(other)
        used.update(id(p) for p in column)
        merged.append(replace(w, text=kind, top=min(p.top for p in column),
                              bottom=max(p.bottom for p in column), math=True))
    return [w for w in words if id(w) not in used] + merged


def split_symbols(words: list[Word]) -> list[Word]:
    """Separa √, ∑, ∫… del texto pegado a ellos ("√b" → "√" + "b")."""
    out: list[Word] = []
    for w in words:
        text = w.text
        if len(text) < 2 or not any(c in _SPLIT_CHARS for c in text):
            out.append(w)
            continue
        char_w = (w.x1 - w.x0) / len(text)
        start = 0
        for i, ch in enumerate(text):
            if ch in _SPLIT_CHARS:
                if i > start:
                    out.append(replace(w, text=text[start:i], x0=w.x0 + start * char_w, x1=w.x0 + i * char_w))
                out.append(replace(w, text=ch, x0=w.x0 + i * char_w, x1=w.x0 + (i + 1) * char_w, math=True))
                start = i + 1
        if start < len(text):
            out.append(replace(w, text=text[start:], x0=w.x0 + start * char_w, x1=w.x1))
    return out


def _synthetic(template: Word, text: str, group: list[Word], size: float) -> Word:
    return replace(
        template, text=text, x0=min(w.x0 for w in group), x1=max(w.x1 for w in group),
        top=min(w.top for w in group), bottom=max(w.bottom for w in group),
        size=size, math=True, latex=True, bold=False, italic=False, mono=False,
    )


# ------------------------------------------------------------------ estructuras


def _radical(words: list[Word], rule: tuple[float, float, float]) -> tuple[Word, list[Word]] | None:
    x0, x1, y = rule
    for sign in words:
        if sign.text != "√":
            continue
        tol = max(2.5, sign.size * 0.3)
        if not (abs(rule[0] - sign.x1) <= tol and sign.top - tol <= y <= sign.top + (sign.bottom - sign.top) * 0.5):
            continue
        inside = [w for w in words if w is not sign and x0 - 1 <= _cx(w) <= x1 + 1
                  and y < _cy(w) <= sign.bottom + sign.size * 0.3]
        if not inside:
            continue
        # Índice de la raíz (∛ escrito como número pequeño arriba a la izquierda).
        index = [w for w in words if w is not sign and w not in inside and w.size < sign.size * 0.8
                 and sign.x0 - sign.size * 0.6 <= w.x1 <= sign.x0 + (sign.x1 - sign.x0) * 0.7
                 and w.bottom <= sign.top + (sign.bottom - sign.top) * 0.6 and w.bottom >= sign.top - sign.size]
        body = band_latex(inside)
        latex = rf"\sqrt[{band_latex(index)}]{{{body}}}" if index else rf"\sqrt{{{body}}}"
        group = [sign, *inside, *index]
        return _synthetic(sign, latex, group, main_size(inside) or sign.size), group
    return None


def _near_rule(w: Word, rule: tuple[float, float, float]) -> bool:
    return rule[0] - 1.5 <= _cx(w) <= rule[1] + 1.5 and abs(_cy(w) - rule[2]) < w.size * 1.2


def _fraction(words: list[Word], rule: tuple[float, float, float],
              others: list[tuple[float, float, float]]) -> tuple[Word, list[Word]] | None:
    x0, x1, y = rule
    in_span = [w for w in words if x0 - 1.5 <= _cx(w) <= x1 + 1.5]
    above_seed = [w for w in in_span if _cy(w) < y and y - w.bottom < 0.9 * w.size]
    below_seed = [w for w in in_span if _cy(w) > y and w.top - y < 0.9 * w.size]
    if not above_seed or not below_seed:
        return None
    num_top = min(w.top for w in above_seed)
    den_bottom = max(w.bottom for w in below_seed)
    num = [w for w in in_span if num_top - 0.5 <= _cy(w) < y]
    den = [w for w in in_span if y < _cy(w) <= den_bottom + 0.5]
    size = statistics.median(w.size for w in num + den)
    # Las piezas deben caber bajo/sobre la raya (una fracción está centrada en su raya).
    if any(w.x0 < x0 - size * 0.5 or w.x1 > x1 + size * 0.5 for w in num + den):
        return None
    # Si a los lados hay texto alineado con el "numerador" o el "denominador", la raya es un
    # subrayado entre dos líneas de texto, no una fracción. No cuenta el texto de otra
    # fracción vecina ("3/4 + 1/2") ni el de la línea principal (queda a media altura).
    group_ids = {id(w) for w in num + den}
    num_cy = statistics.median(_cy(w) for w in num)
    den_cy = statistics.median(_cy(w) for w in den)
    for w in words:
        if id(w) in group_ids or x0 - size * 2 > w.x1 or w.x0 > x1 + size * 2:
            continue
        if getattr(w, "latex", False) or any(_near_rule(w, r) for r in others if r != rule):
            continue
        if abs(_cy(w) - num_cy) < size * 0.35 or abs(_cy(w) - den_cy) < size * 0.35:
            return None
    latex = rf"\frac{{{band_latex(num)}}}{{{band_latex(den)}}}"
    return _synthetic(num[0], latex, num + den, size), num + den


def _big_operator(words: list[Word]) -> tuple[Word, list[Word]] | None:
    for op in words:
        cmd = BIG_OPERATORS.get(op.text)
        if cmd is None or getattr(op, "latex", False):
            continue
        h = op.bottom - op.top
        reach = max(op.size * 0.6, 4.0)
        near = [w for w in words if w is not op and not getattr(w, "latex", False) and w.size < op.size * 0.9
                and op.x0 - op.size * 0.8 <= _cx(w) <= op.x1 + op.size * 0.8]
        below = [w for w in near if w.top >= op.bottom - h * 0.35 and w.top - op.bottom < reach]
        above = [w for w in near if w.bottom <= op.top + h * 0.35 and op.top - w.bottom < reach]
        if not below and not above:
            continue
        latex = cmd
        if below:
            latex += f"_{{{band_latex(below)}}}"
        if above:
            latex += f"^{{{band_latex(above)}}}"
        # El tamaño de la palabra nueva es el del texto que la rodea, no el del símbolo grande.
        neighbours = [w for w in words if w is not op and w not in below and w not in above
                      and abs(_cy(w) - _cy(op)) < h and w.size >= op.size * 0.5]
        size = main_size(neighbours) if neighbours else op.size
        group = [op, *below, *above]
        return _synthetic(op, latex, group, size), group
    return None


OPENERS = {"(": ")", "[": "]", "|": "|", "‖": "‖", "{": "}"}
MATRIX_ENV = {"(": "pmatrix", "[": "bmatrix", "|": "vmatrix", "‖": "Vmatrix", "{": "Bmatrix"}


def _rows(words: list[Word]) -> list[list[Word]]:
    """Agrupa palabras en filas por su posición vertical."""
    rows: list[list[Word]] = []
    for w in sorted(words, key=_cy):
        if rows and abs(_cy(w) - statistics.median(_cy(x) for x in rows[-1])) < w.size * 0.45:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w.x0) for r in rows]


def _mathy(row: list[Word]) -> bool:
    """¿La fila parece una expresión matemática y no una línea de texto?"""
    hits = sum(1 for w in row if getattr(w, "latex", False) or w.math or is_glue(w.text)
               or is_variable(w.text) or looks_like_math(w.text) or (len(w.text) == 1 and w.text.isalpha()))
    return hits >= max(1, 0.6 * len(row))


def _cells(row: list[Word], min_gap: float) -> list[list[Word]]:
    cells: list[list[Word]] = [[row[0]]]
    for prev, w in zip(row, row[1:]):
        if w.x0 - prev.x1 > min_gap:
            cells.append([w])
        else:
            cells[-1].append(w)
    return cells


def _stacked(words: list[Word]) -> tuple[Word, list[Word]] | None:
    """Sistemas de ecuaciones (llave con filas) y matrices/determinantes (filas entre delimitadores)."""
    for d in sorted(words, key=lambda w: w.x0):
        if d.text not in OPENERS or getattr(d, "latex", False):
            continue
        # El delimitador puede ser grande (LaTeX) o del tamaño normal (Word): la referencia es el texto.
        nearby = [w for w in words if w is not d and 0 <= _cx(w) - d.x1 < d.size * 3
                  and abs(_cy(w) - _cy(d)) < d.size * 2.5]
        if not nearby:
            continue
        size = main_size(nearby)
        half = max((d.bottom - d.top) * 0.6, size * 2.3)
        window = [w for w in words if w is not d and _cx(w) > d.x1 - 1 and abs(_cy(w) - _cy(d)) <= half]
        closer = None
        if d.text != "{" or any(w.text == "}" for w in window):
            closers = [w for w in window if w.text == OPENERS[d.text] and abs(_cy(w) - _cy(d)) < size * 0.5
                       and w.x0 > d.x1 + size * 0.3]
            if not closers:
                if d.text != "{":
                    continue
            else:
                closer = min(closers, key=lambda w: w.x0)
        inside = [w for w in window if w is not closer and (closer is None or _cx(w) < closer.x0 + 1)]
        rows = [r for r in _rows(inside) if r[0].x0 <= d.x1 + size * 1.5]
        if closer is None:  # llave de un sistema: cada fila llega hasta un hueco grande
            rows = [_cells(r, size * 3)[0] for r in rows]
        if len(rows) < 2:
            continue
        centers = [statistics.median(_cy(w) for w in r) for r in rows]
        if not (min(centers) < _cy(d) - size * 0.3 and max(centers) > _cy(d) + size * 0.3):
            continue
        # Filas consecutivas y con aspecto de fórmula.
        if any(b - a > size * 2.2 for a, b in zip(centers, centers[1:])) or not all(_mathy(r) for r in rows):
            continue
        if closer is None:
            body = r" \\ ".join(" & ".join(band_latex(c) for c in _cells(r, size * 2)) for r in rows)
            latex = rf"\begin{{cases}} {body} \end{{cases}}"
        else:
            body = r" \\ ".join(" & ".join(band_latex(c) for c in _cells(r, size * 0.9)) for r in rows)
            env = MATRIX_ENV[d.text]
            latex = rf"\begin{{{env}}} {body} \end{{{env}}}"
        group = [d, *(w for r in rows for w in r), *([closer] if closer else [])]
        return _synthetic(d, latex, group, main_size([w for r in rows for w in r]) or size), group
    return None


def build_structures(page, words: list[Word], exclude: list[tuple]) -> list[Word]:
    """Sustituye fracciones, raíces y operadores con límites por palabras con su LaTeX."""
    rules = rules_of(page, exclude)
    words = merge_delimiter_pieces(split_symbols(words))
    if not rules and not any(w.text in BIG_OPERATORS or w.text in OPENERS for w in words):
        return words

    def apply(found: tuple[Word, list[Word]] | None) -> bool:
        nonlocal words
        if not found:
            return False
        new, used = found
        used_ids = {id(w) for w in used}
        words = [w for w in words if id(w) not in used_ids] + [new]
        return True

    unused = []
    for rule in rules:  # de la raya más corta (más interna) a la más larga
        if not (apply(_radical(words, rule)) or apply(_fraction(words, rule, rules))):
            unused.append(rule)
    while apply(_big_operator(words)):
        pass
    while apply(_stacked(words)):
        pass
    return words + _blanks(words, unused, float(page.width))


def _blanks(words: list[Word], rules: list[tuple[float, float, float]], page_width: float) -> list[Word]:
    """Rayas para escribir la respuesta ("Nombre: ______") como guiones bajos.

    No lo son los subrayados (hay texto justo encima de la raya) ni las líneas
    separadoras (largas y sin texto en su renglón).
    """
    blanks = []
    for x0, x1, y in rules:
        underlined = any(min(w.x1, x1) - max(w.x0, x0) > (w.x1 - w.x0) * 0.5
                         and _cy(w) < y and y - w.top < w.size * 1.6 for w in words)
        if underlined:
            continue
        same_line = [w for w in words if abs(w.bottom - y) < w.size * 0.6
                     and min(abs(w.x1 - x0), abs(w.x0 - x1)) < w.size * 3]
        if not same_line and x1 - x0 > page_width * 0.5:
            continue  # línea separadora
        template = min(same_line, key=lambda w: min(abs(w.x1 - x0), abs(w.x0 - x1)), default=None)
        if template is None:
            template = words[0] if words else None
            if template is None:
                continue
            size = main_size(words)
        else:
            size = template.size
        count = max(3, min(30, round((x1 - x0) / (size * 0.5))))
        blanks.append(replace(template, text="_" * count, x0=x0, x1=x1, top=y - size * 0.8, bottom=y + size * 0.2,
                              size=size, math=False, latex=False, bold=False, italic=False, mono=False, link=None))
    return blanks

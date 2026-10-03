"""Reconocimiento de fórmulas sencillas en el texto de un PDF.

Convierte lo que el PDF guarda como texto (letras griegas, operadores,
caracteres matemáticos Unicode de Word/Cambria Math, fuentes de LaTeX como
CMMI/CMSY) a LaTeX. Las potencias y subíndices se detectan por posición y
tamaño de letra en ``pdf_to_md._inline``.
"""

from __future__ import annotations

import re
import unicodedata

MATH_FONT_RE = re.compile(
    r"cmmi|cmsy|cmex|cmbsy|cmmib|msam|msbm|eufm|rsfs|math|stix|xits|asana|symbol|mt ?extra|"
    r"euclid|mtsy|mtmi|rtxmi|txsy|txmi|pxsy|pxmi|esint|wasy|mathjax|katex",
    re.I,
)

SYMBOLS: dict[str, str] = {
    # griego
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon", "ϵ": r"\epsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ϖ": r"\varpi", "ρ": r"\rho",
    "ϱ": r"\varrho", "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi",
    "ϕ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi",
    "Σ": r"\Sigma", "Υ": r"\Upsilon", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
    # operadores y relaciones
    "±": r"\pm", "∓": r"\mp", "×": r"\times", "÷": r"\div", "⋅": r"\cdot", "∙": r"\cdot", "∗": "*",
    "−": "-", "≤": r"\leq", "⩽": r"\leq", "≥": r"\geq", "⩾": r"\geq", "≠": r"\neq", "≈": r"\approx",
    "≡": r"\equiv", "∼": r"\sim", "≃": r"\simeq", "≅": r"\cong", "∝": r"\propto", "≪": r"\ll", "≫": r"\gg",
    "∞": r"\infty", "∂": r"\partial", "∇": r"\nabla", "∫": r"\int", "∬": r"\iint", "∭": r"\iiint",
    "∮": r"\oint", "∑": r"\sum", "∏": r"\prod", "√": r"\sqrt", "∛": r"\sqrt[3]",
    "∈": r"\in", "∉": r"\notin", "∋": r"\ni", "⊂": r"\subset", "⊆": r"\subseteq", "⊃": r"\supset",
    "⊇": r"\supseteq", "∪": r"\cup", "∩": r"\cap", "∅": r"\emptyset", "∀": r"\forall", "∃": r"\exists",
    "∄": r"\nexists", "¬": r"\neg", "∧": r"\wedge", "∨": r"\vee", "⊕": r"\oplus", "⊗": r"\otimes",
    "∘": r"\circ", "→": r"\to", "←": r"\leftarrow", "↔": r"\leftrightarrow", "⇒": r"\Rightarrow",
    "⇐": r"\Leftarrow", "⇔": r"\Leftrightarrow", "↦": r"\mapsto", "⟶": r"\longrightarrow",
    "∠": r"\angle", "⊥": r"\perp", "∥": r"\parallel", "△": r"\triangle", "⟨": r"\langle", "⟩": r"\rangle",
    "‖": r"\|", "ℓ": r"\ell", "ℏ": r"\hbar", "ℵ": r"\aleph", "ℜ": r"\Re", "ℑ": r"\Im",
    "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}", "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}",
    "⌊": r"\lfloor", "⌋": r"\rfloor", "⌈": r"\lceil", "⌉": r"\rceil",
    # solo se traducen dentro de una fórmula (no la delatan por sí solos)
    "°": r"^{\circ}", "·": r"\cdot", "…": r"\ldots", "⋯": r"\cdots", "′": "'", "″": "''",
}
# Caracteres que por sí solos no indican que haya una fórmula (aparecen en texto normal).
_WEAK = set("°·…′″∗")
_STRONG = {c for c in SYMBOLS if c not in _WEAK}

SUPERSCRIPTS = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ", "0123456789+-=()ni"))
SUBSCRIPTS = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓᵢⱼₖₙ", "0123456789+-=()aeoxijkn"))

# Texto que puede formar parte de una fórmula junto a símbolos: números, operadores, paréntesis.
_GLUE_RE = re.compile(r"^[\d\s=+\-−*/().,;:\[\]{}<>|!'^_°·′″]+$")
# Variables y funciones cortas: "x", "A", "mc", "f(x)", "sen(x)", "dx". Se excluyen las
# palabras cortas habituales del español y el inglés para no meterlas en una fórmula.
_VARIABLE_RE = re.compile(r"^\d*[A-Za-z]{1,2}('|′)*(\([^\s()]*\))?[.,;:]?$|^[a-z]{2,4}\([^\s()]+\)[.,;:]?$")
_SHORT_WORDS = {
    "a", "e", "o", "u", "y", "de", "el", "la", "lo", "en", "es", "un", "se", "si", "no", "al", "le",
    "su", "tu", "mi", "ya", "va", "ha", "he", "me", "te", "ni", "da", "di", "ve", "vi", "yo", "os",
    "of", "to", "in", "is", "it", "an", "as", "at", "be", "by", "do", "if", "on", "or", "so", "up", "we",
    "i", "am", "my", "us", "ok",
}
_LATEX_SPECIAL = {"\\": r"\backslash ", "{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#", "&": r"\&",
                  "$": r"\$", "~": r"\sim "}


def is_math_font(fontname: str) -> bool:
    return bool(MATH_FONT_RE.search((fontname or "").split("+", 1)[-1]))


def _is_math_alnum(ch: str) -> bool:
    # Bloque "Mathematical Alphanumeric Symbols" (𝑥, 𝐀, 𝔽...), usado por Word y Cambria Math.
    return 0x1D400 <= ord(ch) <= 0x1D7FF


def looks_like_math(text: str) -> bool:
    """¿El texto contiene símbolos que delatan una fórmula?"""
    return any(c in _STRONG or c in SUPERSCRIPTS or c in SUBSCRIPTS or _is_math_alnum(c) for c in text)


def is_glue(text: str) -> bool:
    return bool(_GLUE_RE.match(text))


def is_variable(text: str) -> bool:
    # Una letra mayúscula suele ser una variable ("A"), pero "a" es una preposición.
    # Con dos letras, "Si" o "En" (inicio de frase) siguen siendo palabras.
    word = text.rstrip(".,;:")
    if word in _SHORT_WORDS or (len(word) > 1 and word.lower() in _SHORT_WORDS):
        return False
    return bool(_VARIABLE_RE.match(text))


# Funciones matemáticas escritas con letras: "sen(x)" → "\sen(x)", "log x" → "\log x".
FUNCTIONS = {name: "\\" + name for name in ("sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp",
                                              "sinh", "cosh", "tanh", "arcsin", "arccos", "arctan")}
FUNCTIONS.update({name: "\\operatorname{" + name + "}" for name in ("sen", "tg", "cotg", "cosec", "arcsen",
                                                                    "arctg", "senh")})
_FUNCTION_RE = re.compile(r"^(" + "|".join(sorted(FUNCTIONS, key=len, reverse=True)) + r")(?=[(\s]|$)")


# Palabras que aparecen dentro de las fórmulas ("0, otro caso", "para x ≥ 0"). En LaTeX
# se escriben con \text{} para que conserven sus espacios y no se lean como variables.
TEXT_WORDS = {
    "para", "si", "otro", "otra", "caso", "casos", "resto", "donde", "cuando", "con", "en", "de", "lo",
    "contrario", "parte", "demás", "todo", "toda", "forma", "sino", "entonces", "tal", "que", "and",
    "otherwise", "for", "if", "elsewhere", "where",
}
_TEXT_RE = re.compile(r"\\text\{([^{}]*)\}(\s*)\\text\{([^{}]*)\}")
# Conjunciones que unen dos expresiones ("E(X) y V(X)"); no son la variable y.
CONJUNCTIONS = {"y", "o", "e", "u", "ó"}


def text_word(word: str) -> str:
    """Palabra dentro de una fórmula, con espacios para que no se pegue a lo de alrededor."""
    return "\\text{ " + word + " }"


def merge_text(latex: str) -> str:
    """Une ``\text{otro} \text{caso}`` en ``\text{otro caso}``."""
    while True:
        merged = _TEXT_RE.sub(lambda m: text_word(m.group(1).strip() + " " + m.group(3).strip()), latex)
        if merged == latex:
            return latex
        latex = merged


def to_latex(text: str) -> str:
    """Traduce el texto de una palabra de una fórmula a LaTeX."""
    plain = unicodedata.normalize("NFKC", text)
    word, punct = re.match(r"^(.*?)([.,;:]*)$", plain).groups()
    if word.lower() in TEXT_WORDS:
        return text_word(word) + punct
    out: list[str] = []
    i = 0
    function = _FUNCTION_RE.match(text)
    if function:
        out.append(FUNCTIONS[function.group(1)] + ("" if text[function.end():].startswith("(") else " "))
        i = function.end()
    while i < len(text):
        ch = text[i]
        if ch in SUPERSCRIPTS or ch in SUBSCRIPTS:
            table, mark = (SUPERSCRIPTS, "^") if ch in SUPERSCRIPTS else (SUBSCRIPTS, "_")
            j = i
            while j < len(text) and text[j] in table:
                j += 1
            out.append(f"{mark}{{{''.join(table[c] for c in text[i:j])}}}")
            i = j
            continue
        if ch in SYMBOLS:
            cmd = SYMBOLS[ch]
            nxt = text[i + 1] if i + 1 < len(text) else ""
            out.append(cmd + (" " if cmd[-1].isalpha() and cmd.startswith("\\") and nxt.isalpha() else ""))
        elif _is_math_alnum(ch):
            plain = unicodedata.normalize("NFKC", ch)  # 𝑥 → x, 𝜆 → λ
            out.append(SYMBOLS.get(plain, plain) + (" " if plain in SYMBOLS and text[i + 1:i + 2].isalpha() else ""))
        elif ch in _LATEX_SPECIAL:
            out.append(_LATEX_SPECIAL[ch])
        else:
            out.append(ch)
        i += 1
    return "".join(out)

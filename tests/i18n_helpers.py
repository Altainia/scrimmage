"""Helpers shared by the i18n gate tests: the token finder of test_i18n_typed.py (what a student types, kept out of
translatable text), which test_en_gb_catalog.py also uses to check a translation keeps those tokens."""
import re

FIELD = re.compile(r"\{[A-Za-z_]\w*\}")
SETTING = re.compile(r"[A-Za-z_][\w.]* ?= ?(?!=)[^\s,;)]+")
TABLE = re.compile(r"\[\[?([A-Za-z_<{][\w.<>{}\-]*)\]\]?")

# Notation, not a setting: a time or a position written as math beside a placeholder ("at t={t}", "x={x}",
# "t={start}–{end} s", "heading={heading}°"). Nobody types it into a file; a translation keeps the letters.
NOTATION = re.compile(r"(?:t|x|y|z|heading)=\{[A-Za-z_]\w*\}(?:–\{[A-Za-z_]\w*\})?[°:]?$")


def tokens(text: str) -> list[str]:
    """The quoted, backticked and bracketed tokens of `text` that aren't one {placeholder}, quotes paired in order."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if text.startswith("{{", i):            # a literal brace: a TOML inline table or a {cad}-style folder token
            j = text.find("}}", i + 2)
            j = len(text) if j < 0 else j + 2
            out.append(text[i:j])
            i = j
            continue
        m = SETTING.match(text, i)
        if m and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_.}")):
            if not NOTATION.match(m.group(0)):
                out.append(m.group(0))         # key = value or key=value, the key written out
            i = m.end()
            continue
        if c in '"`':
            j = text.find(c, i + 1)
            if j < 0:
                break
            if not FIELD.fullmatch(text[i + 1:j]):
                out.append(text[i:j + 1])
            i = j + 1
            continue
        if c == "[":
            m = TABLE.match(text, i)
            if m and not FIELD.fullmatch(m.group(1)):
                out.append(m.group(0))
                i = m.end()
                continue
        i += 1
    return out

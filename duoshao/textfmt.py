"""Turns a plain-text product description into readable HTML (paragraphs, bullets, bold labels)."""
import re
from markupsafe import Markup, escape

_BULLET = re.compile(r"^\s*(?:[-•*·–]|\d+[.)])\s+(.*)$")
_LABEL = re.compile(r"^([A-Za-z][A-Za-z0-9 /&()+.-]{1,28}):\s+(\S.*)$")


def _inline(text):
    """Escape, and bold a short 'Label:' at the start of a line (e.g. 'Storage: 64GB')."""
    m = _LABEL.match(text)
    if m:
        return Markup("<strong>{}:</strong> {}").format(m.group(1), m.group(2))
    return escape(text)


def format_description(text):
    if not text or not text.strip():
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    # A single unbroken blob: split at sentence ends so it isn't a wall of text.
    if "\n" not in text and len(text) > 140:
        text = re.sub(r"(?<=[.!?])\s+(?=[A-Z0-9])", "\n\n", text)

    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        i = 0
        while i < len(lines):
            if _BULLET.match(lines[i]):
                items = []
                while i < len(lines) and _BULLET.match(lines[i]):
                    items.append(_BULLET.match(lines[i]).group(1))
                    i += 1
                out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            else:
                para = []
                while i < len(lines) and not _BULLET.match(lines[i]):
                    para.append(lines[i])
                    i += 1
                out.append("<p>" + "<br>".join(str(_inline(x)) for x in para) + "</p>")
    return Markup("".join(out))

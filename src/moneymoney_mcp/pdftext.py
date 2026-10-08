"""Minimal stdlib PDF text extractor for machine-generated statements.

Handles the common bank-statement case: FlateDecode streams, literal and hex
strings, ``Tj``/``TJ`` operators, per-font ToUnicode CMaps (bfchar/bfrange).
Anything else degrades honestly: unmapped glyphs are skipped, fonts without
a CMap fall back to cp1252, and the result reports whether extraction was
``full``, ``partial`` or ``unavailable``.

Only streams containing a ``BT`` (begin-text) operator are treated as
content, so images and other binary streams are never misread as text.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from dataclasses import dataclass, field


@dataclass
class Extraction:
    text: str
    status: str  # "full" | "partial" | "unavailable"
    unmapped_codes: int = 0
    fallback_fonts: list[str] = field(default_factory=list)


@dataclass
class _CMap:
    mapping: dict[int, str]
    code_len: int  # longest glyph code in bytes (1 or 2)


_STANDARD_FONTS = frozenset(
    [
        "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
        "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
        "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
    ]
)


def _objects(raw: bytes):
    """Yield (object number, dict bytes, stream bytes or None).

    Expands compressed object streams (/ObjStm), where modern producers
    hide fonts, resources and CMaps; embedded objects are yielded with
    their sliced body as "dict bytes" and no stream.
    """
    top: list[tuple[int, bytes, bytes | None]] = []
    for match in re.finditer(rb"(\d+)\s+\d+\s+obj\b", raw):
        num = int(match.group(1))
        start = match.end()
        end = raw.find(b"endobj", start)
        if end == -1:
            continue
        stream_at = raw.find(b"stream", start, end)
        if stream_at == -1:
            top.append((num, raw[start:end], None))
            continue
        eol = raw.find(b"\n", stream_at, stream_at + 10)
        content_start = eol + 1 if eol != -1 else stream_at + 6
        dict_part = raw[start:stream_at]
        # Trust /Length when it lands exactly on the endstream marker
        # (binary streams may contain "endstream" themselves); otherwise
        # fall back to marker search.
        end_at = raw.find(b"endstream", content_start, end + 9)
        length = re.search(rb"/Length\s+(\d+)", dict_part)
        if length:
            want = content_start + int(length.group(1))
            marker = raw[want : want + 11]
            if marker.startswith((b"\nendstream", b"\r\nendstream", b"\rendstream")) or end_at == -1:
                stream = raw[content_start:want]
            else:
                stream = raw[content_start:end_at]
        else:
            if end_at == -1:
                continue
            stream = raw[content_start:end_at]
        if stream.endswith(b"\r\n"):
            stream = stream[:-2]
        elif stream.endswith((b"\n", b"\r")):
            stream = stream[:-1]
        top.append((num, dict_part, stream))
    yield from top
    for _num, dict_part, stream in top:
        if stream is None or b"/ObjStm" not in dict_part:
            continue
        try:
            inflated = zlib.decompress(stream)
        except zlib.error:
            continue
        head = re.match(rb"\s*((?:\d+\s+\d+\s*)+)", inflated)
        if not head:
            continue
        nums = [int(x) for x in head.group(1).split()]
        first_match = re.search(rb"/First\s+(\d+)", dict_part)
        first = int(first_match.group(1)) if first_match else head.end()
        pairs = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        for i, (obj_num, offset) in enumerate(pairs):
            span_end = first + pairs[i + 1][1] if i + 1 < len(pairs) else len(inflated)
            yield obj_num, inflated[first + offset : span_end], None


def _inflate(dict_part: bytes, stream: bytes) -> bytes:
    if re.search(rb"/Fl(ateDecode)?\b", dict_part):
        try:
            return zlib.decompress(stream)
        except zlib.error:
            return b""
    return stream


def _decode_dst(hexstr: str) -> str:
    try:
        return bytes.fromhex(hexstr).decode("utf-16-be")
    except (ValueError, UnicodeDecodeError):
        return ""


def _parse_cmap(text: str) -> _CMap:
    mapping: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            mapping[int(src, 16)] = _decode_dst(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, arr in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<[0-9A-Fa-f]+>\s*\[(.*?)\]", block, re.S
        ):
            for i, dst in enumerate(re.findall(r"<([0-9A-Fa-f]+)>", arr)):
                mapping[int(lo, 16) + i] = _decode_dst(dst)
        plain = re.sub(r"<[0-9A-Fa-f]+>\s*<[0-9A-Fa-f]+>\s*\[.*?\]", "", block, flags=re.S)
        for lo, hi, dst in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", plain
        ):
            try:
                raw_dst = bytes.fromhex(dst)
            except ValueError:
                continue
            base = int.from_bytes(raw_dst, "big")
            start, stop = int(lo, 16), int(hi, 16)
            for code in range(start, min(stop, start + 10000) + 1):
                try:
                    mapping[code] = (base + code - start).to_bytes(
                        len(raw_dst), "big"
                    ).decode("utf-16-be")
                except (OverflowError, UnicodeDecodeError):
                    continue
    code_len = 1
    for block in re.findall(r"begincodespacerange(.*?)endcodespacerange", text, re.S):
        for bound in re.findall(r"<([0-9A-Fa-f]+)>", block):
            code_len = max(code_len, min(len(bound) // 2, 2))
    return _CMap(mapping=mapping, code_len=code_len)


_SIMPLE_ESCAPES = {
    ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b",
    ord("f"): b"\f", ord("("): b"(", ord(")"): b")", ord("\\"): b"\\",
}


def _unescape(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        byte = data[i]
        if byte != 0x5C or i + 1 >= len(data):  # backslash
            out.append(byte)
            i += 1
            continue
        nxt = data[i + 1]
        if nxt in _SIMPLE_ESCAPES:
            out += _SIMPLE_ESCAPES[nxt]
            i += 2
        elif nxt == 0x0D:  # \r (+ optional \n): line continuation
            i += 3 if data[i + 2 : i + 3] == b"\n" else 2
        elif nxt == 0x0A:
            i += 2
        elif 0x30 <= nxt <= 0x37:  # octal, up to 3 digits
            digits = data[i + 1 : i + 4]
            used = 0
            for digit in digits:
                if 0x30 <= digit <= 0x37:
                    used += 1
                else:
                    break
            out.append(int(digits[:used], 8))
            i += 1 + used
        else:
            out.append(nxt)
            i += 2
    return bytes(out)


def _decode_codes(data: bytes, cmap: _CMap | None) -> tuple[str, int]:
    """Decode glyph codes via CMap; returns (text, unmapped byte count)."""
    if cmap is None:
        return data.decode("cp1252", errors="replace"), 0
    out: list[str] = []
    unmapped = 0
    i = 0
    while i < len(data):
        hit = None
        for size in (cmap.code_len, 1) if cmap.code_len > 1 else (1,):
            code = int.from_bytes(data[i : i + size], "big")
            if code in cmap.mapping:
                hit = cmap.mapping[code]
                i += size
                break
        if hit is None:
            unmapped += 1
            i += 1
        else:
            out.append(hit)
    return "".join(out), unmapped


_TOKEN = re.compile(
    rb"/[\w\d]+\s+[\d.]+\s+Tf"  # font selection
    rb"|BT|ET|T\*"
    rb"|[-\d.]+\s+[-\d.]+\s+T[dD]"  # line / same-line move
    rb"|[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+Tm"  # absolute move
    rb"|\((?:\\.|[^()\\])*\)\s*Tj"  # literal string show
    rb"|\((?:\\.|[^()\\])*\)\s*'"  # literal string show + newline
    rb"|<[0-9A-Fa-f\s]+>\s*Tj"  # hex string show
    rb"|\[(.*?)\]\s*TJ",  # array show
    re.S,
)
_FONT_OP = re.compile(rb"/([\w\d]+)\s+[\d.]+\s+Tf$")
_TD_OP = re.compile(rb"([-\d.]+)\s+([-\d.]+)\s+T[dD]$")
_TM_OP = re.compile(
    rb"[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+([-\d.]+)\s+([-\d.]+)\s+Tm$"
)
_TJ_PART = re.compile(rb"\((?:\\.|[^()\\])*\)|<[0-9A-Fa-f\s]+>")


def _looks_like_text(text: str) -> bool:
    """Quality gate: reject mojibake floods from unmapped custom encodings.

    Real statements are overwhelmingly letters, numbers and whitespace;
    misdecoded bytes show up as control, unassigned, private-use or symbol
    characters instead.
    """
    sample = text[:2000]
    if not sample:
        return False
    bad = sum(
        1
        for ch in sample
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "So")
        and ch not in "\n\t\r"
    )
    good = sum(1 for ch in sample if unicodedata.category(ch)[0] in "LNZ")
    return bad / len(sample) <= 0.05 and good / len(sample) >= 0.5


def _num(raw: bytes) -> float:
    """Parse a PDF number defensively; NaN keeps separators as newlines."""
    try:
        value = float(raw)
    except ValueError:
        return float("nan")
    return value


def _string_bytes(token: bytes) -> bytes:
    token = token.strip()
    if token.startswith(b"("):
        return _unescape(token[1:-1])
    hexstr = token[1:-1].decode("ascii", errors="ignore")
    if len(re.sub(r"\s", "", hexstr)) % 2:
        hexstr += "0"
    try:
        return bytes.fromhex(hexstr)
    except ValueError:
        return b""


def extract(raw: bytes) -> Extraction:
    """Extract text from one PDF file's bytes."""
    fonts: dict[int, int | None] = {}  # font object -> ToUnicode object
    standard: set[int] = set()  # font objects using a standard 14 encoding
    cmaps: dict[int, _CMap] = {}
    resources: dict[str, int] = {}  # resource name -> font object
    contents: list[bytes] = []
    objdicts: dict[int, bytes] = {}

    def font_refs(region: bytes) -> None:
        for name, target in re.findall(rb"/([\w\d]+)\s+(\d+)\s+\d+\s+R", region):
            resources.setdefault(name.decode("ascii", errors="ignore"), int(target))

    def font_dict_of(region: bytes) -> None:
        for sub in re.findall(rb"/Font\s*<<(.*?)>>", region, re.S):
            font_refs(sub)
        ref = re.search(rb"/Font\s+(\d+)\s+\d+\s+R", region)
        if ref and int(ref.group(1)) in objdicts:
            font_refs(objdicts[int(ref.group(1))])

    objects = list(_objects(raw))
    for num, dict_part, _stream in objects:
        objdicts.setdefault(num, dict_part)
    for num, dict_part, stream in objects:
        if b"/Font" in dict_part and b"/ToUnicode" in dict_part:
            ref = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", dict_part)
            fonts[num] = int(ref.group(1)) if ref else None
        elif b"/Font" in dict_part and b"/BaseFont" in dict_part:
            base = re.search(rb"/BaseFont\s*/([A-Za-z0-9-]+)", dict_part)
            if base and base.group(1).decode("ascii", errors="ignore") in _STANDARD_FONTS:
                standard.add(num)
        font_dict_of(dict_part)
        res = re.search(rb"/Resources\s+(\d+)\s+\d+\s+R", dict_part)
        if res and int(res.group(1)) in objdicts:
            font_dict_of(objdicts[int(res.group(1))])
        if stream is None:
            continue
        decoded = _inflate(dict_part, stream)
        if b"ToUnicode" in dict_part or b"beginbf" in decoded[:4096]:
            try:
                cmaps[num] = _parse_cmap(decoded.decode("ascii", errors="ignore"))
            except re.error:
                continue
        elif b"BT" in decoded:
            contents.append(decoded)

    font_cmaps = {
        num: cmaps[ref] for num, ref in fonts.items() if ref in cmaps
    }
    blocks: list[str] = []
    current: list[str] = []
    pending_sep = ""
    last_y: float | None = None
    font: str | None = None
    in_text = False
    unmapped = 0
    fallbacks: set[str] = set()

    def decode(data: bytes) -> str:
        nonlocal unmapped
        cmap = font_cmaps.get(resources.get(font, -1)) if font else None
        target = resources.get(font)
        if cmap is None and target not in standard:
            fallbacks.add(font or "?")
        text, missed = _decode_codes(data, cmap)
        unmapped += missed
        return text

    def emit(text: str) -> None:
        nonlocal pending_sep
        if current and pending_sep:
            current.append(pending_sep)
        current.append(text)
        pending_sep = ""

    for content in contents:
        last_y = None  # positions do not carry across content streams
        for token in _TOKEN.finditer(content):
            word = token.group(0)
            if word == b"BT":
                current = []
                pending_sep = ""
                in_text = True
            elif word == b"ET":
                if current:
                    blocks.append("".join(current))
                    current = []
                pending_sep = ""
                in_text = False
            elif not in_text:
                continue  # show ops outside text objects (e.g. images) are noise
            elif word == b"T*":
                pending_sep = "\n"
            elif word.endswith(b"Tf"):
                match = _FONT_OP.match(word)
                if match:
                    font = match.group(1).decode("ascii", errors="ignore")
            elif word.endswith((b"Td", b"TD")):
                match = _TD_OP.match(word)
                if match:
                    # NaN (garbage input) falls through to a newline, which
                    # never glues unrelated lines together.
                    pending_sep = " " if _num(match.group(2)) == 0.0 else "\n"
            elif word.endswith(b"Tm"):
                match = _TM_OP.match(word)
                if match:
                    y = _num(match.group(2))
                    pending_sep = "\n" if last_y is None or y != last_y else " "
                    last_y = y
            elif word.endswith((b"Tj", b"TJ", b"'")):
                if word.endswith(b"'"):
                    pending_sep = "\n"
                    inner = word[:-1].rstrip()
                else:
                    inner = word[:-2].rstrip()
                if inner.startswith(b"["):
                    parts = [_string_bytes(p) for p in _TJ_PART.findall(inner[1:])]
                    emit("".join(decode(p) for p in parts))
                else:
                    emit(decode(_string_bytes(inner)))
        if current:
            blocks.append("".join(current))
            current = []

    text = "\n".join(block for block in blocks if block.strip())
    if not text.strip() or not _looks_like_text(text):
        return Extraction(text="", status="unavailable")
    if fallbacks or unmapped:
        return Extraction(
            text=text, status="partial",
            unmapped_codes=unmapped, fallback_fonts=sorted(fallbacks),
        )
    return Extraction(text=text, status="full")

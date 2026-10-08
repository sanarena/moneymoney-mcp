"""Tests for the stdlib PDF text extractor (hand-crafted fixtures)."""

from __future__ import annotations

import zlib

from moneymoney_mcp import pdftext


def _pdf(objects: list[bytes]) -> bytes:
    out = [b"%PDF-1.5\n"]
    for i, body in enumerate(objects, start=1):
        out.append(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    return b"".join(out)


PLAIN = _pdf(
    [
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 3 0 R >>",
        b"<< /Length 90 >>\nstream\nBT /F1 12 Tf (Hello \\(World\\) \\101) Tj ET\nendstream",
    ]
)

CMAP = (
    b"/CIDInit /ProcSet findresource begin\n"
    b"begincodespacerange\n<00> <FF>\nendcodespacerange\n"
    b"beginbfchar\n<01> <0048>\n<02> <0069>\nendbfchar\n"
    b"beginbfrange\n<10> <12> <0061>\n<20> <21> [<006C> <006F>]\nendbfrange\n"
    b"end\n"
)

CUSTOM = _pdf(
    [
        b"<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+Custom /ToUnicode 2 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(CMAP) + CMAP + b"endstream",
        b"<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length 200 >>\nstream\n"
        b"BT /F1 12 Tf (\x01\x02) Tj ET\n"
        b"BT [(\x10) 120 (\x11\x12)] TJ ET\n"
        b"BT <01> Tj ET\n"
        b"BT (\x20\x21) Tj ET\n"
        b"endstream",
    ]
)


def _compressed(pdf: bytes) -> bytes:
    import re

    def flate(match):
        return b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % 0 + zlib.compress(
            match.group(1)
        ) + b"\nendstream"

    return re.sub(
        rb"<< /Length \d+( /Filter /FlateDecode)? >>\nstream\n(.*?)endstream",
        lambda m: b"<< /Filter /FlateDecode >>\nstream\n" + zlib.compress(m.group(2)) + b"\nendstream",
        pdf,
        flags=re.S,
    )


def test_plain_helvetica_is_full():
    out = pdftext.extract(PLAIN)
    assert out.status == "full"
    assert out.text == "Hello (World) A"


def test_custom_cmap_decodes_tj_hex_and_arrays():
    out = pdftext.extract(CUSTOM)
    assert out.status == "full", out
    assert out.text == "Hi\nabc\nH\nlo"


def test_compressed_streams_decode_identically():
    out = pdftext.extract(_compressed(CUSTOM))
    assert out.status == "full", out
    assert out.text == "Hi\nabc\nH\nlo"


def test_unknown_font_falls_back_and_reports_partial():
    pdf = _pdf(
        [
            b"<< /Type /Font /Subtype /TrueType /BaseFont /XYZ+Custom >>",
            b"<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 3 0 R >>",
            b"<< /Length 40 >>\nstream\nBT /F1 12 Tf (Gr\\374\\337e) Tj ET\nendstream",
        ]
    )
    out = pdftext.extract(pdf)
    assert out.status == "partial"
    assert out.text == "Grüße"
    assert out.fallback_fonts == ["F1"]


def test_unmapped_codes_are_skipped_and_counted():
    pdf = _pdf(
        [
            b"<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+Custom /ToUnicode 2 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(CMAP) + CMAP + b"endstream",
            b"<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length 60 >>\nstream\nBT /F1 12 Tf (\x01\xFF) Tj ET\nendstream",
        ]
    )
    out = pdftext.extract(pdf)
    assert out.status == "partial"
    assert out.text == "H"
    assert out.unmapped_codes == 1


def test_binary_without_text_is_unavailable():
    out = pdftext.extract(b"%PDF-1.5\n1 0 obj\n<< /Type /XObject >>\nstream\n\x00\x01\x02\nendstream\nendobj\n")
    assert out.status == "unavailable"
    assert out.text == ""


def test_garbage_does_not_crash():
    out = pdftext.extract(b"not a pdf at all \x00\xff\xfe Tj BT ET")
    assert out.status == "unavailable"


def test_unescape_octal_and_continuation():
    assert pdftext._unescape(b"a\\\nb\\101") == b"abA"


def test_shows_outside_text_objects_are_ignored():
    pdf = _pdf(
        [
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 3 0 R >>",
            b"<< /Length 60 >>\nstream\n"
            b"(Noise) Tj BT /F1 12 Tf (Signal) Tj ET (More) Tj\n"
            b"endstream",
        ]
    )
    out = pdftext.extract(pdf)
    assert out.text == "Signal"


def test_malformed_numbers_do_not_crash():
    pdf = _pdf(
        [
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 3 0 R >>",
            b"<< /Length 80 >>\nstream\n"
            b"BT /F1 12 Tf (A) Tj 1.2.3 4.5.6 Td (B) Tj ET\n"
            b"endstream",
        ]
    )
    out = pdftext.extract(pdf)
    assert out.text == "A\nB"


def test_length_wins_over_embedded_endstream_marker():
    body = b"BT /F1 12 Tf (keep endstream together) Tj ET"
    pdf = (
        b"%PDF-1.5\n"
        b"1 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"2 0 obj\n<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 3 0 R >>\nendobj\n"
        b"3 0 obj\n<< /Length " + str(len(body)).encode() + b" >>\nstream\n"
        + body
        + b"\nendstream\nendobj\n"
    )
    out = pdftext.extract(pdf)
    assert out.text == "keep endstream together"


def test_object_stream_and_indirect_resources_resolve():
    font = b"<< /Type /Font /Subtype /Type0 /BaseFont /X /ToUnicode 1 0 R >>"
    res = b"<< /Font << /F1 6 0 R >> >>"
    header = b"6 0 7 %d " % len(font)
    first = len(header)
    embedded = zlib.compress(header + font + res)
    pdf = _pdf(
        [
            b"<< /Length %d >>\nstream\n" % len(CMAP) + CMAP + b"endstream",
            b"<< /Type /ObjStm /N 2 /First %d /Filter /FlateDecode >>\nstream\n" % first
            + embedded
            + b"\nendstream",
            b"<< /Type /Page /Resources 7 0 R /Contents 4 0 R >>",
            b"<< /Length 40 >>\nstream\nBT /F1 12 Tf (\x01\x02) Tj ET\nendstream",
        ]
    )
    out = pdftext.extract(pdf)
    assert out.status == "full", out
    assert out.text == "Hi"


def test_spacing_follows_positioning_not_strings():
    pdf = _pdf(
        [
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 3 0 R >>",
            b"<< /Length 200 >>\nstream\n"
            b"BT /F1 12 Tf (H) Tj (i) Tj ET\n"
            b"BT (Hello) Tj 10 0 Td (World) Tj ET\n"
            b"BT (line1) Tj T* (line2) Tj ET\n"
            b"BT 1 0 0 1 50 700 Tm (A) Tj 1 0 0 1 60 700 Tm (B) Tj 1 0 0 1 60 680 Tm (C) Tj ET\n"
            b"endstream",
        ]
    )
    out = pdftext.extract(pdf)
    assert out.status == "full", out
    assert out.text == "Hi\nHello World\nline1\nline2\nA B\nC"


def test_garbage_gate_rejects_mojibake():
    pdf = _pdf(
        [
            b"<< /Type /Font /Subtype /TrueType /BaseFont /XYZ+Custom >>",
            b"<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 3 0 R >>",
            b"<< /Length 60 >>\nstream\nBT /F1 12 Tf (\x00\x01\x02\x03\x04\x05\x06\x07) Tj ET\nendstream",
        ]
    )
    out = pdftext.extract(pdf)
    assert out.status == "unavailable"
    assert out.text == ""

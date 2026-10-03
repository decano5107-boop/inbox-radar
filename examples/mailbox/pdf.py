"""A minimal PDF writer for fixtures: one page, plain text lines, standard library only.

The generated files are uncompressed and use a standard font, which is all a text extractor
needs. They exist so the tests and the demo can exercise "the figures are only in the
attachment" without shipping a binary or a PDF library.
"""

WIN_ANSI_EXTRAS = {"€": b"\x80", "—": b"\x97", "–": b"\x96", "·": b"\xb7"}


def _encode(text):
    out = bytearray()
    for ch in text:
        if ch in WIN_ANSI_EXTRAS:
            out += WIN_ANSI_EXTRAS[ch]
            continue
        b = ch.encode("latin-1", "replace")
        if b in (b"(", b")", b"\\"):
            out += b"\\" + b
        else:
            out += b
    return bytes(out)


def make_pdf(lines, font_size=11):
    """Return the bytes of a one-page PDF that shows `lines` top to bottom."""
    content = bytearray(b"BT\n/F1 %d Tf\n%d TL\n56 790 Td\n" % (font_size, font_size + 5))
    for line in lines:
        content += b"(" + _encode(line) + b") Tj T*\n"
    content += b"ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + bytes(content) + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)

# SPDX-License-Identifier: Apache-2.0
"""Pull the text out of the PDF this tool writes, with no dependency.

The leak check needs the words a reader would see, not the bytes of the file
(fonts are subset and text is stored as glyph numbers). This reads the shape the
Chromium print path writes: a classic cross-reference table, Flate streams, Type0
fonts with a ``ToUnicode`` map, and text shown with ``Tj`` and ``TJ``. It is not a
general PDF reader. Anything it does not understand is an error, never an empty
result, so the check cannot pass because the extractor was blind:

* a form XObject (nested content) is refused, since its text would be missed;
* a page with text operators but no usable ``ToUnicode`` map is refused;
* a document with no pages, or with no text at all, is refused.

Limits: 20,000 objects, 16 MiB per decompressed stream, 64 MiB in total, 300
pages. Text drawn as an image is out of reach of any text search.
"""

from __future__ import annotations

import re
import zlib

MAX_OBJECTS = 20_000
MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PAGE_COUNT = 300
MAX_DEPTH = 16


class PdfTextError(ValueError):
    """The PDF could not be read well enough to search its text."""


_OBJ = re.compile(rb"(?<![0-9])(\d{1,7}) (\d{1,5}) obj\b")
_REF = re.compile(rb"(\d{1,7}) \d{1,5} R")
_LENGTH = re.compile(rb"/Length\s+(\d+)(?!\s+\d+\s+R)")


class _Object:
    __slots__ = ("body", "stream")

    def __init__(self, body: bytes, stream: bytes | None) -> None:
        self.body = body
        self.stream = stream


def _parse_objects(data: bytes) -> dict[int, _Object]:
    objects: dict[int, _Object] = {}
    pos = 0
    while True:
        match = _OBJ.search(data, pos)
        if match is None:
            break
        if len(objects) >= MAX_OBJECTS:
            raise PdfTextError("the PDF has too many objects")
        number = int(match.group(1))
        start = match.end()
        end_obj = data.find(b"endobj", start)
        stream_kw = data.find(b"stream", start)
        if stream_kw != -1 and (end_obj == -1 or stream_kw < end_obj):
            body = data[start:stream_kw]
            first = stream_kw + len(b"stream")
            if data[first : first + 2] == b"\r\n":
                first += 2
            elif data[first : first + 1] == b"\n":
                first += 1
            length = _LENGTH.search(body)
            if length is not None and first + int(length.group(1)) <= len(data):
                stop = first + int(length.group(1))
            else:
                stop = data.find(b"endstream", first)
                if stop == -1:
                    raise PdfTextError("a stream has no end")
            objects[number] = _Object(body, data[first:stop])
            pos = stop
        else:
            if end_obj == -1:
                raise PdfTextError("an object has no end")
            objects[number] = _Object(data[start:end_obj], None)
            pos = end_obj
    return objects


class _Budget:
    def __init__(self) -> None:
        self.total = 0

    def inflate(self, raw: bytes, body: bytes) -> bytes:
        if b"/FlateDecode" not in body:
            if re.search(rb"/Filter", body):
                raise PdfTextError("a stream uses an unsupported filter")
            data = raw
        else:
            inflater = zlib.decompressobj()
            try:
                data = inflater.decompress(raw, MAX_STREAM_BYTES + 1)
            except zlib.error as exc:
                raise PdfTextError("a stream could not be decompressed") from exc
            if len(data) > MAX_STREAM_BYTES or inflater.unconsumed_tail:
                raise PdfTextError("a stream is larger than the limit")
            if not inflater.eof:
                raise PdfTextError("a stream is truncated: it is not complete")
            if inflater.unused_data:
                raise PdfTextError("a stream has trailing bytes after its end")
        self.total += len(data)
        if self.total > MAX_TOTAL_BYTES:
            raise PdfTextError("the PDF is larger than the limit when unpacked")
        return data


def _balanced(body: bytes, start: int) -> bytes:
    """The ``<< ... >>`` dictionary that begins at or after ``start``."""
    begin = body.find(b"<<", start)
    if begin == -1:
        return b""
    depth, i = 0, begin
    while i < len(body) - 1:
        pair = body[i : i + 2]
        if pair == b"<<":
            depth += 1
            i += 2
        elif pair == b">>":
            depth -= 1
            i += 2
            if depth == 0:
                return body[begin:i]
        else:
            i += 1
    raise PdfTextError("an unterminated dictionary")


def _entry(body: bytes, key: bytes) -> bytes | None:
    match = re.search(rb"/" + key + rb"(?![A-Za-z0-9])", body)
    return None if match is None else body[match.end() :]


def _ref_after(body: bytes, key: bytes) -> int | None:
    tail = _entry(body, key)
    if tail is None:
        return None
    ref = _REF.match(tail.lstrip())
    return None if ref is None else int(ref.group(1))


def _refs_in_array(tail: bytes) -> list[int]:
    start = tail.find(b"[")
    end = tail.find(b"]", start)
    if start == -1 or end == -1:
        return []
    return [int(m.group(1)) for m in _REF.finditer(tail[start:end])]


def _hex_text(hexdigits: bytes) -> str:
    raw = bytes.fromhex(re.sub(rb"\s", b"", hexdigits).decode("ascii"))
    return raw.decode("utf-16-be", errors="replace")


class _CMap:
    def __init__(self, text: str) -> None:
        self.single: dict[int, str] = {}
        self.width = 2
        space = re.search(r"begincodespacerange\s*<([0-9A-Fa-f]+)>", text, flags=re.S)
        if space is not None:
            self.width = max(1, len(space.group(1)) // 2)
        for block in re.findall(r"beginbfchar(.*?)endbfchar", text, flags=re.S):
            for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
                self.single[int(src, 16)] = _hex_text(dst.encode())
        for block in re.findall(r"beginbfrange(.*?)endbfrange", text, flags=re.S):
            for lo, hi, rest in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])",
                block,
            ):
                first, last = int(lo, 16), int(hi, 16)
                if last < first or last - first > 0xFFFF:
                    continue
                if rest.startswith("["):
                    for offset, item in enumerate(
                        re.findall(r"<([0-9A-Fa-f]*)>", rest)
                    ):
                        self.single[first + offset] = _hex_text(item.encode())
                else:
                    base = _hex_text(rest[1:-1].encode())
                    for offset in range(last - first + 1):
                        self.single[first + offset] = (
                            base[:-1] + chr(ord(base[-1]) + offset) if base else ""
                        )

    def decode(self, raw: bytes) -> str:
        if len(raw) % self.width:
            raise PdfTextError("text has an incomplete character code")
        out: list[str] = []
        for i in range(0, len(raw), self.width):
            code = int.from_bytes(raw[i : i + self.width])
            if code not in self.single:
                raise PdfTextError("text has an unmapped character code")
            out.append(self.single[code])
        return "".join(out)


_TOKEN = re.compile(
    rb"""
      (?P<name>/[^\s/\[\]<>()]+)
    | <(?P<hex>[0-9A-Fa-f\s]*)>
    | (?P<lit>\((?:\\.|[^\\()])*\))
    | (?P<open>\[) | (?P<close>\])
    | (?P<num>[+-]?\d*\.?\d+)
    | (?P<op>[A-Za-z'"*]+)
    | \s+ | <<|>>
    """,
    re.X | re.S,
)


def _literal(raw: bytes) -> bytes:
    body = raw[1:-1]
    out = bytearray()
    i = 0
    simple = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12}
    while i < len(body):
        ch = body[i : i + 1]
        if ch == b"\\" and i + 1 < len(body):
            nxt = body[i + 1 : i + 2]
            if nxt in simple:
                out.append(simple[nxt])
            elif nxt.isdigit():
                digits = re.match(rb"[0-7]{1,3}", body[i + 1 :])
                if digits is not None:
                    out.append(int(digits.group(0), 8) & 0xFF)
                    i += len(digits.group(0))
                    continue
            else:
                out += nxt
            i += 2
        else:
            out += ch
            i += 1
    return bytes(out)


def _show(operand: tuple[str, bytes], cmap: _CMap | None) -> str:
    kind, raw = operand
    data = bytes.fromhex(re.sub(rb"\s", b"", raw).decode()) if kind == "hex" else raw
    if cmap is None:
        raise PdfTextError("text uses a font with no ToUnicode map")
    return cmap.decode(data)


Operand = tuple[str, bytes]


def _page_text(content: bytes, fonts: dict[bytes, _CMap | None]) -> str:
    out: list[str] = []
    current: _CMap | None = None
    stack: list[Operand | list[Operand]] = []
    array: list[Operand] | None = None
    for match in _TOKEN.finditer(content):
        kind = match.lastgroup
        if kind == "name":
            stack.append(("name", match.group("name")[1:]))
        elif kind in ("hex", "lit"):
            raw = match.group("hex") if kind == "hex" else _literal(match.group("lit"))
            (array if array is not None else stack).append((str(kind), raw))
        elif kind == "open":
            array = []
        elif kind == "close":
            stack.append(array or [])
            array = None
        elif kind == "num":
            if array is None:
                stack.append(("num", match.group("num")))
        elif kind == "op":
            op = match.group("op")
            top = stack[-1] if stack else None
            if op == b"Tf" and len(stack) >= 2:
                name = stack[-2]
                if not isinstance(name, tuple) or name[0] != "name":
                    raise PdfTextError("a font is selected in a way not understood")
                if name[1] not in fonts:
                    raise PdfTextError("text uses a font the extractor cannot map")
                current = fonts[name[1]]
            elif op in (b"Tj", b"'", b'"') and isinstance(top, tuple):
                if top[0] in ("hex", "lit"):
                    out.append(_show(top, current))
            elif op == b"TJ" and isinstance(top, list):
                out.append("".join(_show(item, current) for item in top))
            elif op == b"ET":
                out.append("\n")
            stack.clear()
    return "".join(out)


def extract_pages(data: bytes) -> list[str]:
    """The text of each page, in order. Raises ``PdfTextError`` rather than
    returning less than the whole document."""
    if not data.startswith(b"%PDF-"):
        raise PdfTextError("not a PDF")
    objects = _parse_objects(data)
    budget = _Budget()
    for obj in objects.values():
        if re.search(rb"/Subtype\s*/Form\b", obj.body):
            raise PdfTextError(
                "the PDF has a form XObject; its text would not be searched"
            )
    root_ref = re.findall(rb"/Root\s+(\d+)\s+\d+\s+R", data)
    if not root_ref or int(root_ref[-1]) not in objects:
        raise PdfTextError("the PDF has no document catalog")
    pages_ref = _ref_after(objects[int(root_ref[-1])].body, b"Pages")
    if pages_ref is None:
        raise PdfTextError("the document catalog has no page tree")
    leaves: list[int] = []

    def walk(number: int, depth: int) -> None:
        if depth > MAX_DEPTH or number not in objects:
            raise PdfTextError("the page tree is not readable")
        body = objects[number].body
        if re.search(rb"/Type\s*/Pages\b", body):
            for kid in _refs_in_array(_entry(body, b"Kids") or b""):
                walk(kid, depth + 1)
        else:
            leaves.append(number)
        if len(leaves) > MAX_PAGE_COUNT:
            raise PdfTextError("the PDF has too many pages")

    walk(pages_ref, 0)
    if not leaves:
        raise PdfTextError("the PDF has no pages")

    cmaps: dict[int, _CMap | None] = {}

    def font_map(number: int) -> _CMap | None:
        if number not in cmaps:
            body = objects[number].body if number in objects else b""
            ref = _ref_after(body, b"ToUnicode")
            cmap_obj = objects.get(ref) if ref is not None else None
            if cmap_obj is None or cmap_obj.stream is None:
                cmaps[number] = None
            else:
                text = budget.inflate(cmap_obj.stream, cmap_obj.body)
                cmaps[number] = _CMap(text.decode("latin-1"))
        return cmaps[number]

    pages: list[str] = []
    for leaf in leaves:
        body = objects[leaf].body
        resources = _entry(body, b"Resources") or b""
        res_ref = _REF.match(resources.lstrip())
        if res_ref is not None and int(res_ref.group(1)) in objects:
            resources = objects[int(res_ref.group(1))].body
        else:
            resources = _balanced(resources, 0)
        font_tail = _entry(resources, b"Font") or b""
        font_ref = _REF.match(font_tail.lstrip())
        if font_ref is not None and int(font_ref.group(1)) in objects:
            font_dict = objects[int(font_ref.group(1))].body
        else:
            font_dict = _balanced(font_tail, 0)
        fonts: dict[bytes, _CMap | None] = {
            m.group(1): font_map(int(m.group(2)))
            for m in re.finditer(rb"/([^\s/\[\]<>()]+)\s+(\d+)\s+\d+\s+R", font_dict)
        }
        contents = _entry(body, b"Contents") or b""
        stripped = contents.lstrip()
        refs = (
            _refs_in_array(stripped)
            if stripped.startswith(b"[")
            else [int(m.group(1)) for m in [_REF.match(stripped)] if m is not None]
        )
        text: list[str] = []
        for ref in refs:
            content = objects.get(ref)
            if content is None or content.stream is None:
                raise PdfTextError("a page's content stream is missing")
            text.append(_page_text(budget.inflate(content.stream, content.body), fonts))
        pages.append("".join(text))
    if not "".join(pages).strip():
        raise PdfTextError("the PDF has no text")
    return pages

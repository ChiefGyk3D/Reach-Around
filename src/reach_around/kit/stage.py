# SPDX-License-Identifier: Apache-2.0
"""Stage the gated page for the printer: one sealed, self-contained document.

The input is ``kit.html`` from the gated directory (written by ``reach-around
build --gated-out``). It is treated as untrusted text: an override template, a
tampered file or a bug can put anything in it. The output is built from an
allowlist, never edited in place:

* only known layout tags and attributes are rebuilt (``class``, table ``scope``,
  ``datetime``, an ``https`` link target); the live-site chrome (skip link,
  navigation, footer with its cookie form, the inquiry form's aside, links back to the
  site) is dropped;
* a tag that can run code, fetch, nest a document or submit anything (script,
  iframe, form, object, embed, base, svg, video, a refresh meta, ...) stops the
  build instead of being quietly removed, because its presence means the page is
  not the one the builder wrote;
* the page's own stylesheets are read from the site output directory, only from
  ``/static/*.css``, never through a symbolic link, and inlined; ``@import``,
  ``@font-face`` and every ``url(...)`` are removed, so there is nothing to fetch;
* a local image (``/static/*.png|jpg|jpeg``) is embedded as a data URI after its
  metadata is dropped (PNG text chunks, JPEG EXIF and comments); any other type
  or a remote image is refused;
* a Content-Security-Policy meta forbids every load except the embedded data.

Limits are named constants below; going over one stops the build.
"""

from __future__ import annotations

import base64
import html
import os
import re
import stat
import struct
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_CSS_BYTES = 1024 * 1024
MAX_CSS_TOTAL = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES = 8
MAX_HREF = 2048
GATED_FILE = "kit.html"

CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "font-src 'none'; script-src 'none'; connect-src 'none'; frame-src 'none'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'"
)

# The printed page: white paper, no site chrome, no splitting a row or a card.
PRINT_CSS = (
    "@page{size:Letter;margin:0.6in}"
    ":root{color-scheme:light!important}"
    "html,body{background:#fff!important;margin:0!important}"
    "*,*::before,*::after{color:#111!important;background:transparent!important;"
    "box-shadow:none!important;text-shadow:none!important;min-height:0!important;"
    "backdrop-filter:none!important;filter:none!important;opacity:1!important;"
    "animation:none!important;transition:none!important}"
    "main{max-width:none!important;margin:0!important;padding:0!important}"
    ".skip-link,.cookie-banner{display:none!important}"
    "article,tr,li,.kit-numbers,.kit-engagement,.kit-partners,.kit-services,.kit-rates,.kit-testimonials,.kit-demographics,.kit-disclosure"
    "{break-inside:avoid}"
    "h1,h2,h3,h4{break-after:avoid}"
    "table{width:100%;border-collapse:collapse}"
    "th,td{border-bottom:1px solid #999!important}"
    "a{color:#0645ad!important;text-decoration:underline}"
)

# Tags whose presence means the page is not what the builder wrote.
_FORBIDDEN = frozenset(
    {
        "script",
        "iframe",
        "frame",
        "frameset",
        "object",
        "embed",
        "applet",
        "form",
        "input",
        "button",
        "select",
        "textarea",
        "base",
        "svg",
        "math",
        "video",
        "audio",
        "source",
        "track",
        "canvas",
        "portal",
        "style",
        "noscript",
        "template",
        "picture",
    }
)
# Dropped with everything inside them: the live-site chrome, and the inquiry form's
# <aside> (a form means nothing on paper; the owner details are not inside it).
_CHROME = frozenset({"footer", "nav", "aside"})
_KEEP = frozenset(
    {
        "header",
        "main",
        "section",
        "article",
        "div",
        "span",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "table",
        "caption",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "strong",
        "em",
        "b",
        "i",
        "small",
        "br",
        "hr",
        "a",
        "time",
        "dl",
        "dt",
        "dd",
        "blockquote",
        "code",
        "pre",
        "abbr",
        "img",
        "figure",
        "figcaption",
        "sup",
        "sub",
    }
)
_VOID = frozenset({"br", "hr", "img"})
_CLASS = re.compile(r"[A-Za-z0-9_-]+")
_DATETIME = re.compile(r"[0-9T:+\-.Z ]{1,40}")
_SCOPE = frozenset({"col", "row", "colgroup", "rowgroup"})
_SPAN = re.compile(r"[1-9][0-9]?")
_STATIC = re.compile(
    r"/static/((?:[A-Za-z0-9][A-Za-z0-9._-]*/)*[A-Za-z0-9][A-Za-z0-9._-]*)"
)


class StageError(ValueError):
    """The gated page cannot be staged safely."""


@dataclass(frozen=True)
class StagedPage:
    html: str


# --- reading files without following links ------------------------------------


def _read_regular(path: Path, limit: int, what: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        if path.is_symlink():
            raise StageError(f"{what} is a symbolic link; it is not followed") from exc
        raise StageError(f"{what} cannot be read ({exc.strerror})") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise StageError(f"{what} is not a regular file")
        if info.st_size > limit:
            raise StageError(f"{what} is larger than {limit} bytes")
        data = b""
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) > limit:
            raise StageError(f"{what} is larger than {limit} bytes")
        return data
    finally:
        os.close(fd)


def _static_file(site_dir: Path, url_path: str, suffixes: tuple[str, ...]) -> Path:
    """The file for a root-based ``/static/...`` link, confined to the site
    output's ``static`` directory with no symbolic link on the way."""
    match = _STATIC.fullmatch(url_path)
    if match is None or ".." in match.group(1).split("/"):
        raise StageError("a page asset link is not a plain /static/ path")
    if not match.group(1).lower().endswith(suffixes):
        raise StageError(
            "a page asset link has a file type that is not allowed; stylesheets "
            "must be .css and images must be .png, .jpg or .jpeg"
        )
    current = site_dir / "static"
    if current.is_symlink():
        raise StageError("the site static directory is a symbolic link")
    for part in match.group(1).split("/"):
        current = current / part
        if current.is_symlink():
            raise StageError("a page asset path goes through a symbolic link")
    return current


# --- stylesheets ---------------------------------------------------------------

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CSS_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?|\\(.)", re.S)
_LINE_CONTINUATION = re.compile(r"\\[\n\r\f]")
_FONT_FACE = re.compile(r"@font-face\s*\{[^{}]*\}", re.I)
_IMPORT = re.compile(r"@import\b[^;{}]*;?", re.I)
_URL = re.compile(r"url\s*\((?:[^()\\]|\\.)*\)", re.I)
_REMOTE_FUNCTIONS = re.compile(
    r"url\s*\(|@import|image-set\s*\(|\bimage\s*\(|\bsrc\s*\(", re.I
)


def _unescape_css(match: re.Match[str]) -> str:
    if match.group(1):
        code = int(match.group(1), 16)
        return chr(code) if 0 < code <= 0x10FFFF else "�"
    return match.group(2)


def clean_css(raw: bytes, what: str) -> str:
    """Stylesheet text with nothing in it that can fetch."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StageError(f"{what} is not valid UTF-8") from exc
    if "\x00" in text:
        raise StageError(f"{what} contains a NUL byte")
    text = _CSS_COMMENT.sub("", text)
    # A backslash before a line break is a CSS line continuation; the unescape
    # pass would turn it into a bare line break, so refuse it before decoding.
    if _LINE_CONTINUATION.search(text):
        raise StageError(f"{what} has a line continuation")
    text = _CSS_ESCAPE.sub(_unescape_css, text)
    # One unescape pass is not a fixed point: a backslash left over (written
    # directly, doubled, or produced by an escape) means the text would read
    # differently if unescaped again, so the filters below could be judging a
    # different string than a consumer sees. Refuse it rather than loop.
    if "\\" in text:
        raise StageError(f"{what} has a backslash that remains after unescaping")
    # Comments were removed before decoding, so decoding must not make new ones:
    # a comment opener that appears now would be skipped by a consumer and join
    # the text on either side, so the filters below would judge a different
    # string. Refuse it (fail closed) instead of stripping again.
    if "/*" in text:
        raise StageError(f"{what} has a comment that appears after unescaping")
    # A removal leaves a space, never a join, so the text on either side cannot
    # fuse into a new token or comment delimiter; the final text is re-checked.
    text = _FONT_FACE.sub(" ", text)
    text = _IMPORT.sub(" ", text)
    text = _URL.sub(" none ", text)
    if "/*" in text or "*/" in text:
        raise StageError(f"{what} has a comment delimiter after filtering")
    if _REMOTE_FUNCTIONS.search(text):
        raise StageError(f"{what} has a reference that could not be removed")
    if "<" in text:
        raise StageError(f"{what} contains a < character")
    return text


# --- images --------------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_KEEP = frozenset(
    {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"pHYs"}
)


def _scrub_png(data: bytes) -> bytes:
    out = bytearray(_PNG_MAGIC)
    pos = len(_PNG_MAGIC)
    seen_end = False
    while pos < len(data):
        if pos + 12 > len(data):
            raise StageError("an image (PNG) is cut short")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        end = pos + 12 + length
        if end > len(data):
            raise StageError("an image (PNG) is cut short")
        kind = data[pos + 4 : pos + 8]
        if kind in _PNG_KEEP:
            out += data[pos:end]
        seen_end = seen_end or kind == b"IEND"
        pos = end
        if seen_end:
            break
    if not seen_end or not out.startswith(_PNG_MAGIC + b"\x00\x00\x00\x0dIHDR"):
        raise StageError("an image (PNG) is not well formed")
    return bytes(out)


# JPEG markers, every one classified: anything else stops the build.
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})  # TEM and RSTn
_JPEG_STRUCTURE = frozenset(
    {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE,
        0xCF,  # frame headers (SOFn)
        0xC4,  # DHT
        0xCC,  # DAC
        0xDB,  # DQT
        0xDC,  # DNL
        0xDD,  # DRI
        0xDE,  # DHP
        0xDF,  # EXP
    }
)  # fmt: skip


def _scrub_jpeg(data: bytes) -> bytes:
    """Rebuild a JPEG from its structural segments only.

    Every segment is classified: structure the decoder needs is kept, EXIF, XMP,
    ICC and other application segments and comments are dropped (JFIF and the
    Adobe colour flag stay), and a marker that is not classified stops the build.
    After a start-of-scan header the entropy-coded data is copied up to the next
    real marker (an FF not followed by 00, a restart marker or another FF), so a
    segment between scans of a progressive file is scrubbed like any other and
    bytes that look like an end marker inside one are never mistaken for it.
    Only bytes through the end-of-image marker are kept."""
    if not data.startswith(b"\xff\xd8"):
        raise StageError("an image (JPEG) is not well formed")
    out = bytearray(b"\xff\xd8")
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF or pos + 1 >= len(data):
            raise StageError("an image (JPEG) is not well formed")
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte before a marker
            pos += 1
            continue
        if marker == 0xD9:
            out += b"\xff\xd9"
            return bytes(out)
        if marker in _JPEG_STANDALONE:
            out += data[pos : pos + 2]
            pos += 2
            continue
        known = (
            marker in _JPEG_STRUCTURE
            or marker == 0xDA
            or 0xE0 <= marker <= 0xEF
            or marker == 0xFE
        )
        if not known:
            raise StageError("an image (JPEG) has a marker that is not accepted")
        if pos + 4 > len(data):
            raise StageError("an image (JPEG) is cut short")
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        end = pos + 2 + length
        if length < 2 or end > len(data):
            raise StageError("an image (JPEG) is cut short")
        segment = data[pos:end]
        pos = end
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:
            if (marker == 0xE0 and segment[4:9] == b"JFIF\x00") or (
                marker == 0xEE and segment[4:9] == b"Adobe"
            ):
                out += segment
            continue
        out += segment
        if marker == 0xDA:  # entropy-coded data up to the next real marker
            start = pos
            while pos + 1 < len(data):
                if data[pos] == 0xFF and not (
                    data[pos + 1] == 0x00 or 0xD0 <= data[pos + 1] <= 0xD7
                ):
                    break
                pos += 1
            else:
                raise StageError("an image (JPEG) has no end marker")
            out += data[start:pos]
    raise StageError("an image (JPEG) has no end marker")


def embed_image(site_dir: Path, src: str) -> str:
    path = _static_file(site_dir, src, (".png", ".jpg", ".jpeg"))
    data = _read_regular(path, MAX_IMAGE_BYTES, "an image")
    if data.startswith(_PNG_MAGIC) and path.suffix.lower() == ".png":
        return "data:image/png;base64," + base64.b64encode(_scrub_png(data)).decode()
    if data.startswith(b"\xff\xd8") and path.suffix.lower() in (".jpg", ".jpeg"):
        return "data:image/jpeg;base64," + base64.b64encode(_scrub_jpeg(data)).decode()
    raise StageError(
        "an image must be a PNG (.png) or JPEG (.jpg, .jpeg) whose contents "
        "match its name; GIF, WebP and SVG are not accepted"
    )


# --- the page ------------------------------------------------------------------


class _Rebuild(HTMLParser):
    def __init__(self, site_dir: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.site_dir = site_dir
        self.body: list[str] = []
        self.css: list[bytes] = []
        self.images = 0
        self._in_body = False
        self._chrome_depth = 0
        self._open: list[tuple[str, bool]] = []  # (tag, emitted)
        self._skip_a = 0
        self._hidden: list[str] = []

    # -- helpers
    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        found = {k.lower(): (v or "") for k, v in attrs}
        out: list[str] = []
        classes = [c for c in found.get("class", "").split() if _CLASS.fullmatch(c)]
        if classes:
            out.append(f'class="{" ".join(classes)}"')
        if tag in ("th", "td") and found.get("scope") in _SCOPE:
            out.append(f'scope="{found["scope"]}"')
        if tag in ("th", "td"):
            for key in ("colspan", "rowspan"):
                if _SPAN.fullmatch(found.get(key, "")):
                    out.append(f'{key}="{found[key]}"')
        if tag == "time" and _DATETIME.fullmatch(found.get("datetime", "")):
            out.append(f'datetime="{found["datetime"]}"')
        if tag == "a":
            href = found.get("href", "")
            if (
                href.startswith("https://")
                and len(href) <= MAX_HREF
                and not any(c.isspace() or c in '"<>' or ord(c) < 32 for c in href)
            ):
                out.append(f'href="{html.escape(href, quote=True)}" rel="noreferrer"')
        if tag == "img":
            self.images += 1
            if self.images > MAX_IMAGES:
                raise StageError(f"the page has more than {MAX_IMAGES} images")
            out.append(f'src="{embed_image(self.site_dir, found.get("src", ""))}"')
            out.append(f'alt="{html.escape(found.get("alt", ""), quote=True)}"')
        return (" " + " ".join(out)) if out else ""

    # -- parser events
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {k.lower() for k, _ in attrs}
        if self._chrome_depth:
            if tag in _CHROME:
                self._chrome_depth += 1
            return
        if tag in _CHROME:
            self._chrome_depth = 1
            return
        if tag == "link":
            self._link(attrs)
            return
        if tag == "meta":
            if any(k.lower() == "http-equiv" for k, _ in attrs):
                raise StageError("the page has a meta http-equiv tag")
            return
        if tag in _FORBIDDEN:
            raise StageError(f"the page has a <{tag}> element, which is not allowed")
        if tag == "body":
            self._in_body = True
            return
        if not self._in_body:
            return
        if self._hidden or "hidden" in names:
            if tag not in _VOID:
                self._hidden.append(tag)
            return
        classes = dict(attrs).get("class") or ""
        if tag == "a" and "skip-link" in classes.split():
            self._skip_a += 1
            self._open.append((tag, False))
            return
        if tag == "a":
            href = (dict(attrs).get("href") or "").strip()
            if href.startswith("/") and not href.startswith("//"):
                # a link inside the live site means nothing on paper
                self._skip_a += 1
                self._open.append((tag, False))
                return
        if tag not in _KEEP:
            self._open.append((tag, False))
            return
        self.body.append(f"<{tag}{self._attrs(tag, attrs)}>")
        if tag not in _VOID:
            self._open.append((tag, True))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and not self._chrome_depth and tag in _KEEP:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._chrome_depth:
            if tag in _CHROME:
                self._chrome_depth -= 1
            return
        if self._hidden:
            # close only the matching open element; a stray end tag changes
            # nothing, so hidden content stays hidden until its own element ends
            if tag in self._hidden:
                index = len(self._hidden) - 1 - self._hidden[::-1].index(tag)
                del self._hidden[index:]
            return
        if tag == "body":
            self._in_body = False
            return
        if tag in _VOID or not self._in_body:
            return
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                for name, emitted in reversed(self._open[index:]):
                    if emitted:
                        self.body.append(f"</{name}>")
                    elif name == "a" and self._skip_a:
                        self._skip_a -= 1
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        if (
            self._in_body
            and not self._chrome_depth
            and not self._hidden
            and not self._skip_a
        ):
            self.body.append(html.escape(data, quote=False))

    def _link(self, attrs: list[tuple[str, str | None]]) -> None:
        found = {k.lower(): (v or "") for k, v in attrs}
        if "stylesheet" not in found.get("rel", "").lower().split():
            return
        path = _static_file(self.site_dir, found.get("href", ""), (".css",))
        self.css.append(_read_regular(path, MAX_CSS_BYTES, "a stylesheet"))


def _check_gated_dir(gated_dir: Path) -> None:
    try:
        info = os.lstat(gated_dir)
    except OSError as exc:
        raise StageError(
            f"the gated directory cannot be read ({exc.strerror})"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StageError("the gated directory is not a plain directory")
    if info.st_mode & 0o077:
        raise StageError(
            "the gated directory can be entered by someone other than the owner; "
            "fix it with chmod 700 before the private kit is printed from it"
        )


def stage_page(gated_dir: Path, site_dir: Path) -> StagedPage:
    """The sealed print document for ``gated_dir/kit.html``; ``site_dir`` is the
    public output the page's stylesheets and images come from."""
    _check_gated_dir(gated_dir)
    page = gated_dir / GATED_FILE
    if not os.path.lexists(page):
        raise StageError(
            f"{GATED_FILE} is not in the gated directory; run reach-around build "
            "with --gated-out first"
        )
    raw = _read_regular(page, MAX_PAGE_BYTES, GATED_FILE)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StageError(f"{GATED_FILE} is not valid UTF-8") from exc
    if "\x00" in text:
        raise StageError(f"{GATED_FILE} contains a NUL byte")
    site_root = Path(os.path.abspath(site_dir))
    if site_root.is_symlink() or not site_root.is_dir():
        raise StageError(
            "the site output directory is a symbolic link or not a directory; "
            "use the real path"
        )
    parser = _Rebuild(site_root)
    parser.feed(text)
    parser.close()
    if not parser.body:
        raise StageError(f"{GATED_FILE} has no page content")
    css_parts: list[str] = []
    total = 0
    for sheet in parser.css:
        total += len(sheet)
        if total > MAX_CSS_TOTAL:
            raise StageError("the stylesheets are larger than the limit")
        css_parts.append(clean_css(sheet, "a stylesheet"))
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        "<title>Media kit</title>"
        f"<style>{''.join(css_parts)}</style><style>{PRINT_CSS}</style>"
        f"</head><body>{''.join(parser.body)}</body></html>"
    )
    return StagedPage(html=document)

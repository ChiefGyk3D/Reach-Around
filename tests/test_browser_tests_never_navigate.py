# SPDX-License-Identifier: Apache-2.0
"""Repo guard: a browser test must never open the maintainer's mail client.

A mailto: or tel: navigation in a real browser hands off to a desktop program.
So a test file that drives a browser may not click such a link, may not mention
mailto: unless it also defines the ``__raOpen`` seam (the page script then
reports the URL instead of navigating), and may not use tel: at all.

The guard collects loosely (every test file that drives a browser, found by its
import) and asserts strictly (at least one such file exists, and none breaks a
rule). The negative cases prove each rule bites.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
THIS = Path(__file__).resolve()

_BROWSER_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:playwright|selenium|pyppeteer)\b", re.MULTILINE
)
# A selector that targets a mail or phone link, in any attribute-operator form.
_LINK_SELECTOR = re.compile(
    r"""\[\s*href\s*[\^*$|~]?=\s*["']?\s*(?:mailto|tel)\s*:""", re.I
)
_TEL = re.compile(r"(?<![A-Za-z0-9_])tel:", re.I)
_MAILTO = re.compile(r"mailto:", re.I)
_CLICK_ON_ADDRESS_LINK = re.compile(
    r"""email-address[^\n]*\.(?:click|dblclick|tap)\(|\.(?:click|dblclick|tap)\([^\n]*email-address"""
)


def is_browser_test(text: str) -> bool:
    return bool(_BROWSER_IMPORT.search(text))


def violations(text: str) -> list[str]:
    """The rules a browser test file breaks, each as a short sentence."""
    found: list[str] = []
    if _LINK_SELECTOR.search(text):
        found.append("selects a mailto: or tel: link by its href")
    if _CLICK_ON_ADDRESS_LINK.search(text):
        found.append("clicks the revealed mail link element")
    if _TEL.search(text):
        found.append("uses tel:")
    if _MAILTO.search(text) and "__raOpen" not in text:
        found.append("mentions mailto: without defining the __raOpen seam")
    return found


def browser_test_files() -> list[Path]:
    return [
        path
        for path in sorted(TESTS.rglob("*.py"))
        if path != THIS and is_browser_test(path.read_text("utf-8"))
    ]


def test_the_guard_finds_the_browser_tests() -> None:
    names = {path.name for path in browser_test_files()}
    assert "test_email_visual.py" in names
    assert "test_crypto_visual.py" in names


def test_no_browser_test_can_open_a_mail_or_phone_handler() -> None:
    files = browser_test_files()
    assert files, "no browser test files were collected"
    problems = {
        str(path.relative_to(TESTS)): violations(path.read_text("utf-8"))
        for path in files
    }
    assert {name: found for name, found in problems.items() if found} == {}


# --- negative cases: each rule must bite ------------------------------------------

_PW = "from playwright.sync_api import Page\n"


def test_the_detector_recognizes_a_browser_file() -> None:
    assert is_browser_test(_PW)
    assert is_browser_test("import playwright\n")
    assert not is_browser_test('x = "from playwright import y"\n')
    assert not is_browser_test("import json\n")


def test_selecting_a_mailto_link_is_caught() -> None:
    for sel in (
        'page.click("a[href^=mailto:]")',
        "page.locator(\"a[href^='mailto:x']\").click()",
        "page.locator('a[href*=\"mailto:\"]')",
        'page.click("a[href^=tel:]")',
        'page.click("a[ href $= \\"tel:\\" ]")',
    ):
        assert violations(_PW + sel), sel


def test_clicking_the_revealed_link_element_is_caught() -> None:
    assert violations(_PW + 'page.locator(".email-address").click()')
    assert violations(_PW + 'page.click(".email-address")')


def test_mailto_without_the_seam_is_caught_and_with_it_is_allowed() -> None:
    assert violations(_PW + 'assert url == "mailto:x@example.test"')
    assert not violations(
        _PW
        + 'ctx.add_init_script("window.__raOpen = f")\n'
        + 'assert url == "mailto:x@example.test"'
    )


def test_tel_is_always_caught() -> None:
    seam = "window.__raOpen = f\n"
    assert violations(_PW + seam + 'url = "tel:+15550100"')
    assert not violations(_PW + "hotel: str = 'x'\n")


def test_a_clean_file_passes() -> None:
    assert violations(_PW + 'page.get_by_role("button", name="Copy").click()') == []

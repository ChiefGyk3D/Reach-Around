<!-- SPDX-License-Identifier: Apache-2.0 -->
# Invisible bot check: accessibility checklist

For the hidden helper frame (`analytics.turnstile_beacon`, issue #610). The intent
is that a visitor, with or without assistive technology, can tell nothing has
changed: no new tab stop, nothing announced, no visible challenge, nothing that
holds a link back. Run this with the check on, against a built site with
Cloudflare's test key (`1x00000000000000000000AA`), before the check is turned on
for a live site. Record pass, fail or "not tested" for each line, with the browser
and version.

Needs a person. A rule engine cannot see a focus trap or hear an announcement.

## Keyboard

- [ ] Press Tab from the top of the home page through to the last link. The stops
      are the same, in the same order, as with the check off (build the page both ways and compare).
- [ ] No stop lands on the hidden frame or on anything inside it.
- [ ] Shift+Tab walks back through the same stops.
- [ ] Enter on a link follows it at once while the check is still running (the
      check never delays a link). Try it in the first second after load.
- [ ] The skip link still works.
- [ ] Opening `/e-frame/` directly shows a blank page, not a challenge you can
      focus.

## No focus trap, no focus theft

- [ ] After the page loads and the check finishes, focus is where it was (the
      document, or whatever you had focused). The frame never takes focus.
- [ ] Type in nothing: a page with a text field (the media kit form is not
      affected; use any page with an input if one is added) keeps its caret.

## Screen reader

Test with at least one of NVDA with Firefox, JAWS or NVDA with Chrome, VoiceOver
with Safari, TalkBack with Chrome.

- [ ] Reading the page top to bottom announces nothing about a check, a frame, a
      region or a widget.
- [ ] The browse-mode list of frames or landmarks does not include the helper
      (the frame is `aria-hidden="true"` and has a title only for validators).
- [ ] No live region message appears when the check starts or ends.
- [ ] Links read the same as with the check off.

## Motion and visibility

- [ ] With reduced motion on, nothing moves or appears.
- [ ] No visible widget, spinner, badge or logo appears at any time or size
      (320 px wide, 200% zoom, desktop).
- [ ] Layout does not shift when the frame is added or removed (watch the top of
      the page and the first link; zero shift).
- [ ] High contrast or forced colors: nothing new appears.

## When it fails or is blocked

- [ ] With the Turnstile origin blocked (a content blocker or a hosts-file entry), the page looks the same and the links work.
- [ ] With the helper path blocked, the same.
- [ ] After the 5 second wait the frame is gone from the page (inspect the DOM).
- [ ] With JavaScript off, the page and the links work (the check needs script,
      so nothing is added).
- [ ] With Global Privacy Control or Do Not Track on, no frame is ever added.

## Automated checks to run alongside

- The `accessibility-scan` tier on the home page with the check on and off; the
  violation lists must match.
- Lighthouse mobile on the home page with the check on and off; the score and the
  layout shift must not get worse (thresholds: `docs/performance.md`).

## Record

Date, browsers and versions, screen reader and version, each line above, and the
decision. Keep it with the issue; the privacy wording does not depend on it, the
decision to ship does.

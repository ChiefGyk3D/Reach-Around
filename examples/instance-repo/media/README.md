# media

Images for your page live here: avatar, favicon, background, mascot. Point at
them from `site.yaml` with a path relative to that file, for example
`avatar: media/avatar.png`.

- Use only files you own or have the right to publish.
- Photos carry hidden metadata (location, camera serial, author name). The
  engine copies image bytes as they are and does not strip it. Remove it before
  you commit, for example with `exiftool -all= photo.jpg`.
- Large files make every clone and every build slower. Resize before you add.
- Accepted formats, size limits and the SVG rules are in the engine's
  `docs/customizing.md`, section "Your own images".

Keep `.gitkeep` while this folder has no other files so Git tracks it.

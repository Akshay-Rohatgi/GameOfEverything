---
id: jpeg_comment_steg
description: Hides an operational note (an undocumented route, a hostname, an internal procedure) in a JPEG COM segment as base64, so it is invisible in the rendered image and invisible to a plaintext string search, but trivially recoverable from the file's metadata.
required_vars: [image_path, note_text]
---
# Atom: JPEG Comment Steganography
An image served by the application carries a note in its JPEG comment segment. The image renders normally and the note never appears on screen. It is recovered by looking at the file rather than the picture, which is a step most attackers reach only after exhausting the obvious surfaces.

### Logic Requirements:

1. The note MUST live in a JPEG COM segment (`0xFFFE`), not in EXIF and not in the pixels.
2. The note MUST be base64-encoded, so a plaintext search of the file does not find it.
3. The image MUST still decode and render unchanged.

Preferred, one line:

```bash
exiftool -overwrite_original -Comment="$(printf '%s' "$NOTE" | base64 -w0)" "$IMAGE"
```

Without exiftool, insert a `0xFFFE` marker after `SOI` with a two-byte big-endian length that includes the length field itself.

The note MUST read as internal engineering documentation — a service note, a migration reminder — not as a puzzle clue.

### Common Patterns:

- A device photo whose comment names the query parameters that expose a recording index.
- A build-server wallpaper carrying the internal artifact repository hostname.
- A scanned network diagram whose comment names the jump host.
- A vendor product image left carrying a support account name by the vendor's asset pipeline.

### Testing Guidance:

**Layer 1 (Internal):**
- Confirm the COM segment exists: `exiftool -Comment -b "$IMAGE"`
- Confirm the image still decodes and its dimensions are unchanged

**Layer 2 (External):**
- Fetch the image over HTTP, then extract and decode: `exiftool -Comment -b img.jpg | base64 -d`
- Assert the decoded text contains the operationally significant token

Assert on the DECODED value. A procedure that runs `strings` or `body_contains` against the raw image returns nothing on a perfectly healthy artifact, because the note is base64 — that is a false negative from the instrument, not evidence the atom is broken.

### Synthesis Guidance:

This atom provides knowledge, not access: afterwards the attacker can do nothing new mechanically, they simply know where to look. Place it upstream of an endpoint that is already reachable but not discoverable — an undocumented parameter, an unlinked path, a second API version.

Do not pair it with an entity that also leaks the same route in an error message or `robots.txt`; the step collapses to nothing while every test still passes. If a second copy is wanted for realism, make it stale.

Composes with `policy_derived_credential`: the comment can carry the rule while a separate artifact carries the inputs.

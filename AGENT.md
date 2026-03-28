# AGENT.md — Heptabase Export to Obsidian

This file provides context for AI coding agents (e.g., Claude Code, Cursor, Copilot) working in this repository.

---

## Project Purpose

A single-file Streamlit web app that converts a Heptabase `All-Data.json` export into Obsidian-compatible files:

- **Markdown cards** (`.md`) — one file per non-trashed Heptabase card
- **Obsidian Canvas files** (`.canvas`) — one file per Heptabase whiteboard

Primary goal: preserve **referential integrity** while keeping output filenames readable.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| Framework | Streamlit |
| Package manager | Poetry |
| Entry point | `app.py` (entire app in one file) |
| Output format | In-memory `io.BytesIO` ZIP archives |

---

## Input / Output Specification

### Input

- **Required**: `All-Data.json`
- **Optional**: Heptabase backup folder path (used to resolve local image binaries when JSON only has `fileId`)

### Output

| File | Contents | Trigger |
|---|---|---|
| `Cards.zip` | All non-trashed cards as `.md` + `assets/` (when resolvable) | User clicks download |
| `Canvas.zip` | All whiteboards as `.canvas` | User clicks download |

### Card filename convention

```
{sanitized_title}.md
```

- If duplicate title: `Title (2).md`, `Title (3).md`, ...
- Empty title fallback: `Untitled.md` (also deduplicated)

### Card frontmatter

```yaml
---
heptabase_id: "<uuid>"
heptabase_title: "<raw original title>"
heptabase_display_title: "<display title used in links>"
---
```

### Card link conversion

| Source (Heptabase) | Target (Obsidian) |
|---|---|
| `{{card <uuid>}}` | `[[{filename_stem}\|{title}]]` |

### Image conversion behavior

- Local/resolved image asset: `![[assets/<filename>]]` (Obsidian embed syntax)
- External image URL: `![alt](https://...)`
- Placeholder refs are also handled: `{{image ...}}`, `{{asset ...}}`, `{{file ...}}`

### Canvas node mapping

- Whiteboard filename: `{whiteboard_name}.canvas` (deduplicated with `(2)`, `(3)`)
- Each canvas node `file` is resolved via `card_id -> filename` index
- Edges use `cardInstance.id` (not coordinates)

---

## Architecture Notes

- **Single file**: all logic in `app.py`
- **Indexes built at parse time**:
  - `card_id -> filename`
  - `cardInstance.id -> canvas node`
  - `asset_id -> asset target/bytes`
- **In-memory ZIP**: no disk writes for generated archives
- **Stateless per run**: recompute on each Streamlit rerun

---

## Key Constraints

- Do **not** introduce disk I/O for ZIP creation outputs
- Keep card/canvas relationships driven by IDs, not title matching
- Preserve `heptabase_id` in frontmatter
- `isTrashed: true` cards must be excluded
- Filename uniqueness must be guaranteed by deterministic dedup suffixes

---

## Known Limitations

- Rich text -> Markdown is best-effort; unknown node types fallback to plain text
- Local image resolution from backup folder is filename-based; duplicate filenames across folders can cause occasional mismatches
- If only `All-Data.json` is provided and image nodes contain only `fileId` (no URL/base64), images may be missing unless backup folder path is supplied
- Canvas edge styling is not preserved (only connectivity)

---

## Development

```bash
# Install dependencies
poetry install

# Run locally
poetry run streamlit run app.py
```

No test suite currently exists.

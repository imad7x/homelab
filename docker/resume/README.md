# resume - a resume editor for everyone at home, with many templates and PDF download

A small web app in plain Python (standard library) with Chromium in the image for PDFs.
Each person at home has a tab, and each person keeps any number of copies of their
resume (one per kind of job, say). There are no logins.

Web UI: `http://192.168.0.10:8088` on the LAN. The editor is desktop-first (a list of
sections on the left, one section's form in the middle, the page itself on the right; the
section list shrinks to icons on a laptop screen) and switches to Edit / Preview tabs on a
phone.

## What it does

- **Library** - one tab per person, a card per resume with a live thumbnail. New resume
  (blank, or a copy of any existing one), duplicate, rename, move to another person,
  delete (moves the file to `state/trash/`), import, export.
- **Editor** - profile and links, experience, skills, education, certifications,
  projects, languages, one section at a time. Built around adding to it quickly:
  - Roles, schools and projects are cards that open in place; certifications and
    languages are one row each. Everything has a drag handle (reorder) and Undo on delete.
  - *Add role* puts a new role at the top with the job title focused; if an older
    role is still marked current it offers to set that role's end date.
  - In a list of points, Enter starts the next point (splitting at the caret), Backspace
    at the start joins a point to the one above, pasting several lines makes one point
    per line (leading `-`, `*`, `•`, `1)` are dropped), Alt+Up/Down or the handle moves
    a point.
  - Skills are tags in named groups: Enter or a comma adds one (a duplicate is
    flashed instead), a click renames one in place, dragging moves one within or between
    groups, and the group handle reorders groups.
  - Clicking anything on the preview opens it in the form (a bullet focuses that point).
  - `**double stars**` make words bold in the summary and points.
- **Saving** - about a second after typing stops. Each save carries the revision it was
  based on; a save based on an old revision (another tab or device saved in between) is
  refused and the page asks which version to keep. Before a save overwrites a copy, the
  stored one goes to `state/history/<id>/` unless the newest kept version is under 10
  minutes old, so History holds one version per editing session (200 kept per resume);
  Ctrl+S keeps one on demand. Restoring a version keeps the current one too.
- **Design** - template, accent colour (the template's own or any other) and paper (A4
  or US Letter), in a panel over the preview with every template drawn from your own
  content. The preview is the real page, redrawn on every keystroke, with dashed lines where
  pages are likely to break; Fit or 100% zoom.
- **PDF** - `GET /api/resumes/<id>/pdf` has Chromium print the page (about a second;
  cached until the resume, template or page code changes). The file is named
  `<Name> Resume.pdf`, never after the copy's own title.
- **Import / export** - an old `Resume_Editor.html` (the hand-made editor this replaced;
  its data, including copies re-saved by its Save button, is read out of the file) or a
  [JSON Resume](https://jsonresume.org/schema) `.json`. Export writes JSON Resume with the
  design under `meta.resumeApp`, so it opens in other JSON Resume tools too.

## Templates

| File | Name | Look |
| --- | --- | --- |
| `charcoal.js` | Charcoal | the original editor's design with the name and headline centred on a dark band, 70/30 split, contact, skills, education and certifications in a grey right column, Nunito Sans |
| `plain.js` | Plain | one column, no icons or colour blocks; safest for job portals that parse resumes |
| `sidebar.js` | Sidebar | coloured left column for contact, skills, education |
| `executive.js` | Executive | serif, centred heading, small-caps section rules |
| `compact.js` | Compact | dense, dates right-aligned, skills table; a long career on fewer pages |
| `timeline.js` | Timeline | experience along a line with dates on the left, skills as tags |
| `engineer.js` | Engineer | monospace dates and labels, a skills table |
| `banner.js` | Banner | colour band with name and contact, skill tags in a side column |

A template is one file in `app/web/templates/` calling `Resume.template({id, name,
description, width, accent, css, render(r, h)})`; `app/web/render.js` documents what
`r` (the resume, tidied) and `h` (escaping, dates, contacts, icons) hold. A new file
shows up in the picker on the next page load. Templates are drawn at their own `width`
in px and scaled to the paper; pages after the first get a 12 mm top margin and every
page a 12 mm bottom one. Class `keep` keeps an element on one page, `head` keeps a
heading with what follows. Fonts (Nunito Sans, Inter, Source Serif 4, IBM Plex Sans,
JetBrains Mono, Poppins, Lato; latin subsets, OFL) are served from `app/web/fonts/`.

## API

| Call | Purpose |
| --- | --- |
| `GET /api/library` | people and resumes (without content) |
| `GET /api/templates` | template files |
| `GET /api/resumes/<id>` | one resume: `{id, owner, title, rev, created, updated, design, resume}` |
| `GET /api/resumes/<id>/pdf` | the PDF; `?t=<template>&paper=a4\|letter` override the design, `?view=1` opens it inline |
| `GET /api/resumes/<id>/history[/<version>]` | kept versions / one of them |
| `POST /api/resumes` | new: `{owner, title, from: <id>}` or `{owner, title, resume, design}` |
| `POST /api/resumes/<id>` | save: `{rev, title?, owner?, design?, resume?, checkpoint?}`; 409 + the stored copy if `rev` is stale |
| `POST /api/resumes/<id>/delete` | move to `state/trash/` |
| `POST /api/people`, `/api/people/<id>`, `/api/people/<id>/delete` | add, rename, remove (only with no resumes left) |
| `POST /api/import/editor` | body: a `Resume_Editor.html` -> `{"resume": ...}` |
| `GET /api/ping` | health check |

POSTs need the header `X-Resume: 1` (no other site can send it without a CORS
preflight, which is never approved), and every request must be addressed to an IP,
localhost or a name in `ALLOWED_HOSTS`.

## Files

- `app/server.py` - the server; `app/web/` - the editor (`index.html`, `app.js`,
  `style.css`), `render.js` (draws a resume; the editor puts each preview, thumbnail and
  gallery card in a shadow root of its own via `Resume.mount`), `print.html`/`print.js`
  (the page Chromium prints), `templates/`, `fonts/`, icons and manifest.
- `state/people.json`, `state/resumes/<id>.json` - the data.
- `state/history/<id>/`, `state/trash/` - earlier and deleted versions.
- `state/pdf/` - the last PDF per resume, safe to delete.

Server edits need `docker restart resume`; page and template edits show on the next
reload.

# Smart Document Scanner

**CSE 438 — Digital Image Processing Lab**

A production-ready Django web application that detects, crops, straightens and
enhances document photographs using **classical digital image processing** —
OpenCV edge detection, contour analysis, perspective transformation, thresholding
and morphological operations. **No machine learning, no OCR, no external AI
services.** Everything runs server-side, deterministically, in a handful of
reusable NumPy/OpenCV modules.

---

## Table of contents

1. [Architecture overview](#architecture-overview)
2. [Features](#features)
3. [Project structure](#project-structure)
4. [Local setup (Windows + VS Code)](#local-setup-windows--vs-code)
5. [Environment configuration](#environment-configuration)
6. [Database setup & Neon PostgreSQL](#database-setup--neon-postgresql)
7. [Image storage & Cloudinary](#image-storage--cloudinary)
8. [Running the app & tests](#running-the-app--tests)
9. [GitHub workflow](#github-workflow)
10. [Render deployment](#render-deployment)
11. [Security notes](#security-notes)
12. [Performance notes](#performance-notes)
13. [Known limitations & troubleshooting](#known-limitations--troubleshooting)

---

## Architecture overview

```
Browser (vanilla JS)                Django 5.2                       CV services (OpenCV)
┌────────────────────┐   HTTPS   ┌─────────────────────┐          ┌──────────────────────┐
│ Home / upload      │ ────────► │ views.py (JSON API) │ ───────► │ validators.py  (safe │
│ Scanner workspace  │ ◄──────── │ models.py (Postgres │          │                decode)│
│  - corner handles  │  preview  │   metadata, images  │          │ detection.py   (Canny │
│  - sliders/filters │  URLs     │   on Cloudinary)    │          │                +contours
│  - before/after    │           │ pipeline re-runs    │          │ perspective.py (warp) │
│  - export buttons  │           │ from ORIGINAL every │          │ enhancement.py (CLAHE,│
└────────────────────┘           │ request)            │          │                Otsu…) │
                                 └─────────────────────┘          │ quality.py     (metrics)
     Images (originals/previews/thumbs) — Cloudinary CDN only  │ pdf_export.py  (ReportLab)
                                 ┌─────────────────────┐          │ pipeline.py    (compose)
                                 │ Neon PostgreSQL     │          └──────────────────────┘
                                 │ scan metadata only  │
                                 └─────────────────────┘
```

* **Single repository.** Django templates render every page; vanilla JavaScript
  adds interactivity. No React/Vue/Next.js, no separate frontend app.
* **Cloud-only images.** Every uploaded and generated image (original, preview,
  thumbnail) is stored on **Cloudinary** — never on the web server's disk and
  never inside database rows. Pages receive CDN URLs; the browser loads images
  directly from Cloudinary.
* **Stateless pipeline.** The stored original is the single source of truth.
  Every slider move re-runs `warp → enhance → filter` from it, so quality never
  degrades through repeated JPEG recompression. Exports run the same pipeline at
  full resolution, so the download matches the preview.
* **Ownership model.** Anonymous visitors get session-scoped scans; logging in
  (optional) attaches them to an account. No cross-session access is possible —
  every query filters through `ScanHistory.owned()`.

## Features

| Area | What actually works |
|---|---|
| Upload | Drag & drop + file picker, JPG/JPEG/PNG/WebP, ≤ 12 MB, magic-byte + Pillow + OpenCV validation, per-session page limit |
| Detection | Auto-detect document boundary (grayscale → Gaussian → Canny → dilate → external contours → polygon approximation), corner overlay, manual adjust |
| Manual crop | Four draggable corner handles with mouse, touch (Pointer Events) and keyboard (arrow keys, Shift = ×5); Apply / Reset / Use-full-image; server-side quad validation |
| Perspective | `cv2.getPerspectiveTransform` + `cv2.warpPerspective` with correct corner ordering and degenerate-size guards |
| Enhancement | Brightness, contrast, sharpness sliders; denoise (fastNlMeans/bilateral); CLAHE; all debounced and re-computed from the original |
| Filters | Original Color, Grayscale, Black & White (Otsu + morphological cleanup), Adaptive Threshold, Enhanced Color (gray-world WB + CLAHE + unsharp) |
| Quality feedback | Laplacian-variance blur estimate, brightness, contrast, resolution heuristics (advisory only, configurable) |
| Export | JPG (quality 60–96, default 90), PNG, single- and multi-page PDF (ReportLab, portrait/landscape, up to 30 pages), reorderable page strip |
| History | Postgres-backed `ScanHistory`, session- or user-scoped, delete, re-open, download processed image |
| Storage | Cloudinary-backed uploads via a custom Django storage backend; no image bytes ever touch the server disk |
| Accounts | Optional register/login/logout (Django auth); anonymous scans transfer to the account on login/registration |
| Ops | `/health/` endpoint, WhiteNoise compressed static, Gunicorn, `build.sh`, `render.yaml` blueprint |

## Project structure

```
smart-document-scanner/
├── manage.py
├── requirements.txt
├── build.sh                  # Render build: deps → checks → collectstatic → migrate
├── render.yaml               # optional Render Blueprint
├── .env.example
├── .gitignore
├── README.md
├── config/
│   ├── settings.py           # env-driven settings, Neon/SQLite switch, prod hardening
│   ├── urls.py               # root routes + DEBUG media serving
│   ├── wsgi.py               # Gunicorn entry point
│   └── asgi.py
├── scanner/
│   ├── models.py             # ScanHistory (metadata + Cloudinary refs)
│   ├── views.py              # pages, JSON APIs, exports, auth
│   ├── urls.py  forms.py  admin.py  tests.py
│   ├── migrations/
│   └── services/
│       ├── validators.py     # safe upload decoding + hard limits
│       ├── media_storage.py  # CloudinaryMediaStorage (Django Storage API)
│       ├── detection.py      # order_points, quad math, Canny+contour detection
│       ├── perspective.py    # getPerspectiveTransform / warpPerspective
│       ├── enhancement.py    # sliders, CLAHE, denoise, 5 filters
│       ├── quality.py        # blur/brightness/contrast/resolution heuristics
│       ├── pipeline.py       # resize → warp → enhance → filter composition
│       └── pdf_export.py     # ReportLab multi-page PDF
├── templates/                # base, home, scanner, history, about, 404, 500, registration/
├── static/
│   ├── css/style.css         # design system (light + dark themes)
│   ├── css/scanner.css       # workspace styles
│   ├── js/main.js            # theme toggle, nav, dropzone component, CSRF helpers
│   ├── js/home-upload.js     # homepage upload preview/validation
│   ├── js/scanner.js         # workspace: crop handles, debounced processing, exports
│   └── images/               # logo/favicon (SVG, no remote assets)
└── media/                    # only used as fallback when Cloudinary creds are absent
```

## Local setup (Windows + VS Code)

Requires **Python 3.12** (`python --version`).

1. **Open the project folder in VS Code** (`File → Open Folder…`), then open a
   terminal (`Terminal → New Terminal`).

2. **Create and activate a virtual environment**

   Windows (PowerShell):
   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\Activate.ps1
   ```
   If PowerShell blocks scripts, run once:
   `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
   (macOS/Linux: `python3.12 -m venv .venv && source .venv/bin/activate`)

3. **Install dependencies**
   ```powershell
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Configure the environment** — copy `.env.example` to `.env` and edit:
   ```powershell
   copy .env.example .env
   ```
   At minimum set a `SECRET_KEY`, your Neon `DATABASE_URL` and your Cloudinary
   `CLOUDINARY_URL` (see the Cloudinary section below). Without `DATABASE_URL`
   the app falls back to a local SQLite file; without Cloudinary credentials it
   falls back to the `media/` folder so you can still develop the UI offline.

5. **Apply migrations and (optionally) create an admin user**
   ```powershell
   python manage.py migrate
   python manage.py createsuperuser
   ```

6. **Run the development server**
   ```powershell
   python manage.py runserver
   ```
   Visit <http://127.0.0.1:8000/> — homepage. <http://127.0.0.1:8000/scanner/> — workspace.
   <http://127.0.0.1:8000/admin/> — admin (superuser).

## Environment configuration

All settings are environment-driven (loaded from `.env` locally, set in the
Render dashboard in production):

| Variable | Required | Example / notes |
|---|---|---|
| `SECRET_KEY` | yes (prod) | `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `DEBUG` | yes (prod) | `False` in production — never ship `True` |
| `ALLOWED_HOSTS` | yes (prod) | comma-separated, e.g. `smart-document-scanner.onrender.com` |
| `CSRF_TRUSTED_ORIGINS` | yes (prod) | e.g. `https://smart-document-scanner.onrender.com` |
| `DATABASE_URL` | yes (prod) | Neon Postgres URL; omitted locally → SQLite fallback |
| `CLOUDINARY_URL` | yes (prod) | `cloudinary://API_KEY:API_SECRET@CLOUD_NAME` |
| `SECURE_SSL_REDIRECT` | optional | `True` on Render (default when DEBUG=False) |
| `SCANNER_MAX_UPLOAD_SIZE` | optional | bytes, default 12 MB |

Never commit `.env`. The repo ships only `.env.example`.

## Database setup & Neon PostgreSQL

1. **Create a Neon project** — sign up at <https://neon.tech> (free tier is fine),
   click **New Project**, choose a region close to your users.
2. **Get the connection string** — in the project dashboard open **Connection
   Details**, select the **pooled** connection string and copy it. It looks like:
   ```
   postgresql://USER:PASSWORD@ep-xxxx-pooler.region.aws.neon.tech/neondb?sslmode=require
   ```
3. **Configure locally** — paste it into `.env` (untracked):
   ```
   DATABASE_URL=postgresql://USER:PASSWORD@ep-xxxx-pooler.region.aws.neon.tech/neondb?sslmode=require
   ```
   Keep `?sslmode=require` — Neon refuses plain connections and the settings
   add it automatically when missing.
4. **Add it to Render** — Dashboard → your web service → **Environment** →
   `DATABASE_URL` = the same connection string.
5. **Apply migrations**
   ```powershell
   python manage.py migrate
   ```
   On Render, `build.sh` runs migrations on every deploy.
6. **Verify** — open <http://127.0.0.1:8000/health/>; it should report
   `"database": "ok"`.

**Troubleshooting Neon**

| Symptom | Fix |
|---|---|
| `SSL required` / `server does not support SSL` | connection string missing `?sslmode=require` |
| `password authentication failed` | wrong user/password — copy the string again from Neon |
| `no pg_hba.conf entry` | you edited/removed `sslmode`; restore the original string |
| Timeouts from campus networks | try the **direct** (non-pooled) endpoint, or a different network |
| `relation scanner_scanhistory does not exist` | run `python manage.py migrate` |

## Image storage & Cloudinary

All images — uploaded originals, processed previews and thumbnails — are stored
on **Cloudinary**, in both local development and production. The application
code never changes between environments; only the source of the credentials
changes (`.env` locally, Render Environment Variables in production).

1. **Create a free Cloudinary account** at <https://cloudinary.com> →
   **Dashboard**. Note the *Cloud name*, *API key* and *API secret*.
2. **Build the credentials URL** — `cloudinary://API_KEY:API_SECRET@CLOUD_NAME`,
   e.g. `cloudinary://8623…:aX9…@dxyzabc`.
3. **Configure locally** — paste it into your untracked `.env`:
   ```
   CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME
   ```
   Alternatively set the three individual variables `CLOUDINARY_CLOUD_NAME`,
   `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` — either form works.
4. **Add it to Render** — Dashboard → your web service → **Environment** →
   `CLOUDINARY_URL` = the same value.
5. **Verify** — restart the dev server, upload an image, then check the
   Cloudinary dashboard **Media Library** → the file appears under
   `scans/originals/…`, and the page displays it straight from the CDN.

How it works inside the project:

* `scanner/services/media_storage.py` implements Django's `Storage` API on top
  of the official `cloudinary` SDK (`upload`, `exists`, `url`, `size`,
  `delete`). It is registered as the default storage in `settings.py` the
  moment Cloudinary credentials are present — no model or view changes needed.
* `ScanHistory` keeps only the storage paths and pixel metadata in Postgres;
  the browser receives `https://res.cloudinary.com/...` URLs directly.
* Deleting a scan (UI or admin) also destroys the Cloudinary resources.
* Uploaded bytes are held in memory only — the upload handler list contains
  just `MemoryFileUploadHandler`, so no temporary image files are written to
  the server disk at any point.
* Fallback: with **no** Cloudinary credentials configured, the app boots with
  the classic `media/` folder storage so pure-UI development works offline.
  In production you should always set `CLOUDINARY_URL`.

## Running the app & tests

```powershell
# full test suite (pages, upload security, storage backends, detection, warp,
# enhancement, filters, exports, PDF, privacy, auth, health)
python manage.py test

# Django system checks
python manage.py check

# deployment-readiness check (expected to warn while DEBUG=True locally)
python manage.py check --deploy
```

The test suite uses synthetic document photos generated with NumPy — no
fixtures or network access needed.

## GitHub workflow

**New repository**
```bash
cd smart-document-scanner
git init -b main
git add .
git status                # confirm .env / db.sqlite3 / media/ are NOT listed
git commit -m "Smart Document Scanner — CSE 438 lab project"
git remote add origin https://github.com/<your-username>/smart-document-scanner.git
git push -u origin main
```

**Existing repository (safe, non-destructive)**
```bash
git remote -v             # check current remotes first
git remote add origin https://github.com/<your-username>/smart-document-scanner.git
# or rename an existing remote instead of removing it:
# git remote rename origin old-origin
git pull origin main --rebase   # sync before first push
git push -u origin main
```

Never commit: `.env`, credentials, secret keys, virtual environments,
`__pycache__`, `db.sqlite3`, uploaded media. `.gitignore` already excludes them —
double-check with `git status` before committing.

**Render auto-deploy** — in your Render service → **Settings → Build & Deploy
→ Auto-Deploy**, keep “Deploy commit to `main` on push” enabled. Every `git push`
then rebuilds and migrates automatically.

## Render deployment

1. **Push the repo to GitHub** (above).
2. **Create the database first** — set up your Neon project and copy the
   connection string.
3. **Create the web service** — Render dashboard → **New + → Web Service** →
   connect the GitHub repo. Either use **Blueprint** (reads `render.yaml`) or:
   * **Runtime:** Python 3
   * **Build command:** `./build.sh`
   * **Start command:** `gunicorn config.wsgi:application --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`
   * **Health check path:** `/health/`
4. **Set environment variables** (Dashboard → Environment):
   | Key | Value |
   |---|---|
   | `SECRET_KEY` | a long random string (Render can generate one) |
   | `DEBUG` | `False` |
   | `ALLOWED_HOSTS` | `your-service.onrender.com` (your Render domain, no `https://`) |
   | `CSRF_TRUSTED_ORIGINS` | `https://your-service.onrender.com` |
   | `DATABASE_URL` | your Neon pooled connection string with `?sslmode=require` |
   | `CLOUDINARY_URL` | `cloudinary://API_KEY:API_SECRET@CLOUD_NAME` (same as local `.env`) |
   | `PYTHON_VERSION` | `3.12.6` |
   | `SECURE_SSL_REDIRECT` | `True` |
5. **Deploy** — the first build installs dependencies, collects static files and
   migrates the database. Watch the logs; open `https://…/health/` when it finishes.
6. **Redeploys** — pushing to `main` redeploys automatically.

**Render specifics to keep in mind**

* Free instances **sleep** after ~15 minutes idle; the first request afterwards
  takes ~30–60 s to cold-start. This is expected.
* **Images persist on Cloudinary**, so the ephemeral free-tier filesystem no
  longer matters for uploads — originals, previews and thumbnails survive
  redeploys and restarts, and pages serve them straight from the CDN. Only
  ScanHistory metadata lives in Neon.
* Never store image binaries in Postgres rows — this project intentionally keeps
  only metadata + storage references there.

## Security notes

* Server-side validation only: extension allow-list, magic-byte sniffing,
  Pillow structural verification, format cross-check, full OpenCV decode,
  side/pixel-count caps and a size limit — browser MIME types are ignored.
* Uploads are stored under generated UUID filenames; client filenames exist
  only as sanitized metadata, so path traversal is impossible.
* CSRF protection on every state-changing endpoint (both form POSTs and the
  JSON APIs via `X-CSRFToken`).
* Production hardening activates automatically with `DEBUG=False`: HSTS,
  secure cookies, SSL redirect (toggleable), nosniff, `X-Frame-Options: DENY`,
  referrer policy.
* Scan ownership is enforced on every query (`ScanHistory.owned`) — users and
  sessions can only see and delete their own scans.
* Errors return generic JSON messages; stack traces go to server logs only.
  Secrets and personal data are never logged.

## Performance notes

* Detection runs on a ≤720 px working copy; interactive processing caps at
  2200 px, previews are stored at ≤1100 px, exports run at up to 4000 px.
* Slider changes are debounced (420 ms) and in-flight requests are aborted, so
  dragging never queues a request storm — yet every response is recomputed from
  the pristine original (no quality ratchet).
* Thumbnails (≤340 px) power the page strip and history grid; full images load
  only in the main previews.
* `page_order` + indexed `(session_key, page_order)` / `(user, created_at)`
  indexes keep history queries fast; the history view caps at the latest 120
  entries.

## Known limitations & troubleshooting

* **Detection is heuristic.** Low-contrast paper/backgrounds, shadows, cropped
  edges or heavy blur can defeat automatic detection. The UI then offers the
  full-image fallback and manual corner editing — both always available.
* **Quality feedback is advisory.** Laplacian variance and friends are heuristics,
  not perceptual guarantees; thresholds live in `settings.SCANNER_QUALITY_THRESHOLDS`.
* **No PDF *input*, no OCR, no AI** — by design (course constraints). PDFs are
  output-only.
* **Free-tier Render** sleeps and has an ephemeral disk (see above).
* **`collectstatic` errors on deploy** usually mean a `{% static %}` reference
  to a file that doesn't exist — check the filename in templates.
* **`ManagerUnavailable` / apps not loaded** when running management commands
  manually: always run them inside the activated virtual environment from the
  project root.
* **Windows PowerShell blocks `Activate.ps1`** — see step 2 of local setup.
* **Port already in use locally** — `python manage.py runserver 127.0.0.1:8001`.

# download-x

Batch Twitter/X video downloader. Wraps `yt-dlp` to download best-quality MP4 from tweet URLs.

## Virtual Environment Setup

This sub-project uses its own isolated `.venv`. Set it up before any pip install or execution.

```bash
# Create (first time only)
python -m venv .venv

# Activate — Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Activate — macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Smoke Test

Run these after venv setup to confirm the environment is ready.

```bash
# Step 1 — verify yt-dlp version
# Windows
.venv\Scripts\python.exe -m yt_dlp --version
# macOS / Linux
.venv/bin/python -m yt_dlp --version

# Step 2 — dry-run using first URL in urls.txt (no download)
# Windows
.venv\Scripts\python.exe -m yt_dlp --simulate --no-playlist "https://x.com/i/status/2054757019442352585"
# macOS / Linux
.venv/bin/python -m yt_dlp --simulate --no-playlist "https://x.com/i/status/2054757019442352585"
```

Expected: version string (`2026.x.x`), then a dry-run log. If the tweet requires login, the simulate step will error — place `cookies.txt` in the project root and re-run with `--cookies cookies.txt` appended.

## How to Run

```bash
# Batch mode — reads from inputs/urls.txt
python twitter-vids-downloader.py

# Single or multiple URLs
python twitter-vids-downloader.py "https://x.com/i/status/2054757019442352585"
python twitter-vids-downloader.py URL1 URL2 URL3
```

## Key Files

- `twitter-vids-downloader.py` — Main script
- `requirements.txt` — `yt-dlp>=2024.1.1`
- `inputs/urls.txt` — Batch URL list (one per line, `#` = comment)
- `cookies.txt` — Optional; place in project root for authenticated downloads (private/restricted tweets)

## Output

Videos saved to: `~/Downloads/twitter-videos/`
Filename format: `{uploader} - {title_first_60_chars}.mp4`

## yt-dlp Options (hardcoded)

| Option | Value |
|--------|-------|
| Format | `bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best` |
| Output container | MP4 |
| Retries | 3 download + 3 fragment |
| Playlist | disabled (`--no-playlist`) |

## Dependencies

```
yt-dlp>=2024.1.1
```
No other external dependencies. Uses Python stdlib only (`subprocess`, `shutil`, `pathlib`, `sys`, `ctypes`).

## Authentication

Place a `cookies.txt` file in the project root. If present, it is passed to yt-dlp automatically. Required for private or login-gated tweets. Without it, the script warns and attempts unauthenticated download (public tweets still work).

## Error Handling

- Validates yt-dlp installation at startup (checks PATH and Python module fallback).
- Per-URL failures are logged and skipped; download continues for remaining URLs.
- Summary report at end lists success count and failed URLs.
- Ctrl+C exits gracefully.

## Note on yt-dlp Version

This tool uses `>=2024.1.1` (older minimum than download-utube). If Twitter/X downloads break, upgrade yt-dlp first: `pip install --upgrade yt-dlp`.

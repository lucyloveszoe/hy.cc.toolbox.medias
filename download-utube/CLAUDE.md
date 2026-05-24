# download-utube

Batch YouTube video downloader. Wraps `yt-dlp` to download best-quality MP4 with uploader name in the filename.

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

# Step 2 — dry-run (no download, just metadata fetch)
# Windows
.venv\Scripts\python.exe -m yt_dlp --simulate --no-playlist "https://youtu.be/BaW_jenozKc"
# macOS / Linux
.venv/bin/python -m yt_dlp --simulate --no-playlist "https://youtu.be/BaW_jenozKc"
```

Expected: version string (`2026.x.x`), then a dry-run log ending with `[info] BaW_jenozKc: Simulating...`. No file is written.

## How to Run

```bash
# Batch mode — reads from inputs/urls.txt
python youtube-vids-downloader.py

# Single or multiple URLs
python youtube-vids-downloader.py "https://youtu.be/VIDEO_ID"
python youtube-vids-downloader.py URL1 URL2 URL3
```

## Key Files

- `youtube-vids-downloader.py` — Main script (163 lines)
- `requirements.txt` — `yt-dlp[default]>=2026.3.17`
- `inputs/urls.txt` — Batch URL list (one per line, `#` = comment)
- `inputs/cookies.txt` — Optional; for authenticated downloads (age-restricted, etc.)

## Output

Videos saved to: `~/Downloads/youtube-videos/`
Filename format: `{uploader} - {title_first_60_chars}.mp4`

## yt-dlp Options (hardcoded)

| Option | Value |
|--------|-------|
| Format | `bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best` |
| Output container | MP4 |
| Retries | 3 download + 3 fragment |
| Playlist | disabled (`--no-playlist`) |
| JS runtime | `node` (for JS-heavy sites) |

## Dependencies

```
yt-dlp[default]>=2026.3.17
```
`[default]` extras include ffmpeg bindings and commonly needed plugins.
Node.js is optional — used by yt-dlp for JavaScript-based video sources.

## Authentication

Place a `cookies.txt` file at `inputs/cookies.txt`. If present, it is passed to yt-dlp automatically. Useful for downloading age-restricted or members-only content.

## Error Handling

- Checks yt-dlp availability (CLI binary or Python module) at startup.
- Per-URL failure is logged and skipped; remaining URLs continue.
- Summary report at end lists failed URLs.
- Ctrl+C exits gracefully.

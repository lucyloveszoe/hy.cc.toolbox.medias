# clone-scanner

GUI tool for finding duplicate and visually similar photos and videos in a folder (recursively). Inspired by Fast Duplicate File Finder.

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

Run these after setting up the venv to confirm the environment is ready. All commands use the venv python directly — no activation needed.

```bash
# Windows
.venv\Scripts\python.exe -c "import PIL; import imagehash; import send2trash; print('deps OK')"
ffmpeg -version

# macOS / Linux
.venv/bin/python -c "import PIL; import imagehash; import send2trash; print('deps OK')"
ffmpeg -version
```

Expected output: `deps OK` and ffmpeg version line. If ffmpeg is missing, video features will silently fail at runtime.

## How to Run

```bash
# ffmpeg must be installed separately and in PATH (required for video support)

python gui_viewer.py                        # opens directory picker on launch
python gui_viewer.py C:\path\to\media       # scans immediately
```

## Key Files

- `gui_viewer.py` — The only active file. Full implementation (1,569 lines): hashing, grouping, GUI.
- `group_similar.py` — Deprecated CLI prototype from v0. Do not modify; kept for reference only.
- `requirements.txt` — `Pillow`, `imagehash`, `send2trash`
- `progress.md` — Dev notes and version history

## Dependencies

**pip:**
- `Pillow>=10.0.0` — image I/O and thumbnails
- `imagehash>=4.3.1` — pHash + wHash computation
- `send2trash>=1.8.2` — safe recycle-bin deletion

**System (must be in PATH):**
- `ffmpeg` — required for all video functionality
  - macOS: `brew install ffmpeg`
  - Windows: download from ffmpeg.org, add to PATH

**Optional:**
- `pillow-heif` — HEIC support for iPhone photos

## Configuration Constants (in gui_viewer.py)

| Constant | Default | Meaning |
|----------|---------|---------|
| `HASH_THRESHOLD` | 15 | Hamming distance cutoff (1–30); lower = stricter |
| `VIDEO_SAMPLE_N` | 10 | Frames extracted per video for hashing |
| `VIDEO_SAMPLE_SECS` | 300.0 | Sampling window in seconds |
| `THUMB_SIZE` | (200, 200) | Preview thumbnail size |

The threshold is also adjustable at runtime via the GUI slider without editing code.

## Similarity Logic

**Images (two-step):**
1. SAME — identical file size + pixel dimensions
2. SIMILAR — both pHash and wHash Hamming distance ≤ threshold

**Videos (two-step):**
1. SAME — identical file size + resolution + duration (1-second precision)
2. SIMILAR — any matching frame pair (both pHash and wHash ≤ threshold) across 10 uniformly sampled frames

## Architecture Notes

- Scanning runs in a background thread; UI stays responsive via `self.after()` callbacks.
- Grouping uses Union-Find algorithm.
- GUI is pure `tkinter` (dark theme, no external UI library), 极简风格, 类似apple iOS App的视觉效果
- Export formats: CSV and Markdown (filename: `YYYY-MM-DD-similarity-report.{ext}`).
- Max 40 files displayed per group in the preview panel.

## Known Constraints

- After deleting files, click "Rescan" or "Apply" to refresh results — no auto-refresh.
- Video scanning is slow for large collections (10 ffmpeg invocations per video).
- HEIC support requires installing `pillow-heif` separately.

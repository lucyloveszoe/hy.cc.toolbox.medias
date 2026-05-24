# hy.cc.toolbox.medias

Personal media utility toolkit — 4 independent Python CLI/GUI tools for downloading and organizing media files.

## Sub-projects

| Directory | Purpose | Entry Point |
|-----------|---------|-------------|
| `clone-scanner/` | Find duplicate/similar photos & videos via perceptual hashing | `gui_viewer.py` |
| `download-telegram/` | Batch download videos from Telegram channels | `telegram-vids-downloader.py` |
| `download-utube/` | Batch download YouTube videos | `youtube-vids-downloader.py` |
| `download-x/` | Batch download Twitter/X videos | `twitter-vids-downloader.py` |

## Virtual Environments

**Each sub-project has its own isolated `.venv`.** Never install packages into the global Python environment or share a venv across sub-projects.

Before any `pip install`, script execution, or testing in a sub-project:

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

Always verify the venv is active (`which python` / `where python` should point inside `.venv`) before running anything.

## Shared Patterns

- All tools are Python 3, no shared library or package structure — each sub-project is fully self-contained.
- The three downloaders share the same URL input convention: either CLI args or a local `urls.txt` (one URL per line, `#` = comment).
- The three downloaders all output to `~/Downloads/{platform}-videos/` and support an optional `cookies.txt` for auth.
- No test suite exists. Verify behavior by running the tool directly.
- Cross-platform: must work on macOS, Windows, and Linux.

## General Rules

- Do not add shared utilities or abstract common code across sub-projects — they are intentionally independent.
- Do not delete files without explicit confirmation from the user.
- Do not invent data or fabricate examples.

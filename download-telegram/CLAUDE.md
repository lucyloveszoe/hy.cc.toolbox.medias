# download-telegram

Batch video downloader for Telegram channels (public and private). Uses Telethon to authenticate via the official Telegram API.

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

Run these after venv setup to confirm the environment is ready before attempting any real download.

```bash
# Windows
.venv\Scripts\python.exe -c "import telethon; print('telethon', telethon.__version__)"
.venv\Scripts\python.exe -c "import config; assert str(config.API_ID).isdigit() and len(config.API_HASH)==32, 'credentials not set'; print('config OK')"

# macOS / Linux
.venv/bin/python -c "import telethon; print('telethon', telethon.__version__)"
.venv/bin/python -c "import config; assert str(config.API_ID).isdigit() and len(config.API_HASH)==32, 'credentials not set'; print('config OK')"
```

Expected: `telethon 1.x.x` then `config OK`. If config check fails, edit `config.py` with your API credentials from https://my.telegram.org before proceeding.

## How to Run

```bash
# Batch mode — reads from urls.txt
python telegram-vids-downloader.py

# Single or multiple URLs
python telegram-vids-downloader.py "https://t.me/c/1393612161/124710"
python telegram-vids-downloader.py URL1 URL2
```

**First run** will prompt interactively for phone number and verification code. Session is saved to `telegram_session` and reused on subsequent runs.

## Setup Required

1. Get API credentials from https://my.telegram.org (create an app).
2. Edit `config.py`:
   ```python
   API_ID   = 12345678
   API_HASH = 'your_api_hash_here'
   ```
3. Add Telegram message URLs to `urls.txt` (one per line, `#` = comment).

## Key Files

- `telegram-vids-downloader.py` — Main script (298 lines), async/await via Telethon
- `config.py` — API credentials (API_ID, API_HASH) — **never commit real credentials**
- `urls.txt` — Batch URL list
- `telegram_session` — Auto-created on first login; do not delete unless re-authentication is needed

## URL Formats Supported

| Type | Format |
|------|--------|
| Private channel | `https://t.me/c/1234567890/42` |
| Public channel | `https://t.me/channelname/42` |

## Output

Videos saved to: `~/Downloads/telegram-videos/`
Filename: original filename from Telegram, or `telegram_{chat_id}_{msg_id}.mp4` as fallback.
Duplicate filenames get `_{message_id}` appended.

## Dependencies

```
telethon>=1.36.0
```
Python 3.10+ required (uses modern type hint syntax).

## Error Handling

- Flood-wait errors: auto-retries after the required wait period
- Non-video media (photos, documents): skipped with a warning
- Failed URLs: logged and skipped; summary shown at end
- Keyboard interrupt (Ctrl+C): graceful exit

## Security Note

`config.py` contains real API credentials. Do not commit it to public repos. The `telegram_session` file grants authenticated API access — treat it like a password.

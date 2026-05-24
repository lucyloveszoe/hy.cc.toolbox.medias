#!/usr/bin/env python3
"""
Telegram 视频批量下载工具
用法：
  python scripts/telegram-vids-downloader.py            # 从 urls.txt 批量下载
  python scripts/telegram-vids-downloader.py <URL>      # 下载单个链接

支持链接格式：
  https://t.me/c/1234567890/42          # 私人频道（数字 ID）
  https://t.me/channelname/42           # 公开频道（用户名）
"""

import sys
import re
import asyncio
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
URLS_FILE    = PROJECT_ROOT / "urls.txt"
CONFIG_FILE  = PROJECT_ROOT / "config.py"
SESSION_FILE = PROJECT_ROOT / "telegram_session"
OUTPUT_DIR   = Path.home() / "Downloads" / "telegram-videos"

# ── 颜色输出（Windows / macOS 兼容） ──────────────────────────
try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(
        ctypes.windll.kernel32.GetStdHandle(-11), 7
    )
except Exception:
    pass

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):    print(f"{GREEN}✔ {msg}{RESET}")
def warn(msg):  print(f"{YELLOW}⚠ {msg}{RESET}")
def err(msg):   print(f"{RED}✘ {msg}{RESET}")
def info(msg):  print(f"{CYAN}→ {msg}{RESET}")


# ── 检查依赖 ──────────────────────────────────────────────────
def check_dependencies():
    try:
        import telethon  # noqa: F401
    except ImportError:
        err("未找到 telethon，请先安装依赖：")
        err("  pip install -r requirements.txt")
        sys.exit(1)


# ── 加载配置 ──────────────────────────────────────────────────
def load_config():
    if not CONFIG_FILE.exists():
        err(f"找不到 config.py（路径：{CONFIG_FILE}）")
        err("请创建 config.py，内容如下：")
        err("  API_ID   = 你的api_id")
        err("  API_HASH = '你的api_hash'")
        sys.exit(1)

    import importlib.util
    spec = importlib.util.spec_from_file_location("config", CONFIG_FILE)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)

    api_id   = getattr(config, "API_ID",   None)
    api_hash = getattr(config, "API_HASH", None)

    if not api_id or not api_hash:
        err("config.py 中缺少 API_ID 或 API_HASH，请检查文件内容。")
        sys.exit(1)

    return api_id, api_hash


# ── 读取 URL 列表 ─────────────────────────────────────────────
def load_urls(cli_args: list[str]) -> list[str]:
    if cli_args:
        return [u.strip() for u in cli_args if u.strip()]

    if not URLS_FILE.exists():
        err(f"找不到 urls.txt（路径：{URLS_FILE}）")
        err("请在 urls.txt 中每行填写一个 Telegram 消息链接，或直接传入 URL 参数。")
        sys.exit(1)

    urls = [
        line.strip()
        for line in URLS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not urls:
        err("urls.txt 里没有有效链接，请检查文件内容。")
        sys.exit(1)
    return urls


# ── 解析 Telegram 消息链接 ────────────────────────────────────
def parse_telegram_url(url: str):
    """
    返回 (chat_identifier, message_id)
    chat_identifier 可以是：
      - 字符串用户名（公开频道）：'channelname'
      - 整数（私人频道）：-100xxxxxxxxxx
    """
    # 私人频道：https://t.me/c/1234567890/42
    m = re.match(r"https?://t\.me/c/(\d+)/(\d+)", url)
    if m:
        channel_id = int("-100" + m.group(1))
        message_id = int(m.group(2))
        return channel_id, message_id

    # 公开频道：https://t.me/channelname/42
    m = re.match(r"https?://t\.me/([^/]+)/(\d+)", url)
    if m:
        username   = m.group(1)
        message_id = int(m.group(2))
        return username, message_id

    return None, None


# ── 下载进度回调 ──────────────────────────────────────────────
def make_progress_callback(filename: str):
    last_pct = [-1]

    def callback(current, total):
        if total == 0:
            return
        pct = int(current / total * 100)
        if pct != last_pct[0] and pct % 5 == 0:
            bar_len  = 30
            filled   = int(bar_len * pct / 100)
            bar      = "█" * filled + "░" * (bar_len - filled)
            mb_cur   = current / 1024 / 1024
            mb_tot   = total   / 1024 / 1024
            print(f"\r  [{bar}] {pct:3d}%  {mb_cur:.1f}/{mb_tot:.1f} MB", end="", flush=True)
            last_pct[0] = pct
        if current >= total:
            print()

    return callback


# ── 下载单条消息的视频 ────────────────────────────────────────
async def download_one(client, url: str, index: int, total: int) -> bool:
    from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto
    from telethon.errors import FloodWaitError

    print()
    info(f"[{index}/{total}] {url}")

    chat_id, msg_id = parse_telegram_url(url)
    if chat_id is None:
        err(f"  无法解析链接格式，已跳过：{url}")
        return False

    try:
        message = await client.get_messages(chat_id, ids=msg_id)
    except FloodWaitError as e:
        warn(f"  触发频率限制，需等待 {e.seconds} 秒……")
        await asyncio.sleep(e.seconds)
        message = await client.get_messages(chat_id, ids=msg_id)
    except Exception as e:
        err(f"  获取消息失败：{e}")
        return False

    if message is None:
        err("  消息不存在，或你没有访问权限。")
        return False

    if not message.media:
        warn("  该消息没有附带媒体文件，已跳过。")
        return False

    # 确定文件名
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if isinstance(message.media, MessageMediaDocument):
        doc  = message.media.document
        mime = doc.mime_type if doc else ""
        if not mime.startswith("video/"):
            warn(f"  该消息附件不是视频（类型：{mime}），已跳过。")
            return False

        # 尝试从属性中拿原始文件名
        orig_name = None
        for attr in doc.attributes:
            if hasattr(attr, "file_name") and attr.file_name:
                orig_name = attr.file_name
                break

        if orig_name:
            stem = Path(orig_name).stem
            filename = f"{stem}.mp4"
        else:
            filename = f"telegram_{chat_id}_{msg_id}.mp4"

    elif isinstance(message.media, MessageMediaPhoto):
        warn("  该消息是图片，不是视频，已跳过。")
        return False
    else:
        warn("  未知媒体类型，已跳过。")
        return False

    out_path = OUTPUT_DIR / filename
    # 避免重名
    if out_path.exists():
        stem = out_path.stem
        out_path = OUTPUT_DIR / f"{stem}_{msg_id}.mp4"

    info(f"  保存为：{out_path.name}")

    try:
        await client.download_media(
            message,
            file=str(out_path),
            progress_callback=make_progress_callback(filename),
        )
        ok(f"  下载成功！→ {out_path}")
        return True
    except FloodWaitError as e:
        warn(f"  触发频率限制，需等待 {e.seconds} 秒……")
        await asyncio.sleep(e.seconds)
        err("  等待后仍失败，请稍后重试。")
        return False
    except Exception as e:
        err(f"  下载失败：{e}")
        return False


# ── 主程序（异步） ────────────────────────────────────────────
async def async_main():
    from telethon import TelegramClient

    api_id, api_hash = load_config()
    urls = load_urls(sys.argv[1:])
    total = len(urls)

    print(f"\n{'='*56}")
    print(f"  {BOLD}Telegram 视频批量下载工具{RESET}")
    print(f"  保存位置：{OUTPUT_DIR}")
    print(f"  共 {total} 个链接")
    print(f"{'='*56}")

    info("正在连接 Telegram……")
    async with TelegramClient(str(SESSION_FILE), api_id, api_hash) as client:
        if not await client.is_user_authorized():
            print()
            warn("首次运行，需要登录 Telegram 账号。")
            phone = input("请输入你的手机号（含国家代码，如 +8613800138000）：").strip()
            await client.send_code_request(phone)
            code = input("请输入 Telegram App 收到的验证码：").strip()
            try:
                await client.sign_in(phone, code)
            except Exception:
                password = input("检测到两步验证，请输入密码：")
                await client.sign_in(password=password)
            ok("登录成功！Session 已保存，下次无需再登录。")

        me = await client.get_me()
        ok(f"已登录：{me.first_name}（@{me.username}）")

        success, failed = 0, []

        for i, url in enumerate(urls, 1):
            if await download_one(client, url, i, total):
                success += 1
            else:
                failed.append(url)

    print(f"\n{'='*56}")
    print(f"  下载完成：{GREEN}{success}{RESET}/{total} 成功")
    if failed:
        print(f"  以下链接失败：")
        for u in failed:
            print(f"    {RED}✘ {u}{RESET}")
    print(f"  文件保存在：{OUTPUT_DIR}")
    print(f"{'='*56}\n")


def main():
    check_dependencies()
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print()
        warn("已手动取消。")
        sys.exit(0)


if __name__ == "__main__":
    main()

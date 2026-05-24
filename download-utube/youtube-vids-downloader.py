#!/usr/bin/env python3
"""
YouTube 视频批量下载工具
用法：
  python scripts/youtube-vids-downloader.py            # 从 urls.txt 批量下载
  python scripts/youtube-vids-downloader.py <URL>      # 下载单个链接
"""

import sys
import subprocess
import shutil
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
COOKIES_FILE = PROJECT_ROOT / "inputs" / "cookies.txt"
URLS_FILE    = PROJECT_ROOT / "inputs" / "urls.txt"
OUTPUT_DIR   = Path.home() / "Downloads" / "youtube-videos"

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
RESET  = "\033[0m"

def ok(msg):    print(f"{GREEN}✔ {msg}{RESET}")
def warn(msg):  print(f"{YELLOW}⚠ {msg}{RESET}")
def err(msg):   print(f"{RED}✘ {msg}{RESET}")
def info(msg):  print(f"{CYAN}→ {msg}{RESET}")


# ── 环境检查 ──────────────────────────────────────────────────
def check_yt_dlp() -> bool:
    if shutil.which("yt-dlp"):
        return True
    try:
        subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, check=True
        )
        return True
    except Exception:
        return False


def get_yt_dlp_cmd() -> list[str]:
    """返回可用的 yt-dlp 调用方式。"""
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


# ── 读取 URL 列表 ─────────────────────────────────────────────
def load_urls(cli_args: list[str]) -> list[str]:
    if cli_args:
        return [u.strip() for u in cli_args if u.strip()]

    if not URLS_FILE.exists():
        err(f"找不到 urls.txt（路径：{URLS_FILE}）")
        err("请创建 urls.txt，每行一个 YouTube 视频链接，或直接传入 URL 参数。")
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


# ── 下载单个视频 ──────────────────────────────────────────────
def download(url: str, index: int, total: int) -> bool:
    print()
    info(f"[{index}/{total}] 正在下载：{url}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = get_yt_dlp_cmd() + [
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", str(OUTPUT_DIR / "%(uploader)s - %(title).60s.%(ext)s"),
        "--no-playlist",
        "--retries", "3",
        "--fragment-retries", "3",
        "--console-title",
        "--progress",
        "--js-runtimes", "node",
    ]

    if COOKIES_FILE.exists():
        cmd += ["--cookies", str(COOKIES_FILE)]
        info("已加载 cookies.txt（已登录模式）。")
    else:
        warn("未找到 cookies.txt，将以未登录状态尝试下载（会员/私密视频可能失败）。")

    cmd.append(url)

    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode == 0:
            ok("下载成功！")
            return True
        else:
            err(f"下载失败（返回码 {result.returncode}）。")
            return False
    except KeyboardInterrupt:
        print()
        warn("已手动取消。")
        sys.exit(0)
    except Exception as e:
        err(f"发生错误：{e}")
        return False


# ── 主程序 ────────────────────────────────────────────────────
def main():
    print(f"\n{'='*52}")
    print(f"  YouTube 视频批量下载工具")
    print(f"  保存位置：{OUTPUT_DIR}")
    print(f"{'='*52}")

    if not check_yt_dlp():
        err("未找到 yt-dlp，请先安装：pip install yt-dlp")
        sys.exit(1)

    urls = load_urls(sys.argv[1:])
    total = len(urls)
    info(f"共找到 {total} 个链接，开始下载……\n")

    success, failed = 0, []

    for i, url in enumerate(urls, 1):
        if download(url, i, total):
            success += 1
        else:
            failed.append(url)

    # 汇总报告
    print(f"\n{'='*52}")
    print(f"  下载完成：{success}/{total} 成功")
    if failed:
        print(f"  以下链接失败：")
        for u in failed:
            print(f"    {RED}✘ {u}{RESET}")
    print(f"  文件保存在：{OUTPUT_DIR}")
    print(f"{'='*52}\n")


if __name__ == "__main__":
    main()

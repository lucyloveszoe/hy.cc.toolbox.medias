"""
gui_viewer.py
-------------
相似图片 / 视频查看器 GUI（跨平台 macOS / Windows）。

相似度策略
──────────
图片（两步）
  Step-1 [SAME]    : 文件大小 + 图片尺寸（像素宽高）完全相同 → 视为"完全相同"
  Step-2 [SIMILAR] : pHash + wHash 双哈希距离均 ≤ 阈值 → 视为"内容相似"

视频（两步）
  Step-1 [SAME]    : 文件大小 + 视频分辨率 + 时长（秒，精度 1s）完全相同 → 视为"完全相同"
  Step-2 [SIMILAR] : 前 N 秒均匀取 K 帧，任意一帧对 pHash+wHash 均 ≤ 阈值 → 视为"内容相似"

GUI 功能
──────────
  • 图片 Tab：独立扫描控制（开始 / 取消 / 清除）+ 相似度阈值
  • 视频 Tab：独立扫描控制（开始 / 取消 / 清除）+ 相似度阈值 + 采样帧数 + 采样时长
  • 取消扫描不丢弃已完成的部分结果；清除才重置历史
  • 每次「开始扫描」会先清除上一次结果再重新扫描
  • 左侧分组树：[SAME]/[SIMILAR] 标签 + 文件数 + 总大小
  • 右侧预览区：并排展示缩略图、完整路径、大小、修改时间
  • 可勾选 → 移入回收站 / 永久删除
  • 保留最新、保留最大、全选/取消快捷操作
  • 第三个 Tab：视频帧预览（对任意视频抽帧浏览）

用法：
    python gui_viewer.py [源目录]    # 不填则启动后弹窗选择目录
"""

import sys
import os
import csv
import json
import platform
import threading
import subprocess
import tempfile
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import Image, ImageTk
    import imagehash
except ImportError as e:
    print(f"[错误] 缺少依赖: {e}\n请运行: pip install Pillow imagehash send2trash")
    sys.exit(1)

try:
    from send2trash import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False

# ── 配置 ──────────────────────────────────────────────────────────────────────
HASH_THRESHOLD     = 15
THUMB_SIZE         = (200, 200)
VIDEO_PREVIEW_SIZE = (256, 256)
VIDEO_SAMPLE_SECS  = 300.0
VIDEO_SAMPLE_N     = 10
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".m4v", ".webm"}

IS_MAC     = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

BG_DARK   = "#1e1e2e"
BG_MID    = "#2a2a3e"
BG_CARD   = "#313149"
FG_TEXT   = "#cdd6f4"
FG_DIM    = "#7f849c"
ACCENT    = "#89b4fa"
RED       = "#f38ba8"
YELLOW    = "#f9e2af"
FONT_MAIN  = ("Segoe UI", 10) if IS_WINDOWS else ("SF Pro Text", 10)
FONT_BOLD  = ("Segoe UI", 10, "bold") if IS_WINDOWS else ("SF Pro Text", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)  if IS_WINDOWS else ("SF Pro Text", 9)
FONT_TITLE = ("Segoe UI", 13, "bold") if IS_WINDOWS else ("SF Pro Text", 13, "bold")


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_file(path: Path):
    try:
        if IS_MAC:
            subprocess.Popen(["open", str(path)])
        elif IS_WINDOWS:
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        messagebox.showerror("打开失败", str(e))


def recycle_file(path: Path):
    if HAS_SEND2TRASH:
        send2trash(str(path))
    else:
        path.unlink()


def get_image_meta(path: Path) -> tuple | None:
    try:
        sz = path.stat().st_size
        with Image.open(path) as img:
            w, h = img.size
        return (sz, w, h)
    except Exception:
        return None


def get_image_hashes(path: Path) -> tuple | None:
    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
        return (imagehash.phash(rgb), imagehash.whash(rgb))
    except Exception:
        return None


def get_video_meta(path: Path) -> tuple | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        sz = path.stat().st_size
        w = h = dur = None
        rotate = 0
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                w      = s.get("width")
                h      = s.get("height")
                dur    = float(s.get("duration", 0) or 0)
                rotate = int(s.get("tags", {}).get("rotate", 0) or 0)
                break
        if dur is None:
            dur = float(data.get("format", {}).get("duration", 0) or 0)
        if w and h:
            if rotate in (90, 270):
                w, h = h, w
            return (sz, w, h, round(dur))
        return None
    except Exception:
        return None


def extract_video_frames(video_path: Path,
                          n_frames: int = VIDEO_SAMPLE_N,
                          max_seconds: float = VIDEO_SAMPLE_SECS,
                          _last_error: list | None = None) -> list:
    frames = []
    tmp_paths = []
    last_err = _last_error if isinstance(_last_error, list) else None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(video_path)],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0 and last_err is not None:
            last_err.append((r.stderr or r.stdout or "ffprobe 失败")[:500])
        duration = 0.0
        if r.returncode == 0:
            data = json.loads(r.stdout)
            for s in data.get("streams", []):
                if s.get("codec_type") == "video":
                    duration = float(s.get("duration", 0) or 0)
                    break
            if duration <= 0:
                duration = float(data.get("format", {}).get("duration", 0) or 0)

        sample_end = min(duration, max_seconds) if duration > 0 else min(1.0, max_seconds)
        if sample_end <= 0:
            sample_end = 1.0
        if n_frames == 1:
            timestamps = [min(0.5, sample_end * 0.5)]
        else:
            step = sample_end / n_frames
            timestamps = [step * i + step * 0.5 for i in range(n_frames)]

        def run_ffmpeg(use_autorotate: bool) -> list:
            out_frames = []
            last_stderr = None
            for ts in timestamps:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp_path = tmp.name
                tmp_paths.append(tmp_path)
                cmd = ["ffmpeg", "-y"]
                if use_autorotate:
                    cmd.extend(["-autorotate", "1"])
                cmd.extend([
                    "-ss", f"{ts:.2f}",
                    "-i", str(video_path),
                    "-frames:v", "1",
                    "-vf", "scale=iw:ih",
                    tmp_path,
                ])
                r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                if r2.returncode == 0 and Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 0:
                    img = Image.open(tmp_path).convert("RGB")
                    img.load()
                    out_frames.append(img)
                elif r2.stderr:
                    last_stderr = (r2.stderr or "").strip()[:400]
            if last_err is not None and not out_frames and last_stderr:
                last_err.clear()
                last_err.append(last_stderr)
            return out_frames

        frames = run_ffmpeg(use_autorotate=True)
        if not frames and timestamps:
            frames = run_ffmpeg(use_autorotate=False)
    except Exception as e:
        if last_err is not None:
            last_err.clear()
            last_err.append(str(e)[:500])
    finally:
        for p in tmp_paths:
            try:
                if Path(p).exists():
                    os.unlink(p)
            except Exception:
                pass
    return frames


def extract_first_frame(video_path: Path) -> "Image.Image | None":
    frames = extract_video_frames(video_path, n_frames=1, max_seconds=5)
    return frames[0] if frames else None


def get_video_hashes(path: Path,
                     n_frames: int = VIDEO_SAMPLE_N,
                     max_seconds: float = VIDEO_SAMPLE_SECS) -> list | None:
    frames = extract_video_frames(path, n_frames=n_frames, max_seconds=max_seconds)
    if not frames:
        return None
    result = []
    for img in frames:
        try:
            result.append((imagehash.phash(img), imagehash.whash(img)))
        except Exception:
            pass
    return result if result else None


def make_thumbnail(path: Path) -> "ImageTk.PhotoImage | None":
    try:
        if path.suffix.lower() in VIDEO_EXTS:
            img = extract_first_frame(path)
        else:
            img = Image.open(path).convert("RGB")
        if img is None:
            return None
        img.thumbnail(THUMB_SIZE, Image.LANCZOS)
        padded = Image.new("RGB", THUMB_SIZE, (40, 40, 60))
        offset = ((THUMB_SIZE[0] - img.width) // 2, (THUMB_SIZE[1] - img.height) // 2)
        padded.paste(img, offset)
        return ImageTk.PhotoImage(padded)
    except Exception:
        return None


# ── 分组逻辑 ──────────────────────────────────────────────────────────────────

class Group:
    __slots__ = ("files", "label")

    def __init__(self, files: list[Path], label: str):
        self.files = files
        self.label = label


def collect_files(sources) -> tuple[list[Path], list[Path]]:
    if isinstance(sources, Path):
        sources = [sources]
    images, videos = [], []
    for source in sources:
        for f in source.rglob("*"):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            if ext in IMAGE_EXTS:
                images.append(f)
            elif ext in VIDEO_EXTS:
                videos.append(f)
    return sorted(set(images)), sorted(set(videos))


def _make_union_find(n: int):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    return parent, find, union


def _groups_from_uf(files: list[Path], find, total: int, same_pairs: set) -> list:
    raw: dict[int, list[int]] = defaultdict(list)
    for i in range(total):
        raw[find(i)].append(i)
    groups = []
    for idxs in raw.values():
        if len(idxs) < 2:
            continue
        fs = [files[i] for i in idxs]
        pairs_in_group = {(min(a, b), max(a, b)) for a in idxs for b in idxs if a != b}
        label = "SAME" if pairs_in_group.issubset(same_pairs) else "SIMILAR"
        groups.append(Group(fs, label))
    return groups


def build_image_groups(images: list[Path],
                       progress_cb=None,
                       cancel_event=None,
                       threshold: int = HASH_THRESHOLD) -> list:
    """
    图片两步分组。若 cancel_event 触发则返回当前已完成的部分结果。
    """
    total = len(images)
    if total == 0:
        return []

    parent, find, union = _make_union_find(total)
    same_pairs: set = set()

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    # Step 1: meta → SAME groups
    meta_map: dict[tuple, list[int]] = defaultdict(list)
    for i, f in enumerate(images):
        if _cancelled():
            break
        m = get_image_meta(f)
        if m:
            meta_map[m].append(i)
        if progress_cb:
            progress_cb("image_meta", i + 1, total, f, "读取图片元数据…")
    for idxs in meta_map.values():
        if len(idxs) >= 2:
            for k in range(1, len(idxs)):
                a, b = idxs[0], idxs[k]
                union(a, b)
                same_pairs.add((min(a, b), max(a, b)))

    # Step 2a: compute hashes
    hashes: list[tuple | None] = [None] * total
    if not _cancelled():
        for i, f in enumerate(images):
            if _cancelled():
                break
            h = get_image_hashes(f)
            if h is not None:
                hashes[i] = h
            if progress_cb:
                progress_cb("image_hash", i + 1, total, f, "计算图片哈希…")

    # Step 2b: pairwise comparison
    if not _cancelled():
        valid = [(i, hashes[i]) for i in range(total) if hashes[i] is not None]
        n = len(valid)
        total_pairs = n * (n - 1) // 2
        done = 0
        for a in range(n):
            if _cancelled():
                break
            ia, (ph_a, wh_a) = valid[a]
            for b in range(a + 1, n):
                ib, (ph_b, wh_b) = valid[b]
                if (ph_a - ph_b) <= threshold and (wh_a - wh_b) <= threshold:
                    union(ia, ib)
                done += 1
                if progress_cb and done % 10000 == 0:
                    progress_cb("image_compare", done, total_pairs, None, "比对图片相似度…")

    return _groups_from_uf(images, find, total, same_pairs)


def build_video_groups(videos: list[Path],
                       progress_cb=None,
                       cancel_event=None,
                       threshold: int = HASH_THRESHOLD,
                       sample_n: int = VIDEO_SAMPLE_N,
                       sample_secs: float = VIDEO_SAMPLE_SECS) -> list:
    """
    视频两步分组。若 cancel_event 触发则返回当前已完成的部分结果。
    """
    total = len(videos)
    if total == 0:
        return []

    parent, find, union = _make_union_find(total)
    same_pairs: set = set()

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    # Step 1: meta → SAME groups
    meta_map: dict[tuple, list[int]] = defaultdict(list)
    for i, f in enumerate(videos):
        if _cancelled():
            break
        m = get_video_meta(f)
        if m:
            meta_map[m].append(i)
        if progress_cb:
            progress_cb("video_meta", i + 1, total, f, "读取视频元数据…")
    for idxs in meta_map.values():
        if len(idxs) >= 2:
            for k in range(1, len(idxs)):
                a, b = idxs[0], idxs[k]
                union(a, b)
                same_pairs.add((min(a, b), max(a, b)))

    # Step 2a: compute frame hashes
    hashes: list[list | None] = []
    valid_idx: list[int] = []
    if not _cancelled():
        for i, f in enumerate(videos):
            if _cancelled():
                break
            h = get_video_hashes(f, n_frames=sample_n, max_seconds=sample_secs)
            if h is not None:
                hashes.append(h)
                valid_idx.append(i)
            if progress_cb:
                progress_cb("video_hash", i + 1, total, f,
                            f"计算视频多帧哈希（前{int(sample_secs)}s）…")

    # Step 2b: pairwise comparison
    if not _cancelled():
        n = len(valid_idx)
        total_pairs = n * (n - 1) // 2
        done = 0
        for a in range(n):
            if _cancelled():
                break
            ia = valid_idx[a]
            for b in range(a + 1, n):
                ib = valid_idx[b]
                if _videos_similar(hashes[a], hashes[b], threshold):
                    union(ia, ib)
                done += 1
                if progress_cb and done % 50 == 0:
                    progress_cb("video_compare", done, total_pairs, None, "比对视频相似度…")

    return _groups_from_uf(videos, find, total, same_pairs)


def _videos_similar(hashes_a: list, hashes_b: list, threshold: int) -> bool:
    for ph_a, wh_a in hashes_a:
        for ph_b, wh_b in hashes_b:
            if (ph_a - ph_b) <= threshold and (wh_a - wh_b) <= threshold:
                return True
    return False


# ── Tab 面板（图片 or 视频） ───────────────────────────────────────────────────

class MediaTab(tk.Frame):
    """
    独立的图片或视频扫描 Tab，内含：
      - 顶部控制栏：开始 / 取消 / 清除 + 该媒体类型专属参数
      - 左侧分组树
      - 右侧预览区
    取消扫描保留已找到的部分结果；开始扫描会先清除上次结果。
    """

    def __init__(self, master, media_type: str, app: "App"):
        super().__init__(master, bg=BG_DARK)
        self.media_type  = media_type   # "photo" | "video"
        self.app         = app
        self.groups: list[Group] = []
        self._all_files: list[Path] = []
        self._thumb_refs: list = []
        self._check_vars: dict[str, tk.BooleanVar] = {}
        self._cancel_event = threading.Event()
        self._scanning = False
        self._build_layout()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_layout(self):
        self._build_control_bar()

        paned = tk.PanedWindow(self, orient="horizontal", bg=BG_DARK,
                               sashwidth=5, sashrelief="flat")
        paned.pack(fill="both", expand=True)

        # Left: group tree
        left = tk.Frame(paned, bg=BG_MID, width=320)
        paned.add(left, minsize=220)

        label_text = "图片相似组" if self.media_type == "photo" else "视频相似组"
        tk.Label(left, text=label_text,
                 font=FONT_BOLD, bg=BG_MID, fg=FG_TEXT, pady=6).pack(fill="x")

        tree_frame = tk.Frame(left, bg=BG_MID)
        tree_frame.pack(fill="both", expand=True)

        style_name = "DarkPhoto.Treeview" if self.media_type == "photo" else "DarkVideo.Treeview"
        style = ttk.Style()
        style.configure(style_name,
                        background=BG_MID, foreground=FG_TEXT,
                        fieldbackground=BG_MID, rowheight=26, font=FONT_SMALL)
        style.configure(f"{style_name}.Heading",
                        background=BG_CARD, foreground=ACCENT, font=FONT_BOLD)
        style.map(style_name,
                  background=[("selected", BG_CARD)],
                  foreground=[("selected", ACCENT)])

        self.tree = ttk.Treeview(tree_frame, style=style_name,
                                  columns=("tag", "files", "size"),
                                  show="tree headings")
        self.tree.heading("#0",    text="分组")
        self.tree.heading("tag",   text="类型")
        self.tree.heading("files", text="数量")
        self.tree.heading("size",  text="大小")
        self.tree.column("#0",    width=90,  stretch=True)
        self.tree.column("tag",   width=70,  anchor="center")
        self.tree.column("files", width=50,  anchor="center")
        self.tree.column("size",  width=80,  anchor="e")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_group_select)

        # Right: preview canvas
        right = tk.Frame(paned, bg=BG_DARK)
        paned.add(right, minsize=600)

        self.preview_canvas = tk.Canvas(right, bg=BG_DARK, highlightthickness=0)
        preview_vsb = ttk.Scrollbar(right, orient="vertical",
                                     command=self.preview_canvas.yview)
        self.preview_canvas.configure(yscrollcommand=preview_vsb.set)
        preview_vsb.pack(side="right", fill="y")
        self.preview_canvas.pack(side="left", fill="both", expand=True)

        self.preview_frame = tk.Frame(self.preview_canvas, bg=BG_DARK)
        self._preview_win = self.preview_canvas.create_window(
            (0, 0), window=self.preview_frame, anchor="nw")
        self.preview_frame.bind("<Configure>", self._on_preview_configure)
        self.preview_canvas.bind("<Configure>", self._on_canvas_configure)
        self.preview_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.preview_canvas.bind_all("<Button-4>",   self._on_trackpad_up)
        self.preview_canvas.bind_all("<Button-5>",   self._on_trackpad_down)

    def _build_control_bar(self):
        bar = tk.Frame(self, bg=BG_MID, pady=5, padx=8)
        bar.pack(fill="x", side="top")

        # Action buttons
        self._start_btn = tk.Button(
            bar, text="▶ 开始扫描", font=FONT_MAIN,
            bg=ACCENT, fg=BG_DARK, relief="flat", padx=10,
            command=self._on_start_click)
        self._start_btn.pack(side="left", padx=(0, 3))

        self._cancel_btn = tk.Button(
            bar, text="⏹ 取消", font=FONT_MAIN,
            bg="#4a1e1e", fg=RED, relief="flat", padx=10,
            state="disabled", command=self._on_cancel_click)
        self._cancel_btn.pack(side="left", padx=3)

        self._clear_btn = tk.Button(
            bar, text="✕ 清除结果", font=FONT_MAIN,
            bg=BG_CARD, fg=FG_DIM, relief="flat", padx=10,
            command=self._on_clear_click)
        self._clear_btn.pack(side="left", padx=3)

        # Divider
        tk.Frame(bar, bg=FG_DIM, width=1).pack(side="left", fill="y", padx=10, pady=3)

        # Similarity threshold (both photo and video)
        tk.Label(bar, text="相似阈值:", font=FONT_SMALL,
                 bg=BG_MID, fg=FG_DIM).pack(side="left", padx=(0, 2))
        self._threshold_var = tk.IntVar(value=HASH_THRESHOLD)
        tk.Label(bar, textvariable=self._threshold_var, font=FONT_BOLD,
                 bg=BG_MID, fg=ACCENT, width=3).pack(side="left")
        tk.Scale(bar, from_=1, to=30, orient="horizontal",
                 variable=self._threshold_var,
                 bg=BG_MID, fg=FG_TEXT, troughcolor=BG_CARD,
                 highlightthickness=0, showvalue=False, length=100,
                 command=lambda _: None).pack(side="left", padx=2)
        tk.Button(bar, text="Apply", font=FONT_SMALL,
                  bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=6,
                  command=self._apply_threshold).pack(side="left", padx=(0, 4))

        # Video-only parameters
        if self.media_type == "video":
            tk.Frame(bar, bg=FG_DIM, width=1).pack(side="left", fill="y", padx=10, pady=3)

            tk.Label(bar, text="采样帧数:", font=FONT_SMALL,
                     bg=BG_MID, fg=FG_DIM).pack(side="left", padx=(0, 2))
            self._sample_n_var = tk.IntVar(value=VIDEO_SAMPLE_N)
            tk.Label(bar, textvariable=self._sample_n_var, font=FONT_BOLD,
                     bg=BG_MID, fg=ACCENT, width=3).pack(side="left")
            tk.Scale(bar, from_=1, to=30, orient="horizontal",
                     variable=self._sample_n_var,
                     bg=BG_MID, fg=FG_TEXT, troughcolor=BG_CARD,
                     highlightthickness=0, showvalue=False, length=80,
                     command=lambda _: None).pack(side="left", padx=2)

            tk.Label(bar, text="时长(s):", font=FONT_SMALL,
                     bg=BG_MID, fg=FG_DIM).pack(side="left", padx=(8, 2))
            self._sample_secs_var = tk.IntVar(value=int(VIDEO_SAMPLE_SECS))
            tk.Label(bar, textvariable=self._sample_secs_var, font=FONT_BOLD,
                     bg=BG_MID, fg=ACCENT, width=4).pack(side="left")
            tk.Scale(bar, from_=30, to=600, orient="horizontal",
                     variable=self._sample_secs_var, resolution=30,
                     bg=BG_MID, fg=FG_TEXT, troughcolor=BG_CARD,
                     highlightthickness=0, showvalue=False, length=80,
                     command=lambda _: None).pack(side="left", padx=2)

        # Right side: progress bar + status label
        self._progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(bar, variable=self._progress_var,
                        maximum=100, length=160).pack(side="right", padx=(0, 4))
        self._status_var = tk.StringVar(value="就绪 — 请选择目录后开始扫描")
        tk.Label(bar, textvariable=self._status_var, font=FONT_SMALL,
                 bg=BG_MID, fg=FG_DIM, anchor="w").pack(side="right", padx=(4, 8))

    # ── Scan control ──────────────────────────────────────────────────────────

    def start_scan(self, source_dirs: list[Path]):
        if self._scanning:
            return
        valid = [d for d in source_dirs if d.exists()]
        if not valid:
            return
        # Always clear previous results before a new scan
        self._clear_results()
        self._cancel_event.clear()
        self._scanning = True
        self._update_btn_states()
        self._status_var.set("正在收集文件…")
        self._progress_var.set(0)
        threading.Thread(target=self._scan_thread, args=(valid,), daemon=True).start()

    def _on_start_click(self):
        valid = [d for d in self.app.source_dirs if d.exists()]
        if not valid:
            messagebox.showwarning("提示", "请先在工具栏选择一个有效目录。")
            return
        self.start_scan(valid)

    def _on_cancel_click(self):
        if not self._scanning:
            return
        self._cancel_event.set()
        self._status_var.set("正在取消…（已找到的结果将保留）")
        self._cancel_btn.configure(state="disabled")

    def _on_clear_click(self):
        if self._scanning:
            return
        self._clear_results()
        self._status_var.set("就绪")
        self._progress_var.set(0)
        if self.media_type == "video":
            self.app.on_video_scan_complete([])

    def _apply_threshold(self):
        valid = [d for d in self.app.source_dirs if d.exists()]
        if valid:
            self.start_scan(valid)
        else:
            messagebox.showwarning("提示", "请先选择一个有效目录。")

    def _update_btn_states(self):
        if self._scanning:
            self._start_btn.configure(state="disabled")
            self._cancel_btn.configure(state="normal")
            self._clear_btn.configure(state="disabled")
        else:
            self._start_btn.configure(state="normal")
            self._cancel_btn.configure(state="disabled")
            self._clear_btn.configure(state="normal")

    # ── Scan thread ───────────────────────────────────────────────────────────

    def _scan_thread(self, source_dirs: list[Path]):
        try:
            _last_update = [0.0]

            def progress_cb(kind, done, total, path, msg):
                now = time.monotonic()
                if now - _last_update[0] < 0.5 and done < total:
                    return
                _last_update[0] = now
                pct = done / total * 100 if total else 0
                desc = f"{msg} ({done}/{total})"
                self.after(0, lambda p=pct, m=desc: (
                    self._progress_var.set(p),
                    self._status_var.set(m),
                ))
                if path is not None:
                    print(f"[{kind}] {done}/{total} ({pct:.1f}%) - {path}", flush=True)

            threshold = self._threshold_var.get()

            if self.media_type == "photo":
                images, _ = collect_files(source_dirs)
                self.after(0, lambda n=len(images): self._status_var.set(
                    f"图片 {n} 张，正在分析…"))
                groups = build_image_groups(
                    images,
                    progress_cb=progress_cb,
                    cancel_event=self._cancel_event,
                    threshold=threshold)

            else:  # video
                _, videos = collect_files(source_dirs)
                self._all_files = list(videos)
                self.after(0, lambda n=len(videos): self._status_var.set(
                    f"视频 {n} 个，正在分析…"))
                sample_n    = self._sample_n_var.get()
                sample_secs = float(self._sample_secs_var.get())
                groups = build_video_groups(
                    videos,
                    progress_cb=progress_cb,
                    cancel_event=self._cancel_event,
                    threshold=threshold,
                    sample_n=sample_n,
                    sample_secs=sample_secs)

            groups.sort(key=lambda g: (0 if g.label == "SAME" else 1, -len(g.files)))
            cancelled = self._cancel_event.is_set()
            self.after(0, lambda g=groups, c=cancelled: self._on_scan_done(g, c))

        except Exception as e:
            self.after(0, lambda err=str(e): self._status_var.set(f"扫描出错: {err}"))
        finally:
            self._scanning = False
            self.after(0, self._update_btn_states)

    def _on_scan_done(self, groups: list[Group], cancelled: bool):
        self.populate(groups)
        same = sum(1 for g in groups if g.label == "SAME")
        sim  = len(groups) - same

        if cancelled:
            self._status_var.set(
                f"已取消（部分结果：{len(groups)} 组，{same} 相同 / {sim} 相似）")
            # keep progress bar where it was
        elif groups:
            self._status_var.set(f"完成：{len(groups)} 组（{same} 相同 / {sim} 相似）")
            self._progress_var.set(100)
        else:
            label = "图片" if self.media_type == "photo" else "视频"
            self._status_var.set(f"未发现相似{label}")
            self._progress_var.set(100)

        if self.media_type == "video":
            self.app.on_video_scan_complete(self._all_files)

    def _clear_results(self):
        self.groups = []
        self._all_files = []
        self._check_vars.clear()
        self._thumb_refs.clear()
        self._clear_tree()
        self._clear_preview()

    # ── Tree population ───────────────────────────────────────────────────────

    def populate(self, groups: list[Group]):
        self.groups = groups
        self._check_vars.clear()
        self._clear_tree()
        self._clear_preview()

        for i, grp in enumerate(groups, 1):
            total_bytes = sum(f.stat().st_size for f in grp.files if f.exists())
            self.tree.insert("", "end",
                             text=f"组 {i:03d}",
                             values=(grp.label, len(grp.files), fmt_size(total_bytes)),
                             tags=(grp.label,))
        self.tree.tag_configure("SAME",    foreground=YELLOW)
        self.tree.tag_configure("SIMILAR", foreground=ACCENT)

        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])

    # ── Preview ───────────────────────────────────────────────────────────────

    def _on_group_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx < len(self.groups):
            self._show_preview(self.groups[idx])

    def _show_preview(self, grp: Group):
        self._clear_preview()
        self._thumb_refs.clear()

        MAX_PREVIEW = 40
        all_files = sorted(grp.files,
                           key=lambda p: p.stat().st_size if p.exists() else 0,
                           reverse=True)
        files  = all_files[:MAX_PREVIEW]
        hidden = len(all_files) - len(files)

        canvas_w     = self.preview_canvas.winfo_width()
        card_w       = THUMB_SIZE[0] + 60
        cols_per_row = max(1, canvas_w // card_w) if canvas_w > 50 else 3

        tag_color = YELLOW if grp.label == "SAME" else ACCENT
        total_str = (f"（共 {len(all_files)} 个，显示前 {MAX_PREVIEW} 个）"
                     if hidden else f"（共 {len(all_files)} 个）")
        tag_text  = ("✦ 完全相同（文件大小 + 尺寸一致）" if grp.label == "SAME"
                     else "≈ 内容相似（视觉帧哈希匹配）")
        banner = tk.Label(self.preview_frame, text=f"{tag_text}  {total_str}",
                          font=FONT_BOLD, bg=BG_MID, fg=tag_color,
                          pady=4, anchor="w", padx=12)
        banner.grid(row=0, column=0, columnspan=cols_per_row,
                    sticky="ew", pady=(0, 6))

        for i, path in enumerate(files):
            grid_row = 1 + i // cols_per_row
            grid_col = i % cols_per_row

            card = tk.Frame(self.preview_frame, bg=BG_CARD,
                            relief="flat", bd=0, padx=8, pady=8)
            card.grid(row=grid_row, column=grid_col, padx=8, pady=6, sticky="n")

            key = str(path)
            if key not in self._check_vars:
                self._check_vars[key] = tk.BooleanVar(value=False)
            var = self._check_vars[key]
            tk.Checkbutton(card, variable=var, bg=BG_CARD,
                           activebackground=BG_CARD, selectcolor=BG_DARK,
                           fg=RED, text="标记删除", font=FONT_SMALL).pack(anchor="w")

            thumb_lbl = tk.Label(card, bg=BG_CARD, text="加载中…",
                                  fg=FG_DIM, font=FONT_SMALL,
                                  width=THUMB_SIZE[0] // 8,
                                  height=THUMB_SIZE[1] // 20)
            thumb_lbl.pack()
            threading.Thread(target=self._load_thumb,
                             args=(path, thumb_lbl), daemon=True).start()

            fname = path.name if len(path.name) <= 28 else path.name[:25] + "…"
            name_lbl = tk.Label(card, text=fname, font=FONT_BOLD,
                                fg=ACCENT, bg=BG_CARD, cursor="hand2")
            name_lbl.pack(pady=(4, 0))
            name_lbl.bind("<Button-1>", lambda e, p=path: open_file(p))

            dir_str = str(path.parent)
            if len(dir_str) > 40:
                dir_str = "…" + dir_str[-37:]
            tk.Label(card, text=dir_str, font=FONT_SMALL,
                     fg=FG_DIM, bg=BG_CARD, wraplength=215,
                     justify="left").pack(pady=(0, 2))

            try:
                st       = path.stat()
                size_str = fmt_size(st.st_size)
                mtime    = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                size_str = mtime = "N/A"

            dim_str = ""
            if path.suffix.lower() in IMAGE_EXTS:
                m = get_image_meta(path)
                if m:
                    dim_str = f"  {m[1]}×{m[2]}"
            elif path.suffix.lower() in VIDEO_EXTS:
                m = get_video_meta(path)
                if m:
                    dim_str = f"  {m[1]}×{m[2]}  {m[3]}s"

            tk.Label(card, text=f"大小: {size_str}{dim_str}",
                     font=FONT_SMALL, fg=FG_DIM, bg=BG_CARD).pack()
            tk.Label(card, text=f"修改: {mtime}",
                     font=FONT_SMALL, fg=FG_DIM, bg=BG_CARD).pack()

        # Action buttons row
        last_row = 1 + (len(files) - 1) // cols_per_row + 1
        ctrl = tk.Frame(self.preview_frame, bg=BG_DARK)
        ctrl.grid(row=last_row, column=0, columnspan=cols_per_row,
                  pady=8, sticky="w", padx=8)
        for text, cmd in [
            ("全选本组",           lambda g=grp: self._check_group(g, True)),
            ("取消全选",           lambda g=grp: self._check_group(g, False)),
            ("保留最新，其余勾选", lambda g=grp: self._keep_newest(g)),
            ("保留最大，其余勾选", lambda g=grp: self._keep_largest(g)),
        ]:
            tk.Button(ctrl, text=text, font=FONT_SMALL,
                      bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=8,
                      command=cmd).pack(side="left", padx=4)

        if hidden:
            tk.Label(
                self.preview_frame,
                text=f"⚠ 还有 {hidden} 个文件未显示（勾选操作仍对全组 {len(all_files)} 个文件生效）",
                font=FONT_SMALL, bg=BG_DARK, fg=YELLOW,
                pady=4, padx=12, anchor="w",
            ).grid(row=last_row + 1, column=0, columnspan=cols_per_row,
                   sticky="ew", pady=(0, 8))

    def _load_thumb(self, path: Path, label: tk.Label):
        photo = make_thumbnail(path)
        if photo:
            self._thumb_refs.append(photo)
            self.after(0, lambda lbl=label, ph=photo:
                       lbl.configure(image=ph, text="", width=0, height=0))
        else:
            self.after(0, lambda lbl=label: lbl.configure(text="无法预览"))

    # ── Selection helpers ─────────────────────────────────────────────────────

    def _check_group(self, grp: Group, checked: bool):
        for p in grp.files:
            if str(p) in self._check_vars:
                self._check_vars[str(p)].set(checked)

    def _keep_newest(self, grp: Group):
        existing = [p for p in grp.files if p.exists()]
        if not existing:
            return
        keep = max(existing, key=lambda p: p.stat().st_mtime)
        for p in existing:
            if str(p) in self._check_vars:
                self._check_vars[str(p)].set(p != keep)

    def _keep_largest(self, grp: Group):
        existing = [p for p in grp.files if p.exists()]
        if not existing:
            return
        keep = max(existing, key=lambda p: p.stat().st_size)
        for p in existing:
            if str(p) in self._check_vars:
                self._check_vars[str(p)].set(p != keep)

    def get_checked(self) -> list[Path]:
        return [Path(k) for k, v in self._check_vars.items()
                if v.get() and Path(k).exists()]

    def delete_checked(self, permanent: bool):
        targets = self.get_checked()
        if not targets:
            messagebox.showinfo("提示", "请先勾选要删除的文件。")
            return False

        action  = "永久删除" if permanent else "移入回收站"
        preview = "\n".join(str(t) for t in targets[:10])
        if len(targets) > 10:
            preview += f"\n…还有 {len(targets)-10} 个"
        if not messagebox.askyesno("确认", f"确定{action} {len(targets)} 个文件？\n\n{preview}"):
            return False

        errors = []
        for p in targets:
            try:
                if permanent:
                    p.unlink()
                else:
                    recycle_file(p)
                self._check_vars.pop(str(p), None)
            except Exception as e:
                errors.append(f"{p.name}: {e}")

        if errors:
            messagebox.showerror("部分失败", "\n".join(errors))
        else:
            messagebox.showinfo("完成", f"已{action} {len(targets)} 个文件。")
        return True

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _clear_preview(self):
        for w in self.preview_frame.winfo_children():
            w.destroy()

    def _on_preview_configure(self, _event):
        self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.preview_canvas.itemconfig(self._preview_win, width=event.width)

    def _on_mousewheel(self, event):
        self.preview_canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _on_trackpad_up(self, _event):
        self.preview_canvas.yview_scroll(-1, "units")

    def _on_trackpad_down(self, _event):
        self.preview_canvas.yview_scroll(1, "units")


# ── 第三个 Tab：视频帧预览 ─────────────────────────────────────────────────────

class VideoPreviewTab(tk.Frame):
    """
    Tab：列出所有视频文件，右侧展示选中视频的多帧预览图。
    """

    def __init__(self, master, app: "App"):
        super().__init__(master, bg=BG_DARK)
        self.app = app
        self.videos: list[Path] = []
        self._thumb_refs: list = []
        self._check_vars: dict[str, tk.BooleanVar] = {}
        self._build_layout()

    def _build_layout(self):
        paned = tk.PanedWindow(self, orient="horizontal", bg=BG_DARK,
                               sashwidth=5, sashrelief="flat")
        paned.pack(fill="both", expand=True)

        # Left: video list
        left = tk.Frame(paned, bg=BG_MID, width=320)
        paned.add(left, minsize=220)

        hdr = tk.Frame(left, bg=BG_MID)
        hdr.pack(fill="x")
        tk.Label(hdr, text="视频列表",
                 font=FONT_BOLD, bg=BG_MID, fg=FG_TEXT, pady=6).pack(side="left", padx=8)
        tk.Button(hdr, text="✕ 清除", font=FONT_SMALL,
                  bg=BG_CARD, fg=FG_DIM, relief="flat", padx=6,
                  command=self.clear).pack(side="right", padx=8, pady=4)

        tree_frame = tk.Frame(left, bg=BG_MID)
        tree_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        style_name = "DarkPreview.Treeview"
        style.configure(style_name,
                        background=BG_MID, foreground=FG_TEXT,
                        fieldbackground=BG_MID, rowheight=24, font=FONT_SMALL)
        style.configure(f"{style_name}.Heading",
                        background=BG_CARD, foreground=ACCENT, font=FONT_BOLD)

        self.tree = ttk.Treeview(tree_frame, style=style_name,
                                 columns=("duration", "size"),
                                 show="tree headings", selectmode="browse")
        self.tree.heading("#0",       text="文件名")
        self.tree.heading("duration", text="时长")
        self.tree.heading("size",     text="大小")
        self.tree.column("#0",       width=160, stretch=True)
        self.tree.column("duration", width=70,  anchor="center")
        self.tree.column("size",     width=80,  anchor="e")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_video_select)

        # Right: frame preview
        right = tk.Frame(paned, bg=BG_DARK)
        paned.add(right, minsize=600)

        self.preview_canvas = tk.Canvas(right, bg=BG_DARK, highlightthickness=0)
        preview_vsb = ttk.Scrollbar(right, orient="vertical",
                                     command=self.preview_canvas.yview)
        self.preview_canvas.configure(yscrollcommand=preview_vsb.set)
        preview_vsb.pack(side="right", fill="y")
        self.preview_canvas.pack(side="left", fill="both", expand=True)

        self.preview_frame = tk.Frame(self.preview_canvas, bg=BG_DARK)
        self._preview_win = self.preview_canvas.create_window(
            (0, 0), window=self.preview_frame, anchor="nw")
        self.preview_frame.bind("<Configure>", self._on_preview_configure)
        self.preview_canvas.bind("<Configure>", self._on_canvas_configure)
        self.preview_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.preview_canvas.bind_all("<Button-4>",   self._on_trackpad_up)
        self.preview_canvas.bind_all("<Button-5>",   self._on_trackpad_down)

    # ── Public interface ──────────────────────────────────────────────────────

    def populate(self, videos: list[Path]):
        self.videos = [v for v in videos if v.exists()]
        self._check_vars.clear()
        self._clear_tree()
        self._clear_preview()

        for v in self.videos:
            try:
                size_str = fmt_size(v.stat().st_size)
            except Exception:
                size_str = "N/A"
            meta    = get_video_meta(v)
            dur_str = f"{meta[3]}s" if meta else "?"
            fname   = v.name if len(v.name) <= 32 else v.name[:29] + "…"
            self.tree.insert("", "end", iid=str(v),
                             text=fname, values=(dur_str, size_str))

        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
            self._on_video_select()

    def clear(self):
        self.videos = []
        self._check_vars.clear()
        self._clear_tree()
        self._clear_preview()

    # ── Events / preview ──────────────────────────────────────────────────────

    def _on_video_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        path = Path(sel[0])
        if path.exists():
            self._show_frames(path)

    def _show_frames(self, video_path: Path):
        self._clear_preview()
        self._thumb_refs.clear()

        key = str(video_path)
        if key not in self._check_vars:
            self._check_vars[key] = tk.BooleanVar(value=False)
        var = self._check_vars[key]

        header = tk.Frame(self.preview_frame, bg=BG_DARK)
        header.pack(fill="x", pady=(4, 6), padx=8)
        tk.Checkbutton(header, text="标记此视频删除", variable=var,
                       bg=BG_DARK, activebackground=BG_DARK,
                       selectcolor=BG_CARD, fg=RED,
                       font=FONT_SMALL).pack(side="left")
        tk.Label(header, text=str(video_path), font=FONT_SMALL,
                 fg=FG_DIM, bg=BG_DARK, wraplength=900,
                 justify="left").pack(side="left", padx=10)

        last_error = []
        frames = extract_video_frames(
            video_path,
            n_frames=VIDEO_SAMPLE_N,
            max_seconds=VIDEO_SAMPLE_SECS,
            _last_error=last_error,
        )

        if not frames:
            msg = "无法从该视频提取预览帧。"
            if last_error:
                msg += "\n\n" + last_error[0]
            tk.Label(self.preview_frame, text=msg, font=FONT_SMALL,
                     fg=FG_DIM, bg=BG_DARK, justify="left",
                     wraplength=700).pack(pady=20, padx=12)
            return

        row = tk.Frame(self.preview_frame, bg=BG_DARK)
        row.pack(padx=8, pady=4, anchor="w")

        for idx, img in enumerate(frames):
            try:
                img = img.copy()
                img.thumbnail(VIDEO_PREVIEW_SIZE, Image.LANCZOS)
                padded = Image.new("RGB", VIDEO_PREVIEW_SIZE, (40, 40, 60))
                offset = ((VIDEO_PREVIEW_SIZE[0] - img.width) // 2,
                          (VIDEO_PREVIEW_SIZE[1] - img.height) // 2)
                padded.paste(img, offset)
                ph = ImageTk.PhotoImage(padded)
                self._thumb_refs.append(ph)
            except Exception:
                continue
            card = tk.Frame(row, bg=BG_CARD, padx=6, pady=6)
            card.pack(side="left", padx=6, pady=4)
            tk.Label(card, image=ph, bg=BG_CARD).pack()
            tk.Label(card, text=f"帧 {idx + 1}", font=FONT_SMALL,
                     fg=FG_DIM, bg=BG_CARD).pack(pady=(4, 0))

        ctrl = tk.Frame(self.preview_frame, bg=BG_DARK)
        ctrl.pack(fill="x", pady=10, padx=8)
        for text, cmd in [
            ("全选",             self._check_all),
            ("取消全选",         lambda: self._check_all(False)),
            ("保留最新，其余勾选", self._keep_newest),
            ("保留最大，其余勾选", self._keep_largest),
        ]:
            tk.Button(ctrl, text=text, font=FONT_SMALL,
                      bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=8,
                      command=cmd).pack(side="left", padx=4)

    # ── Selection helpers ─────────────────────────────────────────────────────

    def _check_all(self, checked: bool = True):
        for v in self.videos:
            key = str(v)
            if key not in self._check_vars:
                self._check_vars[key] = tk.BooleanVar(value=False)
            self._check_vars[key].set(checked)

    def _keep_newest(self):
        existing = [v for v in self.videos if v.exists()]
        if not existing:
            return
        keep = max(existing, key=lambda p: p.stat().st_mtime)
        for v in existing:
            key = str(v)
            if key not in self._check_vars:
                self._check_vars[key] = tk.BooleanVar(value=False)
            self._check_vars[key].set(v != keep)

    def _keep_largest(self):
        existing = [v for v in self.videos if v.exists()]
        if not existing:
            return
        keep = max(existing, key=lambda p: p.stat().st_size)
        for v in existing:
            key = str(v)
            if key not in self._check_vars:
                self._check_vars[key] = tk.BooleanVar(value=False)
            self._check_vars[key].set(v != keep)

    def get_checked(self) -> list[Path]:
        return [Path(k) for k, v in self._check_vars.items()
                if v.get() and Path(k).exists()]

    def delete_checked(self, permanent: bool):
        targets = self.get_checked()
        if not targets:
            messagebox.showinfo("提示", "请先勾选要删除的视频。")
            return False

        action  = "永久删除" if permanent else "移入回收站"
        preview = "\n".join(str(t) for t in targets[:10])
        if len(targets) > 10:
            preview += f"\n…还有 {len(targets)-10} 个"
        if not messagebox.askyesno("确认", f"确定{action} {len(targets)} 个视频？\n\n{preview}"):
            return False

        errors = []
        for p in targets:
            try:
                if permanent:
                    p.unlink()
                else:
                    recycle_file(p)
                self._check_vars.pop(str(p), None)
            except Exception as e:
                errors.append(f"{p.name}: {e}")

        if errors:
            messagebox.showerror("部分失败", "\n".join(errors))
        else:
            messagebox.showinfo("完成", f"已{action} {len(targets)} 个视频。")
        return True

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _clear_preview(self):
        for w in self.preview_frame.winfo_children():
            w.destroy()

    def _on_preview_configure(self, _event):
        self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.preview_canvas.itemconfig(self._preview_win, width=event.width)

    def _on_mousewheel(self, event):
        self.preview_canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _on_trackpad_up(self, _event):
        self.preview_canvas.yview_scroll(-1, "units")

    def _on_trackpad_down(self, _event):
        self.preview_canvas.yview_scroll(1, "units")


# ── 主窗口 ────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self, source_dirs: "list[Path] | None"):
        super().__init__()
        self.source_dirs: list[Path] = source_dirs or []

        self.title("相似图片 / 视频查看器")
        self.geometry("1400x860")
        self.configure(bg=BG_DARK)
        self.resizable(True, True)

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Toolbar — directory, bulk scan, export, delete
        toolbar = tk.Frame(self, bg=BG_MID, pady=6, padx=10)
        toolbar.pack(fill="x", side="top")

        tk.Label(toolbar, text="相似图片 / 视频查看器",
                 font=FONT_TITLE, bg=BG_MID, fg=ACCENT).pack(side="left")

        tk.Button(toolbar, text="📂 选择目录", font=FONT_MAIN,
                  bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=10,
                  command=self._choose_dir).pack(side="left", padx=(20, 4))

        tk.Button(toolbar, text="📋 导出报告", font=FONT_MAIN,
                  bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=10,
                  command=self._export_report).pack(side="left", padx=4)

        dir_text = "; ".join(str(d) for d in self.source_dirs) if self.source_dirs else "未选择目录"
        self.dir_label = tk.Label(toolbar, text=dir_text,
                                   font=FONT_SMALL, bg=BG_MID, fg=FG_DIM)
        self.dir_label.pack(side="left", padx=12)

        tk.Button(toolbar, text="🗑 永久删除勾选", font=FONT_MAIN,
                  bg="#4a1e1e", fg=RED, relief="flat", padx=10,
                  command=lambda: self._delete_checked(permanent=True)
                  ).pack(side="right", padx=4)

        recycle_text = "♻ 移入回收站" if HAS_SEND2TRASH else "♻ 移入回收站（需 send2trash）"
        tk.Button(toolbar, text=recycle_text, font=FONT_MAIN,
                  bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=10,
                  command=lambda: self._delete_checked(permanent=False)
                  ).pack(side="right", padx=4)

        # Notebook
        nb_style = ttk.Style()
        nb_style.theme_use("clam")
        nb_style.configure("Dark.TNotebook",
                            background=BG_DARK, borderwidth=0)
        nb_style.configure("Dark.TNotebook.Tab",
                            background=BG_MID, foreground=FG_DIM,
                            padding=[14, 6], font=FONT_BOLD)
        nb_style.map("Dark.TNotebook.Tab",
                     background=[("selected", BG_CARD)],
                     foreground=[("selected", ACCENT)])

        self.notebook = ttk.Notebook(self, style="Dark.TNotebook")
        self.notebook.pack(fill="both", expand=True)

        self.img_tab     = MediaTab(self.notebook, "photo", self)
        self.vid_tab     = MediaTab(self.notebook, "video", self)
        self.preview_tab = VideoPreviewTab(self.notebook, self)
        self.notebook.add(self.img_tab,     text="  🖼  图片  ")
        self.notebook.add(self.vid_tab,     text="  🎬  视频  ")
        self.notebook.add(self.preview_tab, text="  📷  视频帧预览  ")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def on_video_scan_complete(self, videos: list[Path]):
        """Called by vid_tab when its scan finishes (or is cancelled/cleared)."""
        self.preview_tab.populate(videos)

    def _choose_dir(self):
        init = str(self.source_dirs[0]) if self.source_dirs else str(Path.home())
        d = filedialog.askdirectory(initialdir=init, title="选择要扫描的目录")
        if d:
            self.source_dirs = [Path(d)]
            self.dir_label.configure(text=str(self.source_dirs[0]))

    def _delete_checked(self, permanent: bool):
        idx = self.notebook.index(self.notebook.select())
        if idx == 0:
            self.img_tab.delete_checked(permanent)
        elif idx == 1:
            self.vid_tab.delete_checked(permanent)
        else:
            self.preview_tab.delete_checked(permanent)

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_report(self):
        img_groups = self.img_tab.groups
        vid_groups = self.vid_tab.groups
        if not img_groups and not vid_groups:
            messagebox.showinfo("提示", "暂无相似组数据，请先完成扫描。")
            return

        fmt = _ask_export_format(self)
        if not fmt:
            return

        ext          = ".csv" if fmt == "csv" else ".md"
        default_name = datetime.now().strftime(f"%Y-%m-%d-similarity-report{ext}")
        out_path     = filedialog.asksaveasfilename(
            title="保存报告",
            initialfile=default_name,
            defaultextension=ext,
            filetypes=[("CSV 文件", "*.csv"), ("Markdown 文件", "*.md"), ("所有文件", "*.*")],
        )
        if not out_path:
            return

        try:
            if fmt == "csv":
                _export_csv(Path(out_path), img_groups, vid_groups)
            else:
                _export_markdown(Path(out_path), img_groups, vid_groups)
            messagebox.showinfo("导出成功", f"报告已保存到：\n{out_path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))


# ── 导出辅助 ──────────────────────────────────────────────────────────────────

def _ask_export_format(parent) -> str | None:
    result = [None]
    dlg = tk.Toplevel(parent)
    dlg.title("选择导出格式")
    dlg.configure(bg=BG_DARK)
    dlg.resizable(False, False)
    dlg.grab_set()

    tk.Label(dlg, text="请选择导出格式：",
             font=FONT_BOLD, bg=BG_DARK, fg=FG_TEXT,
             pady=14, padx=20).pack()

    btn_frame = tk.Frame(dlg, bg=BG_DARK)
    btn_frame.pack(pady=(0, 16), padx=20)

    def pick(fmt):
        result[0] = fmt
        dlg.destroy()

    tk.Button(btn_frame, text="CSV", font=FONT_MAIN,
              bg=ACCENT, fg=BG_DARK, relief="flat", padx=20, pady=6,
              command=lambda: pick("csv")).pack(side="left", padx=8)
    tk.Button(btn_frame, text="Markdown", font=FONT_MAIN,
              bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=20, pady=6,
              command=lambda: pick("md")).pack(side="left", padx=8)
    tk.Button(btn_frame, text="取消", font=FONT_MAIN,
              bg=BG_MID, fg=FG_DIM, relief="flat", padx=20, pady=6,
              command=dlg.destroy).pack(side="left", padx=8)

    dlg.wait_window()
    return result[0]


def _export_csv(out_path: Path, img_groups: list, vid_groups: list):
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["类型", "组号", "标签", "文件路径", "文件大小(bytes)", "修改时间"])
        for media_type, groups in [("图片", img_groups), ("视频", vid_groups)]:
            for i, grp in enumerate(groups, 1):
                for p in grp.files:
                    try:
                        st    = p.stat()
                        size  = st.st_size
                        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        size = mtime = "N/A"
                    writer.writerow([media_type, i, grp.label, str(p), size, mtime])


def _export_markdown(out_path: Path, img_groups: list, vid_groups: list):
    lines = [
        "# 相似图片 / 视频报告",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"哈希阈值：（见各 Tab 设置）",
        "",
    ]
    for media_type, groups in [("图片", img_groups), ("视频", vid_groups)]:
        if not groups:
            continue
        same = sum(1 for g in groups if g.label == "SAME")
        sim  = len(groups) - same
        lines += [
            f"## {media_type}相似组（共 {len(groups)} 组：{same} 完全相同 / {sim} 内容相似）",
            "",
        ]
        for i, grp in enumerate(groups, 1):
            total_bytes = sum(p.stat().st_size for p in grp.files if p.exists())
            lines += [
                f"### 组 {i:03d} [{grp.label}] — {len(grp.files)} 个文件，{fmt_size(total_bytes)}",
                "",
                "| 文件路径 | 大小 | 修改时间 |",
                "|---|---|---|",
            ]
            for p in grp.files:
                try:
                    st    = p.stat()
                    size  = fmt_size(st.st_size)
                    mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    size = mtime = "N/A"
                lines.append(f"| `{p}` | {size} | {mtime} |")
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        source_dirs = [Path(p) for p in sys.argv[1:]]
    elif IS_WINDOWS:
        default = Path(r"C:\Temp\self\ss")
        source_dirs = [default] if default.exists() else []
    else:
        source_dirs = []

    App(source_dirs).mainloop()


if __name__ == "__main__":
    main()

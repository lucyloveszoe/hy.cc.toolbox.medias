"""
gui_viewer.py
-------------
相似图片 / 视频查看器 GUI（跨平台 macOS / Windows）。

相似度策略
──────────
图片（两步）
  Step-1 [SAME]    : 文件大小 + 图片尺寸（像素宽高）完全相同 → 视为"完全相同"
  Step-2 [SIMILAR] : pHash + wHash 双哈希距离均 ≤ HASH_THRESHOLD → 视为"内容相似"

视频（两步）
  Step-1 [SAME]    : 文件大小 + 视频分辨率 + 时长（秒，精度 1s）完全相同 → 视为"完全相同"
  Step-2 [SIMILAR] : 前 300s 均匀取 10 帧，任意一帧对 pHash+wHash 均 ≤ HASH_THRESHOLD → 视为"内容相似"

GUI 功能
──────────
  • 顶部 Tab：「图片」/ 「视频」分开展示
  • 左侧分组树：[SAME]/[SIMILAR] 标签 + 文件数 + 总大小
  • 右侧预览区：并排展示缩略图、完整路径、大小、修改时间
  • 可勾选 → 移入回收站 / 永久删除
  • 保留最新、保留最大、全选/取消快捷操作

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
HASH_THRESHOLD    = 15          # pHash / wHash 汉明距离阈值（两者都满足才判为 SIMILAR）
THUMB_SIZE        = (200, 200)  # 图片/视频相似组缩略图尺寸
VIDEO_PREVIEW_SIZE = (256, 256) # 第三个Tab帧预览尺寸，对齐系统 Extra Large Icons
VIDEO_SAMPLE_SECS = 300.0       # 视频采样窗口（秒）
VIDEO_SAMPLE_N    = 10          # 采样帧数（前300秒均匀取10帧）
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".m4v", ".webm"}

IS_MAC     = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

# 颜色 / 字体
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
    """跨平台打开文件。"""
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
    """跨平台移入回收站。优先用 send2trash，否则直接删除。"""
    if HAS_SEND2TRASH:
        send2trash(str(path))
    else:
        path.unlink()


def get_image_meta(path: Path) -> tuple | None:
    """返回 (file_size, width, height)；失败返回 None。"""
    try:
        sz = path.stat().st_size
        with Image.open(path) as img:
            w, h = img.size
        return (sz, w, h)
    except Exception:
        return None


def get_image_hashes(path: Path) -> tuple | None:
    """返回 (phash, whash)；失败返回 None。"""
    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
        return (imagehash.phash(rgb), imagehash.whash(rgb))
    except Exception:
        return None


def get_video_meta(path: Path) -> tuple | None:
    """
    用 ffprobe 读取视频元数据。
    返回 (file_size, width, height, duration_sec)；失败返回 None。
    """
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
                w   = s.get("width")
                h   = s.get("height")
                dur = float(s.get("duration", 0) or 0)
                # 读取旋转元数据（手机竖拍视频常见 rotate=90 或 270）
                rotate = int(s.get("tags", {}).get("rotate", 0) or 0)
                break
        if dur is None:
            dur = float(data.get("format", {}).get("duration", 0) or 0)
        if w and h:
            # 旋转 90° / 270° 时宽高互换，统一为"显示后"的分辨率再比较
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
    """
    在视频前 max_seconds 秒内均匀提取 n_frames 帧，返回 PIL Image 列表。
    若视频时长 < max_seconds，则在整个视频内均匀采样。
    会从 stream 与 format 两处读取时长；若仍为 0 则按 1 秒窗口取帧。
    若传入 _last_error=[], 失败时会在其中写入一条 ffmpeg 报错信息（供 GUI 显示）。
    """
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

        # 时长为 0 或未知时用 1 秒窗口，避免在 120s 处取帧导致失败
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
                r2 = subprocess.run(
                    cmd,
                    capture_output=True, text=True, timeout=20
                )
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
            # 旧版 ffmpeg 可能不支持 -autorotate，重试一次
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
    """提取视频首帧，用于缩略图预览。"""
    frames = extract_video_frames(video_path, n_frames=1, max_seconds=5)
    return frames[0] if frames else None


def get_video_hashes(path: Path) -> list | None:
    """
    在视频前 300 秒内均匀取 10 帧，返回每帧的 (phash, whash) 列表。
    若无法提取任何帧则返回 None。
    """
    frames = extract_video_frames(path, n_frames=VIDEO_SAMPLE_N, max_seconds=VIDEO_SAMPLE_SECS)
    if not frames:
        return None
    result = []
    for img in frames:
        try:
            result.append((imagehash.phash(img), imagehash.whash(img)))
        except Exception:
            pass
    return result if result else None


def videos_are_similar(hashes_a: list, hashes_b: list) -> bool:
    """
    任意一对帧的 phash 和 whash 距离都 <= HASH_THRESHOLD，判为相似。
    """
    for ph_a, wh_a in hashes_a:
        for ph_b, wh_b in hashes_b:
            if (ph_a - ph_b) <= HASH_THRESHOLD and (wh_a - wh_b) <= HASH_THRESHOLD:
                return True
    return False


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
    """代表一组相似文件，携带标签 'SAME' 或 'SIMILAR'。"""
    __slots__ = ("files", "label")

    def __init__(self, files: list[Path], label: str):
        self.files = files
        self.label = label   # "SAME" | "SIMILAR"


def collect_files(sources: "Path | list[Path]") -> tuple[list[Path], list[Path]]:
    """分别收集图片列表和视频列表，支持单个或多个目录，每个目录递归搜索。"""
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


def build_image_groups(images: list[Path], progress_cb=None) -> list[Group]:
    """
    图片两步分组：
      Step-1 [SAME]    : 文件大小 + 尺寸完全相同
      Step-2 [SIMILAR] : 双哈希距离均 ≤ HASH_THRESHOLD
    """
    total = len(images)
    if total == 0:
        return []

    parent, find, union = _make_union_find(total)
    same_pairs: set[tuple[int, int]] = set()

    # Step-1：读元数据
    meta_map: dict[tuple, list[int]] = defaultdict(list)
    for i, f in enumerate(images):
        m = get_image_meta(f)
        if m:
            meta_map[m].append(i)
        if progress_cb:
            progress_cb(
                "image_meta", i + 1, total, f, "读取图片元数据…"
            )

    for idxs in meta_map.values():
        if len(idxs) >= 2:
            for k in range(1, len(idxs)):
                a, b = idxs[0], idxs[k]
                union(a, b)
                same_pairs.add((min(a, b), max(a, b)))

    # Step-2：计算哈希
    hashes: list[tuple | None] = [None] * total
    for i, f in enumerate(images):
        h = get_image_hashes(f)
        if h is not None:
            hashes[i] = h
        if progress_cb:
            progress_cb(
                "image_hash", i + 1, total, f, "计算图片哈希…"
            )

    valid = [(i, hashes[i]) for i in range(total) if hashes[i] is not None]
    n = len(valid)
    total_pairs = n * (n - 1) // 2
    done = 0
    for a in range(n):
        ia, (ph_a, wh_a) = valid[a]
        for b in range(a + 1, n):
            ib, (ph_b, wh_b) = valid[b]
            if (ph_a - ph_b) <= HASH_THRESHOLD and (wh_a - wh_b) <= HASH_THRESHOLD:
                union(ia, ib)
            done += 1
            if progress_cb and done % 10000 == 0:
                progress_cb(
                    "image_compare", done, total_pairs, None, "比对图片相似度…"
                )

    # 聚合
    raw: dict[int, list[int]] = defaultdict(list)
    for i in range(total):
        raw[find(i)].append(i)

    groups = []
    for idxs in raw.values():
        if len(idxs) < 2:
            continue
        files = [images[i] for i in idxs]
        pairs_in_group = {(min(a, b), max(a, b)) for a in idxs for b in idxs if a != b}
        label = "SAME" if pairs_in_group.issubset(same_pairs) else "SIMILAR"
        groups.append(Group(files, label))
    return groups


def build_video_groups(videos: list[Path], progress_cb=None) -> list[Group]:
    """
    视频两步分组：
      Step-1 [SAME]    : 文件大小 + 分辨率 + 时长完全相同
      Step-2 [SIMILAR] : 前300s取10帧，任意帧对双哈希距离均 ≤ HASH_THRESHOLD
    """
    total = len(videos)
    if total == 0:
        return []

    parent, find, union = _make_union_find(total)
    same_pairs: set[tuple[int, int]] = set()

    # Step-1：读元数据
    meta_map: dict[tuple, list[int]] = defaultdict(list)
    for i, f in enumerate(videos):
        m = get_video_meta(f)
        if m:
            meta_map[m].append(i)
        if progress_cb:
            progress_cb(
                "video_meta", i + 1, total, f, "读取视频元数据…"
            )

    for idxs in meta_map.values():
        if len(idxs) >= 2:
            for k in range(1, len(idxs)):
                a, b = idxs[0], idxs[k]
                union(a, b)
                same_pairs.add((min(a, b), max(a, b)))

    # Step-2：前 120 秒多帧哈希
    hashes: list[list | None] = []
    valid_idx: list[int] = []
    for i, f in enumerate(videos):
        h = get_video_hashes(f)
        if h is not None:
            hashes.append(h)
            valid_idx.append(i)
        if progress_cb:
            progress_cb(
                "video_hash",
                i + 1,
                total,
                f,
                f"计算视频多帧哈希（前{int(VIDEO_SAMPLE_SECS)}s）…",
            )

    n = len(valid_idx)
    total_pairs = n * (n - 1) // 2
    done = 0
    for a in range(n):
        ia = valid_idx[a]
        for b in range(a + 1, n):
            ib = valid_idx[b]
            if videos_are_similar(hashes[a], hashes[b]):
                union(ia, ib)
            done += 1
            if progress_cb and done % 50 == 0:
                progress_cb(
                    "video_compare", done, total_pairs, None, "比对视频相似度…"
                )

    # 聚合
    raw: dict[int, list[int]] = defaultdict(list)
    for i in range(total):
        raw[find(i)].append(i)

    groups = []
    for idxs in raw.values():
        if len(idxs) < 2:
            continue
        files = [videos[i] for i in idxs]
        pairs_in_group = {(min(a, b), max(a, b)) for a in idxs for b in idxs if a != b}
        label = "SAME" if pairs_in_group.issubset(same_pairs) else "SIMILAR"
        groups.append(Group(files, label))
    return groups


# ── Tab 面板（图片 or 视频） ───────────────────────────────────────────────────

class MediaTab(tk.Frame):
    """
    一个 Tab 面板，包含：
      - 左侧分组树
      - 右侧预览区（缩略图 + 信息 + 勾选）
      - 底部操作按钮
    """

    def __init__(self, master, tab_label: str, app: "App"):
        super().__init__(master, bg=BG_DARK)
        self.tab_label = tab_label   # "图片" or "视频"
        self.app       = app
        self.groups: list[Group] = []
        self._thumb_refs: list   = []
        self._check_vars: dict[str, tk.BooleanVar] = {}

        self._build_layout()

    def _build_layout(self):
        paned = tk.PanedWindow(self, orient="horizontal", bg=BG_DARK,
                               sashwidth=5, sashrelief="flat")
        paned.pack(fill="both", expand=True)

        # ── 左侧分组树 ──
        left = tk.Frame(paned, bg=BG_MID, width=320)
        paned.add(left, minsize=220)

        tk.Label(left, text=f"{self.tab_label} 相似组",
                 font=FONT_BOLD, bg=BG_MID, fg=FG_TEXT, pady=6).pack(fill="x")

        tree_frame = tk.Frame(left, bg=BG_MID)
        tree_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        style_name = f"Dark{self.tab_label}.Treeview"
        style.configure(style_name,
                         background=BG_MID, foreground=FG_TEXT,
                         fieldbackground=BG_MID, rowheight=26,
                         font=FONT_SMALL)
        style.configure(f"{style_name}.Heading",
                         background=BG_CARD, foreground=ACCENT,
                         font=FONT_BOLD)
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

        # ── 右侧预览区 ──
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
        # macOS trackpad 滚动
        self.preview_canvas.bind_all("<Button-4>", self._on_trackpad_up)
        self.preview_canvas.bind_all("<Button-5>", self._on_trackpad_down)

    # ── 填充树 ────────────────────────────────────────────────────────────────

    def populate(self, groups: list[Group]):
        self.groups = groups
        self._check_vars.clear()
        self._clear_tree()
        self._clear_preview()

        same_count = sum(1 for g in groups if g.label == "SAME")
        sim_count  = len(groups) - same_count

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

        return same_count, sim_count

    def clear(self):
        self.groups = []
        self._check_vars.clear()
        self._clear_tree()
        self._clear_preview()

    # ── 预览 ──────────────────────────────────────────────────────────────────

    def _on_group_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        self._show_preview(self.groups[idx])

    def _show_preview(self, grp: Group):
        self._clear_preview()
        self._thumb_refs.clear()

        MAX_PREVIEW = 40
        all_files = sorted(grp.files, key=lambda p: p.stat().st_size if p.exists() else 0,
                           reverse=True)
        files = all_files[:MAX_PREVIEW]
        hidden = len(all_files) - len(files)

        canvas_w  = self.preview_canvas.winfo_width()
        card_w    = THUMB_SIZE[0] + 60
        cols_per_row = max(1, canvas_w // card_w) if canvas_w > 50 else 3

        tag_color = YELLOW if grp.label == "SAME" else ACCENT
        total_str = f"（共 {len(all_files)} 个，显示前 {MAX_PREVIEW} 个）" if hidden else f"（共 {len(all_files)} 个）"
        tag_text  = "✦ 完全相同（文件大小 + 尺寸一致）" if grp.label == "SAME" \
                    else "≈ 内容相似（视觉帧哈希匹配）"
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
                           activebackground=BG_CARD,
                           selectcolor=BG_DARK, fg=RED,
                           text="标记删除", font=FONT_SMALL).pack(anchor="w")

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

        # 操作按钮行
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
            tk.Label(self.preview_frame,
                     text=f"⚠ 还有 {hidden} 个文件未显示（勾选操作仍对全组 {len(all_files)} 个文件生效）",
                     font=FONT_SMALL, bg=BG_DARK, fg=YELLOW,
                     pady=4, padx=12, anchor="w"
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

    # ── 操作 ──────────────────────────────────────────────────────────────────

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

        action = "永久删除" if permanent else "移入回收站"
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

    # ── 布局辅助 ──────────────────────────────────────────────────────────────

    def _clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _clear_preview(self):
        for w in self.preview_frame.winfo_children():
            w.destroy()

    def _on_preview_configure(self, _event):
        self.preview_canvas.configure(
            scrollregion=self.preview_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.preview_canvas.itemconfig(self._preview_win, width=event.width)

    def _on_mousewheel(self, event):
        # Windows / Linux
        self.preview_canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _on_trackpad_up(self, _event):
        self.preview_canvas.yview_scroll(-1, "units")

    def _on_trackpad_down(self, _event):
        self.preview_canvas.yview_scroll(1, "units")


# ── 第三个 Tab：视频帧预览 ─────────────────────────────────────────────────────


class VideoPreviewTab(tk.Frame):
    """
    第三个 Tab：
      - 左侧列出所有视频文件
      - 右侧展示当前选中视频抽取的多帧预览图
      - 支持对视频执行与其它 Tab 一致的删除操作
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

        # 左侧：视频列表
        left = tk.Frame(paned, bg=BG_MID, width=320)
        paned.add(left, minsize=220)

        tk.Label(left, text="视频列表",
                 font=FONT_BOLD, bg=BG_MID, fg=FG_TEXT, pady=6).pack(fill="x")

        tree_frame = tk.Frame(left, bg=BG_MID)
        tree_frame.pack(fill="both", expand=True)

        style = ttk.Style()
        style_name = "DarkPreview.Treeview"
        style.configure(style_name,
                        background=BG_MID, foreground=FG_TEXT,
                        fieldbackground=BG_MID, rowheight=24,
                        font=FONT_SMALL)
        style.configure(f"{style_name}.Heading",
                        background=BG_CARD, foreground=ACCENT,
                        font=FONT_BOLD)

        self.tree = ttk.Treeview(tree_frame, style=style_name,
                                 columns=("duration", "size"),
                                 show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="文件名")
        self.tree.heading("duration", text="时长")
        self.tree.heading("size", text="大小")
        self.tree.column("#0", width=160, stretch=True)
        self.tree.column("duration", width=70, anchor="center")
        self.tree.column("size", width=80, anchor="e")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_video_select)

        # 右侧：帧预览
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
        self.preview_canvas.bind_all("<Button-4>", self._on_trackpad_up)
        self.preview_canvas.bind_all("<Button-5>", self._on_trackpad_down)

    # ── 对外接口 ───────────────────────────────────────────────────────────────

    def populate(self, videos: list[Path]):
        self.videos = [v for v in videos if v.exists()]
        self._check_vars.clear()
        self._clear_tree()
        self._clear_preview()

        for v in self.videos:
            try:
                st = v.stat()
                size_str = fmt_size(st.st_size)
            except Exception:
                size_str = "N/A"
            meta = get_video_meta(v)
            dur_str = f"{meta[3]}s" if meta else "?"

            fname = v.name if len(v.name) <= 32 else v.name[:29] + "…"
            self.tree.insert(
                "",
                "end",
                iid=str(v),
                text=fname,
                values=(dur_str, size_str),
            )

        # 默认选中第一条
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

    # ── 事件 / 预览 ──────────────────────────────────────────────────────────

    def _on_video_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        path = Path(sel[0])
        if not path.exists():
            return
        self._show_frames(path)

    def _show_frames(self, video_path: Path):
        self._clear_preview()
        self._thumb_refs.clear()

        # 勾选状态按视频为单位
        key = str(video_path)
        if key not in self._check_vars:
            self._check_vars[key] = tk.BooleanVar(value=False)
        var = self._check_vars[key]

        header = tk.Frame(self.preview_frame, bg=BG_DARK)
        header.pack(fill="x", pady=(4, 6), padx=8)

        tk.Checkbutton(
            header,
            text="标记此视频删除",
            variable=var,
            bg=BG_DARK,
            activebackground=BG_DARK,
            selectcolor=BG_CARD,
            fg=RED,
            font=FONT_SMALL,
        ).pack(side="left")

        tk.Label(
            header,
            text=str(video_path),
            font=FONT_SMALL,
            fg=FG_DIM,
            bg=BG_DARK,
            wraplength=900,
            justify="left",
        ).pack(side="left", padx=10)

        # 抽帧预览（传入 _last_error 以便失败时显示 ffmpeg 报错）
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
            tk.Label(
                self.preview_frame,
                text=msg,
                font=FONT_SMALL,
                fg=FG_DIM,
                bg=BG_DARK,
                justify="left",
                wraplength=700,
            ).pack(pady=20, padx=12)
            return

        row = tk.Frame(self.preview_frame, bg=BG_DARK)
        row.pack(padx=8, pady=4, anchor="w")

        for idx, img in enumerate(frames):
            try:
                img = img.copy()
                img.thumbnail(VIDEO_PREVIEW_SIZE, Image.LANCZOS)
                padded = Image.new("RGB", VIDEO_PREVIEW_SIZE, (40, 40, 60))
                offset = ((VIDEO_PREVIEW_SIZE[0] - img.width) // 2, (VIDEO_PREVIEW_SIZE[1] - img.height) // 2)
                padded.paste(img, offset)
                ph = ImageTk.PhotoImage(padded)
                self._thumb_refs.append(ph)
            except Exception:
                continue

            card = tk.Frame(row, bg=BG_CARD, padx=6, pady=6)
            card.pack(side="left", padx=6, pady=4)
            tk.Label(card, image=ph, bg=BG_CARD).pack()
            tk.Label(
                card,
                text=f"帧 {idx + 1}",
                font=FONT_SMALL,
                fg=FG_DIM,
                bg=BG_CARD,
            ).pack(pady=(4, 0))

        # 操作按钮行（对所有视频生效）
        ctrl = tk.Frame(self.preview_frame, bg=BG_DARK)
        ctrl.pack(fill="x", pady=10, padx=8)

        for text, cmd in [
            ("全选本组", self._check_all),
            ("取消全选", lambda: self._check_all(False)),
            ("保留最新，其余勾选", self._keep_newest),
            ("保留最大，其余勾选", self._keep_largest),
        ]:
            tk.Button(
                ctrl,
                text=text,
                font=FONT_SMALL,
                bg=BG_CARD,
                fg=FG_TEXT,
                relief="flat",
                padx=8,
                command=cmd,
            ).pack(side="left", padx=4)

    # ── 删除逻辑（与 MediaTab 接口对齐） ───────────────────────────────────────

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
        return [
            Path(k)
            for k, v in self._check_vars.items()
            if v.get() and Path(k).exists()
        ]

    def delete_checked(self, permanent: bool):
        targets = self.get_checked()
        if not targets:
            messagebox.showinfo("提示", "请先勾选要删除的视频。")
            return False

        action = "永久删除" if permanent else "移入回收站"
        preview = "\n".join(str(t) for t in targets[:10])
        if len(targets) > 10:
            preview += f"\n…还有 {len(targets)-10} 个"
        if not messagebox.askyesno(
            "确认",
            f"确定{action} {len(targets)} 个视频？\n\n{preview}",
        ):
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

    # ── 布局辅助 ──────────────────────────────────────────────────────────────

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
        # 支持多目录；单目录时也以列表形式存储
        self.source_dirs: list[Path] = source_dirs or []
        self._all_videos: list[Path] = []
        self._cancel_event = threading.Event()
        self._scanning = False

        self.title("相似图片 / 视频查看器")
        self.geometry("1400x860")
        self.configure(bg=BG_DARK)
        self.resizable(True, True)

        self._build_ui()

        valid = [d for d in self.source_dirs if d.exists()]
        if valid:
            self.after(100, self._start_scan)
        else:
            self.after(100, self._choose_dir)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # 工具栏
        toolbar = tk.Frame(self, bg=BG_MID, pady=6, padx=10)
        toolbar.pack(fill="x", side="top")

        tk.Label(toolbar, text="相似图片 / 视频查看器",
                 font=FONT_TITLE, bg=BG_MID, fg=ACCENT).pack(side="left")

        tk.Button(toolbar, text="📂 选择目录", font=FONT_MAIN,
                  bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=10,
                  command=self._choose_dir).pack(side="left", padx=(20, 4))

        tk.Button(toolbar, text="🔄 重新扫描", font=FONT_MAIN,
                  bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=10,
                  command=self._start_scan).pack(side="left", padx=4)

        self._cancel_btn = tk.Button(toolbar, text="⏹ 取消扫描", font=FONT_MAIN,
                                     bg="#4a1e1e", fg=RED, relief="flat", padx=10,
                                     state="disabled",
                                     command=self._request_cancel)
        self._cancel_btn.pack(side="left", padx=4)

        tk.Button(toolbar, text="📋 导出报告", font=FONT_MAIN,
                  bg=BG_CARD, fg=FG_TEXT, relief="flat", padx=10,
                  command=self._export_report).pack(side="left", padx=4)

        dir_text = "; ".join(str(d) for d in self.source_dirs) if self.source_dirs else "未选择目录"
        self.dir_label = tk.Label(toolbar,
                                  text=dir_text,
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

        # 哈希阈值滑块
        tk.Label(toolbar, text="阈值:", font=FONT_SMALL,
                 bg=BG_MID, fg=FG_DIM).pack(side="right", padx=(12, 2))
        self._threshold_var = tk.IntVar(value=HASH_THRESHOLD)
        self._threshold_label = tk.Label(toolbar,
                                         textvariable=self._threshold_var,
                                         font=FONT_BOLD, bg=BG_MID, fg=ACCENT,
                                         width=3)
        self._threshold_label.pack(side="right")
        self._threshold_slider = tk.Scale(
            toolbar,
            from_=1, to=30,
            orient="horizontal",
            variable=self._threshold_var,
            bg=BG_MID, fg=FG_TEXT,
            troughcolor=BG_CARD,
            highlightthickness=0,
            showvalue=False,
            length=120,
            command=lambda _: None,
        )
        self._threshold_slider.pack(side="right", padx=4)
        tk.Button(toolbar, text="Apply", font=FONT_SMALL,
                  bg=ACCENT, fg=BG_DARK, relief="flat", padx=8,
                  command=self._apply_threshold).pack(side="right", padx=(0, 4))

        # Notebook（Tab 切换）
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

        self.img_tab = MediaTab(self.notebook, "图片", self)
        self.vid_tab = MediaTab(self.notebook, "视频", self)
        self.preview_tab = VideoPreviewTab(self.notebook, self)
        self.notebook.add(self.img_tab, text="  🖼  图片  ")
        self.notebook.add(self.vid_tab, text="  🎬  视频  ")
        self.notebook.add(self.preview_tab, text="  📷  视频帧预览  ")

        # 状态栏
        status_bar = tk.Frame(self, bg=BG_MID, pady=4, padx=10)
        status_bar.pack(fill="x", side="bottom")

        self.status_var = tk.StringVar(value="就绪 — 请选择目录后开始扫描")
        tk.Label(status_bar, textvariable=self.status_var,
                 font=FONT_SMALL, bg=BG_MID, fg=FG_DIM).pack(side="left")

        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(status_bar, variable=self.progress_var,
                         maximum=100, length=220).pack(side="right", padx=8)

    # ── 扫描 ──────────────────────────────────────────────────────────────────

    def _export_report(self):
        img_groups = self.img_tab.groups
        vid_groups = self.vid_tab.groups
        if not img_groups and not vid_groups:
            messagebox.showinfo("提示", "暂无相似组数据，请先完成扫描。")
            return

        fmt = _ask_export_format(self)
        if not fmt:
            return

        ext = ".csv" if fmt == "csv" else ".md"
        default_name = datetime.now().strftime(f"%Y-%m-%d-similarity-report{ext}")
        out_path = filedialog.asksaveasfilename(
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

    def _apply_threshold(self):
        global HASH_THRESHOLD
        HASH_THRESHOLD = self._threshold_var.get()
        self._start_scan()

    def _choose_dir(self):
        init = str(self.source_dirs[0]) if self.source_dirs else str(Path.home())
        d = filedialog.askdirectory(initialdir=init, title="选择要扫描的目录")
        if d:
            self.source_dirs = [Path(d)]
            self.dir_label.configure(text=str(self.source_dirs[0]))
            self._start_scan()

    def _start_scan(self):
        valid = [d for d in self.source_dirs if d.exists()]
        if not valid:
            messagebox.showwarning("提示", "请先选择一个有效目录。")
            return
        if self._scanning:
            return
        self._cancel_event.clear()
        self._scanning = True
        self._cancel_btn.configure(state="normal")
        self.img_tab.clear()
        self.vid_tab.clear()
        self.preview_tab.clear()
        dirs_str = "; ".join(str(d) for d in valid)
        self.status_var.set(f"正在扫描 {dirs_str} …")
        self.progress_var.set(0)
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _request_cancel(self):
        if not self._scanning:
            return
        if messagebox.askyesno("确认取消", "确定要取消当前扫描吗？\n已完成的部分将丢失。"):
            self._cancel_event.set()
            self.status_var.set("正在取消扫描…")
            self._cancel_btn.configure(state="disabled")

    def _scan_thread(self):
        import time
        valid = [d for d in self.source_dirs if d.exists()]
        images, videos = collect_files(valid)
        self._all_videos = videos
        self.after(0, lambda: self.status_var.set(
            f"图片 {len(images)} 张 / 视频 {len(videos)} 个，正在分析…"))

        _last_update = [0.0]
        cancelled = [False]

        def progress(kind, done, total, path, msg):
            if self._cancel_event.is_set():
                cancelled[0] = True
                raise InterruptedError("用户取消")
            now = time.monotonic()
            if now - _last_update[0] < 0.5 and done < total:
                return
            _last_update[0] = now
            pct = done / total * 100 if total else 0
            desc = f"{msg} ({done}/{total})"
            self.after(
                0,
                lambda p=pct, m=desc: (
                    self.progress_var.set(p),
                    self.status_var.set(m),
                ),
            )
            if path is not None:
                print(f"[{kind}] {msg} {done}/{total} ({pct:.1f}%) - {path}", flush=True)
            else:
                print(f"[{kind}] {msg} {done}/{total} ({pct:.1f}%)", flush=True)

        try:
            img_groups = build_image_groups(images, progress_cb=progress)
            vid_groups = build_video_groups(videos, progress_cb=progress)
        except InterruptedError:
            self.after(0, self._on_scan_cancelled)
            return
        finally:
            self._scanning = False
            self.after(0, lambda: self._cancel_btn.configure(state="disabled"))

        img_groups.sort(key=lambda g: (0 if g.label == "SAME" else 1, -len(g.files)))
        vid_groups.sort(key=lambda g: (0 if g.label == "SAME" else 1, -len(g.files)))

        self.after(0, lambda: self._populate_tabs(img_groups, vid_groups))

    def _on_scan_cancelled(self):
        self.status_var.set("扫描已取消")
        self.progress_var.set(0)
        print("[INFO] 扫描已由用户取消", flush=True)

    def _populate_tabs(self, img_groups: list[Group], vid_groups: list[Group]):
        img_same, img_sim = self.img_tab.populate(img_groups)
        vid_same, vid_sim = self.vid_tab.populate(vid_groups)
        # 第三个 Tab：使用所有原始视频列表进行预览
        self.preview_tab.populate(self._all_videos)

        total_groups = len(img_groups) + len(vid_groups)
        self.status_var.set(
            f"图片：{len(img_groups)} 组（{img_same} 相同 / {img_sim} 相似）  "
            f"视频：{len(vid_groups)} 组（{vid_same} 相同 / {vid_sim} 相似）"
        )
        self.progress_var.set(100)

        # 切到有结果的 Tab
        if img_groups:
            self.notebook.select(0)
        elif vid_groups:
            self.notebook.select(1)

    # ── 删除（委托给当前激活 Tab） ────────────────────────────────────────────

    def _delete_checked(self, permanent: bool):
        idx = self.notebook.index(self.notebook.select())
        if idx == 0:
            tab = self.img_tab
        elif idx == 1:
            tab = self.vid_tab
        else:
            tab = self.preview_tab
        # 删除后不自动重新扫描，由用户手动点击「🔄 重新扫描」决定
        tab.delete_checked(permanent)


# ── 导出辅助 ──────────────────────────────────────────────────────────────────

def _ask_export_format(parent) -> str | None:
    """弹窗让用户选择导出格式，返回 'csv' 或 'md' 或 None（取消）。"""
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
                        st = p.stat()
                        size = st.st_size
                        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        size = mtime = "N/A"
                    writer.writerow([media_type, i, grp.label, str(p), size, mtime])


def _export_markdown(out_path: Path, img_groups: list, vid_groups: list):
    lines = [
        f"# 相似图片 / 视频报告",
        f"",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"哈希阈值：{HASH_THRESHOLD}",
        f"",
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
                    st = p.stat()
                    size = fmt_size(st.st_size)
                    mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    size = mtime = "N/A"
                lines.append(f"| `{p}` | {size} | {mtime} |")
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        # 支持一个或多个目录参数
        source_dirs = [Path(p) for p in sys.argv[1:]]
    elif IS_WINDOWS:
        # Windows 下无参数时使用默认目录
        default = Path(r"C:\Temp\self\ss")
        source_dirs = [default] if default.exists() else []
    else:
        source_dirs = []   # macOS / Linux：启动后弹窗让用户选择

    App(source_dirs).mainloop()


if __name__ == "__main__":
    main()

"""
group_similar.py
----------------
扫描指定目录中的所有图片和视频，将视觉内容相似的图片归组到编号子目录中。

相似度判断：
  - 图片：使用感知哈希（perceptual hash）pHash，汉明距离 <= THRESHOLD 视为相似
  - 视频：提取首帧后同图片逻辑处理
  - 完全相同内容（不同文件名/尺寸/格式）也会被识别并归组

用法:
    python group_similar.py [源目录]          # 默认: C:\\tmp\\test
    python group_similar.py C:\\some\\folder

输出:
    源目录下以数字命名的子目录（001, 002, …），每组 >= 2 张相似图片
    源目录下 similarity-log.md
"""

import sys
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image
    import imagehash
except ImportError:
    print("[错误] 缺少依赖，请先运行: pip install Pillow imagehash")
    sys.exit(1)

# ── 配置 ──────────────────────────────────────────────────────────────────────
THRESHOLD = 8          # 汉明距离阈值（0=完全相同，越大越宽松）
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".m4v", ".webm"}


def get_image_hash(path: Path) -> imagehash.ImageHash | None:
    """计算图片感知哈希；视频提取首帧后计算。"""
    try:
        if path.suffix.lower() in VIDEO_EXTS:
            frame = extract_first_frame(path)
            if frame is None:
                return None
            img = frame
        else:
            img = Image.open(path).convert("RGB")
        return imagehash.phash(img)
    except Exception as e:
        print(f"  [跳过] {path.name}: {e}")
        return None


def extract_first_frame(video_path: Path) -> Image.Image | None:
    """用 ffmpeg 提取视频首帧（需要 ffmpeg 在 PATH 中）。"""
    try:
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", tmp_path],
            capture_output=True, timeout=30
        )
        if result.returncode == 0 and Path(tmp_path).exists():
            img = Image.open(tmp_path).convert("RGB")
            img.load()
            os.unlink(tmp_path)
            return img
        os.unlink(tmp_path)
    except Exception as e:
        print(f"  [ffmpeg] {video_path.name}: {e}")
    return None


def collect_files(source: Path) -> list[Path]:
    """递归收集所有图片和视频（跳过已整理的数字子目录）。"""
    all_exts = IMAGE_EXTS | VIDEO_EXTS
    files = []
    for f in source.rglob("*"):
        if not f.is_file():
            continue
        # 跳过已整理的分组子目录内的文件（防止重复运行时误处理）
        rel = f.relative_to(source)
        if rel.parts and rel.parts[0].isdigit():
            continue
        if f.suffix.lower() in all_exts:
            files.append(f)
    return sorted(files)


def build_groups(files: list[Path]) -> list[list[Path]]:
    """
    Union-Find 方式：计算所有文件的 pHash，
    将汉明距离 <= THRESHOLD 的文件归入同一组。
    返回只含 >= 2 个文件的组列表。
    """
    print(f"正在计算 {len(files)} 个文件的感知哈希...")
    hashes: list[tuple[Path, imagehash.ImageHash]] = []
    for i, f in enumerate(files, 1):
        h = get_image_hash(f)
        if h is not None:
            hashes.append((f, h))
        if i % 20 == 0:
            print(f"  {i}/{len(files)} 完成")

    n = len(hashes)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    print("正在比对相似度...")
    for i in range(n):
        for j in range(i + 1, n):
            dist = hashes[i][1] - hashes[j][1]
            if dist <= THRESHOLD:
                union(i, j)

    # 按根节点聚合
    groups: dict[int, list[Path]] = {}
    for i, (path, _) in enumerate(hashes):
        root = find(i)
        groups.setdefault(root, []).append(path)

    return [g for g in groups.values() if len(g) >= 2]


def move_groups(groups: list[list[Path]], source: Path) -> list[dict]:
    """将每组文件移入编号子目录，返回日志记录。"""
    log: list[dict] = []
    for idx, group in enumerate(groups, 1):
        folder_name = f"{idx:03d}"
        dest_dir = source / folder_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in group:
            dest = dest_dir / src.name
            # 防止同名冲突
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{src.stem}_{counter}{src.suffix}"
                counter += 1
            shutil.move(str(src), str(dest))
            log.append({"group": folder_name, "src": src, "dest": dest})
    return log


def write_log(log: list[dict], source: Path, group_count: int, file_count: int) -> None:
    lines = [
        "# Similarity Group Log",
        f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"源目录: {source}",
        f"共发现 {group_count} 个相似组，移动 {file_count} 个文件\n",
        "| 组号 | 原路径 | 新路径 |",
        "|------|--------|--------|",
    ]
    for entry in log:
        lines.append(f"| {entry['group']} | `{entry['src']}` | `{entry['dest']}` |")

    log_path = source / "similarity-log.md"
    log_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"日志已写入: {log_path}")


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\tmp\test")

    if not source.exists():
        print(f"[错误] 目录不存在: {source}")
        sys.exit(1)

    print(f"源目录: {source}")
    files = collect_files(source)
    if not files:
        print("未找到任何图片或视频文件。")
        return

    print(f"找到 {len(files)} 个媒体文件")
    groups = build_groups(files)

    if not groups:
        print("未发现相似内容，无需分组。")
        return

    print(f"\n发现 {len(groups)} 个相似组，开始移动文件...")
    log = move_groups(groups, source)
    total_moved = sum(len(g) for g in groups)

    print(f"\n整理完成：{len(groups)} 个组，共移动 {total_moved} 个文件")
    write_log(log, source, len(groups), total_moved)


if __name__ == "__main__":
    main()

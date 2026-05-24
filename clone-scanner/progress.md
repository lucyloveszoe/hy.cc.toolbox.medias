# Progress Notes — 相似图片 / 视频查找工具

---

## 项目背景

个人媒体文件整理工具。用于扫描指定目录，找出所有相似或重复的图片和视频，通过 GUI 并排预览后，选择性删除冗余文件。

目标参考：[Fast Duplicate File Finder](https://www.mindgems.com/products/Fast-Duplicate-File-Finder/Fast-Duplicate-File-Finder-Screenshots.htm)

---

## v1 — 2026-03-15 ✅ 完成

### 关键决策记录

**图片相似度：两步策略**
- 第一步（SAME）：比较文件大小 + 图片像素尺寸。完全一致即视为"相同"，不依赖时间戳。快速、无损、零误判。
- 第二步（SIMILAR）：用 pHash + wHash 双哈希比较视觉内容，汉明距离均 ≤ 15 才判为相似。双哈希比单哈希误判率更低。

**视频相似度：方案 A（帧哈希）**
- 备选方案 B 是引入人脸识别（face_recognition / deepface），能识别"同一个人"，但处理慢、依赖复杂。
- 本次选方案 A：在视频前 120 秒内均匀取 6 帧，对每帧做 pHash + wHash，任意帧对匹配即判为相似。
- 方案 B 留作 v2 扩展。

**回收站：跨平台**
- 原代码用 Windows `ctypes + SHFileOperationW`，macOS 完全不可用。
- 改为引入 `send2trash` 库，一行代码统一处理 macOS / Windows / Linux。
- 未安装时自动降级为直接删除，并在按钮文字上给出提示。

**打开文件：跨平台**
- 原代码用 `os.startfile()`（Windows 专属）。
- macOS 改为 `subprocess.Popen(["open", path])`，Linux 用 `xdg-open`。

**GUI 架构：双 Tab**
- 原版图片和视频结果混在一个列表里，难以区分。
- 改为顶部 Notebook，「🖼 图片」和「🎬 视频」各自独立的分组树 + 预览区 + 勾选状态。
- 每个 Tab 封装为 `MediaTab` 类，主窗口 `App` 只负责扫描调度和 Tab 切换。

**启动方式**
- 原代码硬编码 `C:\Temp\self\ss`，macOS 根本无法使用。
- 改为：有命令行参数则直接用，无参数则启动后弹窗选目录。

### 最终文件结构

```
projects/photo-similarity-group/
├── scripts/
│   ├── gui_viewer.py       # 主程序（v1 完整版）
│   └── group_similar.py    # 早期原型，已被 gui_viewer.py 覆盖，保留备查
├── requirements.txt        # Pillow, imagehash, send2trash
├── _MANIFEST.md
└── progress.md             # 本文件
```

### 依赖说明

| 库 | 用途 |
|---|---|
| Pillow | 图片读取、缩略图生成、格式转换 |
| imagehash | pHash / wHash 感知哈希计算 |
| send2trash | 跨平台移入回收站 |
| ffmpeg（系统级） | 视频元数据读取、帧提取（需单独安装） |

### 已知限制 / 注意事项

- **视频扫描慢**：每个视频需提取 6 帧并计算哈希，文件多时耗时明显。（扫描支持取消，见 v1.2）
- **HEIC 格式**：需要系统安装 `pillow-heif` 插件才能正常读取 iPhone 拍摄的 HEIC 图片。
- **ffmpeg 必须在 PATH 中**：视频功能依赖系统 ffmpeg；macOS 用 `brew install ffmpeg`，Windows 需手动下载并配置 PATH。

---

---

## v1.1 — 2026-03-15 ✅ 完成

### 修复：视频旋转方向问题

**问题**：手机竖拍视频（常见于 iPhone）在文件元数据中存储的是原始横向分辨率（如 `1920×1080`），但带有 `rotate=90` 或 `rotate=270` 的旋转标记。ffmpeg 提取帧时默认不处理这个标记，导致：
1. Step-1 SAME 判断：两个内容相同但一个旋转过的视频，分辨率读出来一个是 `1920×1080`，另一个是 `1080×1920`，被误判为不同
2. Step-2 SIMILAR 哈希：提取出来的帧方向错误（横竖不一致），哈希值差异大，导致漏判

**修复方案**：
- `get_video_meta`：读取流的 `tags.rotate` 字段，若为 90° 或 270° 则将宽高互换，统一为"显示后"的尺寸再做 SAME 比较
- `extract_video_frames`：ffmpeg 命令加入 `-autorotate 1` + `-vf scale=iw:ih`，让提取出的帧自动按旋转元数据校正方向

---

## v1.2 — 2026-03-23 ✅ 完成

### 新功能

1. **视频取帧数：3 → 6 帧** — `VIDEO_SAMPLE_N = 6`，提升相似度判断准确率
2. **第三个 Tab 预览尺寸：200 → 256px** — 独立常量 `VIDEO_PREVIEW_SIZE = (256, 256)`，对齐系统 Extra Large Icons
3. **扫描进度可取消** — 工具栏新增"⏹ 取消扫描"按钮，点击需二次确认，避免误操作；通过 `threading.Event` 实现线程安全取消
4. **导出相似组报告** — 工具栏新增"📋 导出报告"按钮，支持导出 CSV 或 Markdown，含组号/标签/路径/大小/时间
5. **自定义哈希阈值** — 工具栏新增滑块（范围 1–30）+ Apply 按钮，点击后以新阈值重跑完整扫描流程
6. **删除后不自动重新扫描** — 删除 / 移入回收站操作完成后保持界面不变，仅「重新扫描」按钮、Apply 按钮、选择新目录才触发重跑

---

## v2 — 待规划

### 候选功能

1. **HEIC 支持增强** — 自动检测并提示安装 pillow-heif
2. **视频人物识别（方案 B）** — 引入 deepface 或 face_recognition，识别"同一个人"出现在不同视频中；处理慢，适合离线批处理模式

---

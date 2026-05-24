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

## v1.3 — 2026-05-24 ✅ 完成

### 重构：图片 / 视频 Tab 独立控制

**背景**：原版图片和视频共享一套扫描控制（一个开始按钮、一个进度条、一个阈值滑块），参数无法按媒体类型区分。

**变更内容**：

1. **Tab 独立控制栏**
   - 每个 Tab（图片 / 视频）顶部独立一条控制栏，含：开始扫描、取消、清除结果、相似阈值滑块 + Apply
   - 视频 Tab 额外增加：采样帧数滑块（1–30，默认 10）、采样时长滑块（30–600s，步进 30，默认 300s）
   - 每个 Tab 有独立的进度条和状态文字，互不干扰

2. **取消保留部分结果**（行为变更）
   - 旧版：取消需二次确认，且丢弃所有已完成结果
   - 新版：点击"⏹ 取消"立即生效，无需确认；Union-Find 当前状态直接转为结果返回，已找到的分组仍显示
   - 状态栏显示：`已取消（部分结果：N 组，X 相同 / Y 相似）`

3. **明确的清除操作**
   - 新增"✕ 清除结果"按钮，扫描中禁用
   - "开始扫描"自动先清除上次结果再启动新扫描
   - 视频 Tab 清除时同步清空第三个 Tab（帧预览）的列表

4. **第三个 Tab 增加清除按钮**
   - 视频帧预览 Tab 标题栏右侧新增"✕ 清除"按钮

5. **工具栏精简**
   - 移除全局"⏹ 取消扫描"按钮（改为各 Tab 自有）
   - 移除全局阈值滑块（改为各 Tab 自有）
   - 保留：📂 选择目录、📋 导出报告、删除/回收站操作

**架构变化**：
- `build_image_groups` / `build_video_groups` 新增 `cancel_event` + `threshold` 参数，取消时返回部分结果而非抛异常
- `build_video_groups` 新增 `sample_n` / `sample_secs` 参数
- `videos_are_similar` → `_videos_similar`，新增 `threshold` 参数
- `_groups_from_uf` 抽为公共辅助函数消除重复代码
- `MediaTab` 完整重写：内含独立扫描线程、cancel_event、状态/进度变量
- `App` 移除：`_cancel_event`、`_scanning`、`_cancel_btn`、`_threshold_slider`、`_scan_thread`、`_populate_tabs`、`_apply_threshold`、`_request_cancel`、`_on_scan_cancelled`
- `App` 新增：`on_video_scan_complete(videos)` 供视频 Tab 扫描完成后触发帧预览 Tab 刷新

---

## v1.4 — 2026-05-24 ✅ 完成

### 工作流调整：手动触发扫描，Tab 优先选择

**背景**：旧版启动后自动弹出目录选择框、选完目录自动同时启动图片和视频两个扫描。用户无法决定扫哪个类型。

**变更内容**：

1. **启动不再自动触发任何操作** — App 启动后直接显示空白界面，等待用户操作
2. **"📂 选择目录"只更新目录，不触发扫描** — 选完目录后显示路径，用户自行点击对应 Tab 的"▶ 开始扫描"
3. **移除"▶▶ 全部扫描"工具栏按钮** — 避免绕过 Tab 选择直接触发两个扫描
4. **移除 `_start_all` 方法**

**新工作流**：
```
启动 → 选 Tab（图片 or 视频）→ 📂 选择目录 → ▶ 开始扫描 → （可取消）→ 查看结果 → 勾选删除
```
目录选择和扫描完全解耦，用户对"扫什么"有明确控制权。

---

## v2 — 待规划

### 候选功能

1. **HEIC 支持增强** — 自动检测并提示安装 pillow-heif
2. **视频人物识别（方案 B）** — 引入 deepface 或 face_recognition，识别"同一个人"出现在不同视频中；处理慢，适合离线批处理模式
3. **多目录选择** — 当前只支持单目录，可扩展为勾选多个目录合并扫描
4. **哈希缓存** — 扫描结果缓存到本地文件，重新 Apply 阈值时无需重新计算哈希

---

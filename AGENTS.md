# AGENTS.md — Mega Pro Pen Plotter (M0/M1a + M3 上位机)

Drive an Anycubic Mega Pro as a pen plotter / paper cutter over its **stock
Marlin USB serial** — no firmware flash, no board mods. Laser is sealed,
heaters/spindle are hard-blocked in software. Read `REPORT.md` (Chinese,
full handoff with evidence) before touching transport or calibration.

Doc hierarchy: this file + `REPORT.md` + `docs/preview-layout-blueprint.md`
(the refactor contract) are current and authoritative. `README.md` was
brought up to date 2026-09-23 (COM examples marked "pass `--port`", M303
wording now matches `guard.py`). `PRD.md` / `PRD_layout.md` are product
docs with status notes on top — where they disagree with code, trust code.

## Running / testing

Package is NOT pip-installed; `src/` is on the path only via `tests/conftest.py`
and the `scripts/*.py` header. From repo root:

```sh
PYTHONPATH=src python -m megapro --port COM7 probe   # CLI（README 里的裸 `python -m megapro` 缺 PYTHONPATH 会失败）
PYTHONPATH=src python -m pytest -q --basetemp=.pytest_tmp -p no:cacheprovider   # 全量回归
PYTHONPATH=src python -m megapro.gui               # 上位机（PySide6，中文）
```

- Core dependency: `pyserial==3.5` (`requirements-m0.txt`). GUI adds
  `PySide6==6.11.2` (`requirements-gui.txt`). Otherwise stdlib only
  (Python 3.14) — **no PyYAML**: the profile is parsed/written by the
  hand-rolled flat-YAML parser in `cli/main.py` (`_load_profile`/`_save_profile`),
  so keep `profiles/mega-pro-marlin.yaml` in flat `key: value` shape (top-level,
  no indentation for scalars; the `raw: |` block is handled specially).
- GUI 测试自设 `QT_QPA_PLATFORM=offscreen` 且每个测试文件独占一个
  `QApplication`（QWidget 用 QApplication、worker 用 QCoreApplication，
  同进程混用会挂死 —— 故 `test_gui_worker.py` 与 `test_gui_window.py` 分文件）。
- Sandbox note: if pytest errors with `PermissionError` on
  `C:\Users\zyq\AppData\Local\Temp\pytest-of-zyq`, rerun with
  `--basetemp=.pytest_tmp -p no:cacheprovider` (environment restriction, not a code bug).

## Machine facts (live-verified, not guesses)

- Board: Trigorilla-class ATmega2560, USB **COM7 via CP210x** (COM number can
  shift after reboot/hub changes — always pass `--port`, never trust a default).
- Firmware: Marlin 1.1.0-RC8 (V1.2.9), baud **250000**, fallback 115200
  (transport auto-scans both).
- Steps X80 Y80 Z400 E384; travel 210×210×205 mm; SD card dead (`SD init fail`) — USB only.
- **Open problem**: intermittent `ok`-without-motion / coordinate desync
  (firmware reports moved/logical coords but motors didn't, or logic vs
  physical disagree). Live-reproduced 2026-09-06 on a return move
  `G0 X0 Y50` (firmware said X20 Y100, pen far from line start — aborted,
  M112, power-cycled); NOT reproduced on 2026-09-07 rerun. Prime suspect:
  stepper power / driver thermal / belt slip, needs eyes on machine. See
  `REPORT.md` §6 and `logs/pen_trial_2026*.md` before assuming software bugs.
- **M114 `Count` field is UNRELIABLE** on this build (Marlin 1.1.0-RC8):
  verified twice — logic Z17 showed `Count Z 8802`, logic Y50 showed
  `Count Y 1495`, then self-corrected to 4000 after an unrelated move.
  Never use `Count` as lost-step/stall evidence; ground truth is logical
  coords + human/physical confirmation. **ok ≠ 已移动**：固件回 ok 只代表
  命令被接受。

## Architecture / layer rules（2026-09-23 重构后）

- `src/megapro/cli/main.py` — argparse entry (`python -m megapro`);
  subcommands: `probe`, `jog`, `home`, `stream`, `move`, `estop`, `console`.
  Exit codes: **0** ok, **2** usage/guard/config, **3** link lost.
- `src/megapro/transport/marlin_serial.py` — all serial I/O. Nothing else
  touches `pyserial` directly (estop's raw M112 send is the CLI's one exception;
  GUI 侧唯一入口是 `gui/worker.py`).
- `src/megapro/safety/guard.py` — `check()` vets **every** line before TX.
- `src/megapro/dialect/marlin.py` — G-code header/footer snippets.
- `src/megapro/toolchain/svg_to_gcode.py` — **纯数据侧**：`parse_svg`（SVG→
  折线，stdlib XML）/ `parse_svg_meta`（根尺寸声明，供 Placement 判定）/
  `nearest_neighbor_sort`。Z0/Z1 双轨的 G-code 发射器已于阶段 5 退役
  （发射统一在 `gui/job.py`，机器绝对 Z）。
- `src/megapro/preview/to_svg.py` — `preview_svg_from_segments(segments, *,
  work_origin)`：把 `gcode_parse.Segment` 渲成独立 SVG（DRAW 蓝实 /
  TRAVEL 红虚 / PLUNGE·RETRACT 灰竖标 + 工件原点十字）；吃的是
  「将发送同一份 lines 的 parse 回读」，不是裸折线。
- `src/megapro/gui/` — **上位机（PySide6, 中文）**，纯逻辑与 Qt 分层：
  - **唯一坐标权威 `gui/canvas/coords.py`（零 Qt 纯函数）**：`BED_W/BED_H
    = 210.0` 是全仓库唯一 210 数值定义处（§8.4 测试钉死）；`flip_y_scalar`
    是唯一 y 翻转实现，调用面共三处 —— SVG 互换层 `paper_to_svg_ydown`/
    `paper_from_svg_ydown` 与 `text_to_svg.py:256` 的归一化域格式编码
    （`flip_y_scalar(y, 1.0)`，蓝图 missing #5 明令的迁调）；
    `machine_from_paper`/`paper_from_machine` 恒等（M114 logical ≡ 纸面 mm）；
    `translate_paths`/`place_at_anchor`/`grid_steps`/`snap`。**任何文件出现
    手写翻转（如 `210 - y`）按 bug 处理**（tests/test_coords.py::
    test_flip_math_single_source 全树 pattern 扫描 + ::
    test_flip_symbol_level_zero_hit 符号级扫描钉死）。
  - **唯一显示翻转边界** `gui/canvas/view_transform.py`（负比例尺
    `QTransform(ppm,0,0,-ppm,dx,dy)` + `mm_from_view`/`view_from_mm` 浮点
    通道；禁用 mapToScene/mapFromScene 的 int 量化）。
  - `gui/canvas/`（两页共享画布核心）：`paper_scene.py`（网格两级/床框/
    原点十字/材料三框）、`paper_view.py`（set_zoom 显式锚点/滚轮缩放/中键
    平移）、`rulers.py`（tick=view_from_mm 浮点，0 刻度对齐纸面原点）、
    `items.py`（PathItem 直画 y-up + 拖动 mouseRelease 回写）、`snap.py`
    （SnapEngine：网格/对象吸附）、`handles.py`（旋转/等比缩放手柄）、
    `undo_cmds.py`（QUndoStack 命令，手势 token）、`gcode_items.py`
    （GcodePathItem 三色 + LiveMarker）。
  - **纯逻辑（无 Qt 可单测）**：`job.py`（`JobSpec`/`compile_job`/
    `CompiledJob`/`ZMap`/`check_bounds_v2`/`gcode_for_*`）、`gcode_parse.py`
    （将发送 lines → `Segment` 列表，预览≡发送的回读侧）、`controller.py`
    （状态机 gating / M114 解析 / jog·home·pen·park·Frame·MoveTo·GotoOrigin
    序列构造 / `can_start_job`）、`opt.py`、`presets.py`。
  - **薄 Qt 绑定**：`worker.py`（常驻 QThread 串口消费者，唯一碰 pyserial 的
    Qt 对象；`sequenceDone(bool)` 供 homed 门禁）、`main_window.py`
    （QTab 双页：Tab0 控制/作业、Tab1 排版/制作）、`calibration.py`
    （Z 触纸标定向导，写 `pen_down_z`/`cut_touch_z`）。入口
    `python -m megapro.gui`。
  - **M3 排版**：`layout/`（`model.py` 纯逻辑 mm y-up + `export_svg.py`
    拍平导出（y-down 对合）+ `doc_import.py` 组导入 + `layout_page.py`
    工具/属性/命令编排）、`text_to_svg.py`（fontTools 轮廓字 + 单线
    `data/chinese_hershey_heiti.json`）、`image_to_svg.py`（Pillow 阈值 +
    `bin/potrace.exe`）、`edge_to_svg.py`（骨架中心线）。
  - **预览 ≡ 发送（构造性）**：`compile_job` 产出 `CompiledJob.lines`（唯一
    真源）→ 预览吃 `parse_lines(lines)` 的 `segments`，worker 发同一份
    `lines`；生成器 bug 会原样出现在预览并被 golden 拦下。工件原点是
    **编译期纯平移**（不发 G92）——设/清原点即 `_recompile()`。
  - **`JobSpec` 内存唯一源 + `_recompile()` 唯一汇流**：触发集 = 设/清原点、
    速度/跳段/切深/边距/材料、去重/顺序/笔径、放置模式、工具切换（保留
    几何、换 ZMap）、Z 标定回写、预设应用、载入/排版导出；执行中 no-op +
    提示，`jobDone` 后补编译（进度高亮用执行开始时的不可变快照）。
  - **对准工具 + homed 门禁**：走边框 Frame / 四角·中心点检 / 回原点 /
    空跑校验（`compile_job(dry_run=True)`，可选物理空跑）。`homed` 仅在
    一键寻零序列 `sequenceDone(ok=True)` 后置位（guard 拦一半必为 False），
    连接打开/FAULT/ESTOP/断开清零；`can_start_job` 拦未连接/未归位的
    执行/Frame/设原点（点检/回原点同）；未勾『允许 Z』禁 Z 动作（含一键
    寻零，内含 `G28 Z`）并给中文原因。
  - **Z 映射**：`ZMap(safe_z, down_z)`——写字落笔 = `pen_down_z`；裁刀
    下压 = `cut_z_for_depth(cut_touch_z, depth)` = 触纸 Z − 深度；空跑
    `down_z := safe_z`（不下压）；段间低抬 `min(safe, down+travel_lift_mm)`。
    real 模式断言 `safe_z > down_z ≥ 0`，**输出所有 Z ≥ 0**。
  - 安全语义与 CLI 一致（负 Z 需 allow_z；XY 平移按笔态抬 Z；pen_down 用 profile）。
  - 测试：`test_coords/gcode_parse/gui_job/opt/text/image/doc_import/controller/
    layout.py`（纯逻辑）+ `test_gui_worker.py`（MarlinSim）+ `test_gui_window/
    gui_layout_window/gui_alignment/gui_edge/gui_presets.py`（独立
    QApplication, offscreen）。
- `scripts/` — hardware diagnostics only (raw probe, DTR bisect, boot timing,
  liveness M105 poll, camera frames); each inserts `src` into `sys.path` itself.
- `logs/` is git-ignored and holds run logs + `probe-*.txt` transcripts.
- `data/` — M3 单线字体数据 `chinese_hershey_heiti.json`（LingDong, OFL, ~9.8MB, 随仓库）。
- `bin/` — potrace.exe（GPL 外置二进制，随仓库；来源见其随附 README）。
- M0 acceptance rule: `probe` counts only when a human reads the real
  transcript in `logs/probe-*.txt`.

## Safety invariants (do not weaken)

- Guard blocks, case-insensitively, first-token `M104 M109 M140 M190 M3 M4 M5`
  (word-boundary aware so `M32`/`M500` survive) and exact `G5 G6` (laser);
  also rejects non-ASCII, lines > 96 chars, `N`-numbered lines. Note: `M303`
  (PID autotune, heats heater) is NOT blocked by `guard.py` — README 现与
  `guard.py` 一致（历史版本曾误称 M303 被拦）。Don't add guard-block list
  edits to make M303 pass/fail tests; the source of truth is
  `guard.py:BLOCKED_PREFIXES`.
- Z motion needs `--allow-z`; negative-Z / Z-homing moves additionally need
  `pen_down_z` present in the profile. XY travel auto Z-hops +5 mm unless
  `--draw`. `home` lifts +10 mm before G28. Z never below 0.（GUI 同语义；
  `compile_job` 再断言输出全 Z ≥ 0。）
- After any serial open the board **resets** (DTR edge): coordinates zero,
  soft-limit clamps silently return `ok` without motion. Always home (G28)
  and use absolute coords after reset. `open_link()` already handles
  dtr/rts-low + `DTR_WAIT` 2.5 s + `settle_link()` cold-boot drain (~16 s) —
  keep that order if you touch transport.
- `G28` can take ~60 s, a single long move ~30 s (`HOME_TIMEOUT`/`MOVE_TIMEOUT`);
  don't shorten them.
- Multi-command calibration/sequences must use the `console` subcommand (one
  persistent session = no per-command resets). Each separate CLI invocation
  re-opens the port and resets the board.（GUI 会话 = 常驻连接，同理。）
- 红线：`safety/guard.py`、`transport/*`、`dialect/*` 一字不改；`worker.py`
  的 guard/estop/pause/job 流控语义不改（执行期 M114 轮询因此明确不做）。

## Current state / next steps (2026-09-23)

Calibrated (live 2026-09-07): pen touches paper at absolute **Z17**
(`pen_down_z: 17.0`, from safe Z30 down 13); safe point **X0 Y50 Z30**.
The pen is **friction-fit, no spring**. 2026-09-23：预览与排版重构（蓝图
`docs/preview-layout-blueprint.md`）阶段 1–5 全部落地——10 个根因（原点不重
编译、双 y 约定、旋转镜像、flip_y 床反射、首段漏定位 G0、非零角反向、Word/Excel
漏翻、拖动不回写、竖标尺恒偏、数值定位/吸附脱节）均已修复并有回归；详见
`REPORT.md` 的 2026-09-23 重构记录。

## 已知缺口 / 后续（2026-09-23，明确不做或待做）

- **执行期 M114 轮询**：run_job 期间 LiveMarker 冻结置灰（无位置回报）——
  要做得改 worker `run_job` 发送/暂停/中止流控（零改动红线），明确不做；
  脱节对照靠停机读 M114/人眼复核（§6 硬件问题软件无法构造性解决）。
- **相机叠加 / Print&Cut 双点套准**（对准强制项已由 Frame/点检/设原点覆盖）。
- **G92 / WCS**：与「开串口即复位」冲突；工件原点现为编译期纯平移。
- **TiledPathItem**（上千段折线的 exposedRect 分块渲染）：接口预留，
  段数 >2000 实测需要才做。
- SVG 单位换算（px/in、viewBox 缩放）、越界自动裁剪（只拦截+报告）、
  橡皮筋/凸包 Frame、Word/Excel 样式保真 —— 均按蓝图 §9 明确不做。
- 契约曾列「拖动的对象吸附（端点/中点/边）」「吸附 pitch 联动 grid_steps
  minor」「数值输入撤销合并（手势 token）」为缺口；**本树已实现**并有回归
  （`tests/test_gui_layout_window.py::test_drag_snaps_to_object_key_points`、
  `::test_snap_pitch_follows_zoom`、`::test_numeric_edit_merges_undo_per_gesture`；
  实现在 `canvas/snap.py`、`canvas/items.py`、`layout/layout_page.py`）——
  按「写事实」不列为缺口，若回归丢失再挂账。
- 真机验收（不计 pytest）：Frame 与预览 bbox 目视一致；设原点后落笔对准
  <1mm（人眼）；空跑全程不触纸 —— 待现场执行。

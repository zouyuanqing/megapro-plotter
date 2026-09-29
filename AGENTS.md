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
    `bin/potrace.exe`）、`edge_to_svg.py`（骨架中心线 + Canny 照片/素描）。
    **2026-09-27 模型树化后**：`Item` 增 `children`（容器=children 非空）/
    `mirror_x`·`mirror_y`/`image_spec`；`Document` 持 `pages` + **当前页门面**
    （`items`/`bed_w`/`add`/`items_visible`/`top_z`… 全部委托当前页，故
    `export_svg.py` 零改动即按当前页导出）；组用**扁平场景 + 自管组框 overlay**
    （`canvas/group_overlay.py`，**不引入 Qt 父子**）；镜像数学只在
    `canvas/coords.py::mirror_scalar`（复用唯一翻转实现 `flip_y_scalar`）。
    测试：`test_gui_layout*.py`（model/doc_import 冻结 + group/pages/retrace）、
    `test_gui_mirror.py`、`test_gui_edge.py`（纯逻辑 + 独立 QApplication）。
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

## Current state / next steps (2026-09-29 更新)

> 本节是**里程碑史**（到 2026-09-27 的 M1–M6 落地为止）。此后又经历 16 轮
> 「交付收口」（`0f160c3..67883e8`），**当前的技术状态、哪些缺口还开着、
> 哪些测试钉不住什么，一律看下面「已知缺口 / 后续」那一节**（那是唯一权威版，
> 2026-09-29 第 16 轮复核后重写）。本节的「326 passed」是 2026-09-27 的数字，
> **不是当前的全量数字**。

Calibrated (live 2026-09-07): pen touches paper at absolute **Z17**
(`pen_down_z: 17.0`, from safe Z30 down 13); safe point **X0 Y50 Z30**.
The pen is **friction-fit, no spring**. 2026-09-23：预览与排版重构（蓝图
`docs/preview-layout-blueprint.md`）阶段 1–5 全部落地——10 个根因（原点不重
编译、双 y 约定、旋转镜像、flip_y 床反射、首段漏定位 G0、非零角反向、Word/Excel
漏翻、拖动不回写、竖标尺恒偏、数值定位/吸附脱节）均已修复并有回归；详见
`REPORT.md` 的 2026-09-23 重构记录。

2026-09-27：**排版模型树化 PRD（`docs/PRD_layout_model_tree.md` v1.3）M1–M6
全部落地**，全量 pytest **326 passed**（基线 217）。新增能力与落点：
- **M1 树化地基**：`Item.children`（容器 = children 非空）、递归
  `bbox`/`transformed_paths`/`page_bbox`/`flatten_visible`（父∘子一般式）、
  `MAX_TREE_DEPTH=64` 成环保护、`Document.remove` 组感知 + `DetachInfo` 归属回执。
- **M2 编组**：`Document.group_items`/`ungroup`、`Group/UngroupCommand`、
  `canvas/group_overlay.py`（**扁平场景 + 自管组框 overlay，不引入 Qt 父子** ——
  `pos()` 是唯一返回父坐标系的接口，成组即错位）；组选中 = 叶子集，
  **组变换 = 叶子变换集合**、容器恒等；`_all_paths` 吸附候选切页面域
  （拖动侧与绘制侧共用 `snap_candidate_paths` 一份）。
- **M3 镜像**：`coords.mirror_scalar`（复用唯一翻转实现）、`Item.mirror_x/y`
  （支点 = **本地 bbox 中心**、pos/scale/angle **不补偿**）、合成顺序
  mirror→scale→rotate→translate、`rebuild_path` 烘镜像 + `ChangeItemPropsCommand`
  按 `_GEOMETRY_FIELDS` 额外重建（setattr 通道带不了几何）。
- **M4 多页**：`Document.pages` + **当前页门面**（`items`/`bed_w`/`add`/… 全委托）
  ⇒ `export_svg.py` **零改动**即按当前页导出；页签 UI + 场景全量重建；
  撤销 = **单栈 + 命令页归属**（跨页撤销作用到归属页再还原视图页）。
- **M5 图片重追**：`Item.image_spec`、`RetraceImageCommand`（显式
  `rebuild_path`）、加图对话框「照片/素描（Canny+骨架）」模式 + 双击就地重追
  （参数预填、pos/scale/angle 不变、可撤销）。
- 新增用例：`tests/test_gui_layout_group.py`（19）、`test_gui_mirror.py`（20）、
  `test_gui_layout_pages.py`（18）、`test_gui_layout_retrace.py`（18）、
  `test_gui_edge.py` 的 Canny golden（12，含上游 T11）。既有冻结面
  （`test_gui_layout.py` 37 / `test_gui_doc_import.py` 10 /
  `test_gui_layout_window.py` 21 / `test_coords.py` 12）**一字未改全绿**。

## 已知缺口 / 后续（2026-09-29 第 16 轮收口后；按「确切症状 + 触发条件」写）

> **本节的证据等级**：下面每条都带触发条件与实测现象。凡本轮**实测过**的，
> 括号里写了 `实测` 与现象数字；只读码得出的写 `读码`；**本会话未复现**的
> 明确标 `未独立复现`。本轮全部取证是 **offscreen Qt 模拟层**（真 `LayoutPage` /
> 真 `MainWindow` / 真 `QUndoStack`，**无串口、无电机**）——凡涉及「机器真的
> 怎么动」的，一律归到文末「需现场」。
>
> **怎么自己再验一遍**：本轮的探针都在 `.pytest_tmp/doccheck/`（独立脚本，
> 需 `os.environ.setdefault("QT_QPA_PLATFORM","offscreen")` 在 import PySide6
> **之前**、`sys.path.insert(0, r"D:\mcu_prj1\src")`）。定向回归：
> `PYTHONPATH=src python -m pytest -q --basetemp=.pytest_tmp/<名字> -p
> no:cacheprovider tests/<文件>`。

### 已销账（**旧文案作废，勿再照抄**）

- ~~**越界预检只警告不拦截**~~ —— **已修**。`layout_page._confirm_in_bed`
  （`layout_page.py:2150`）是「送去执行」与「另存为 SVG」**共用的唯一越床闸**：
  床内直接过；越界弹 Critical 框、「取消」是默认按钮、**关窗等同取消**，
  只有显式点「忽略并继续」才放行。`_on_export`（`:2203`）在 emit **之前**
  return ⇒ 越界几何绝不静默送去动刀。判据 `_out_of_bed`（`:2107`）走
  `iter_units(visible_only=True)` + `unit_page_bbox`（含祖先链）、床面取
  `BED_W/BED_H` 常量（不硬编码 210）。回归 `tests/test_gui_layout_bounds_gate.py`
  （本轮实跑，含在 106 passed 里）。
  **遗留两条**：① 「另存为 SVG」这个纯写文件、不动刀的动作也被同一道闸拦
  （见「仍然存在」B-13）；② **静默同步这条路根本够不着这道闸**（见「仍然存在」A-3）。
- ~~**撤销/重做按钮文案不刷新**~~ —— **不成立，症状不存在**。工具条按钮是
  `QUndoStack.createUndoAction` 建的 `QUndoAction`，Qt 每次 push/undo/redo/clear
  自动重写文案与可用态；`layout_page.py:651` 的 `_refresh_undo_actions` 只在构造时
  调一次且当时栈为空（`undoText()==''`），是**空转的死代码**。
  `tests/test_gui_layout_undo_actions.py` 6 条钉住文案/可用态。
- ~~**床尺寸在 5 条页操作路径上可能脱节**~~ —— **当前设计下不可表达**。
  `Document.bed_w/bed_h` 改成直通当前页的 property（`model.py:1024-1039`），
  床只有一处存储，恒等式从「纪律」变成结构事实。
  回归 `tests/test_gui_layout_bed_enum_structural.py`（含「bed 不得变回
  dataclass 字段」「门面是活读」「两个 Document 共用一个 Page 看到同一份床」）。
  **但要知道它守着的是个没人读的数**：`git grep` 统计 A2 触及表面
  （`sorted_items|items_visible|.bed_w|.bed_h|bed_w=|bed_h=`）在 `model.py`
  之外命中 **0**；导出（`export_svg.py:41` 宽度**写死** `BED_W`）、越床闸
  （`layout_page.py:2143`）、画布床框（`paper_scene.py:99/122`）三处生产链都
  读常量。**所以「页级床」当前是惰性的，别拿它做改床尺寸的 UI**（见「契约本身
  的缺口」第 1 条）。
- ~~**带自身折线的容器：导出切、画布不画**~~ —— **已在入档口堵死**。
  新增 `OwnGeometryContainerError`（`layout_page.py:217`）；`_item_from_json`
  （`:251`）在 `children` 与 `paths` 同时非空时抛出，`_paste`（`:1398`）与
  `_duplicate`（`:1421`）捕获后**整单拒绝 + 中文提示**。本轮实测：粘贴一个
  自带折线的容器 ⇒ `doc.items == []`、提示 1 条 ⇒ **造不出这个形状**。
  回归 `tests/test_gui_layout_own_geometry_ingest.py`（11 用例）。
  **遗留**：`flatten_visible` 仍把容器自身 paths 当一等几何（模型层口径未变），
  且入档口**只判这一个属性**（见「仍然存在」B-12）。
- ~~**`_page_box` 丢祖先链**~~ —— **读侧已修**。`layout_page.py:393` 改走
  `iter_ancestors(self.doc.items)`（从文档根查、命中即停），与同文件
  `_owner_container`（`:430`）同一口径。嵌套组的两层框现在重合于真实切割 AABB。
  回归 `tests/test_gui_layout_ancestor_chain_box.py`（6 用例）。
  **但写侧没跟上 —— 这是会动刀的那类，见「仍然存在」A-2。**
- ~~**组套组时一个组框都不画**~~ —— **已修**（交付收口第 1 轮）。`_complete_groups`
  （`group_overlay.py:108`）枚举面走 `iter_units`（任意深度），判据 = 「子树叶子
  传递闭包 ⊆ 当前叶子选择集」。回归 `tests/test_gui_canvas_group_nested.py`(7) +
  `test_gui_canvas_group_overlay_nested.py`(9)。
  **遗留**：判据单次成本从 O(#顶层容器) 变成 O(Σ子树点数)（见「仍然存在」F-29）。
- ~~**排版页零提示通道**~~ —— **已加常驻提示行**（交付收口第 14 轮）。
  `LayoutPage._build_status_line()`（`layout_page.py:711`）在画布与属性栏之间插了
  一条 `QLabel`（objectName=`layoutPageStatus`），`status_message` 接到
  `_show_status`（`:730`）；console 那条连接（`main_window.py:271`）保留。
  回归 `tests/test_gui_layout_status_visibility.py`（5 用例）。
  **但提示行有两处新问题，见「仍然存在」B-16、B-17。**
- ~~**工具条 8 个按钮选择不足时零反馈**~~ —— **已接中文提示**（交付收口第 16 轮）。
  `_zorder`(`:1520`) / `_align`(`:1578`) / `_distribute`(`:1610`) /
  `_delete_selected`(`:1371`) 四处守卫各发一条；另有编组/解组/删页的既有提示。
  回归 `tests/test_gui_layout_toolbar_feedback.py`（29 用例 + 5 skip）。
  **但「门槛已满足、操作本身空转」与「键盘入口」两个家族还在，见第 15、16 条。**
- ~~**组内图片双击提示与真实重追判据不一致**~~ —— **已统一为真值**（第 15 轮）。
  `layout_page.py:532` 从 `is not None` 改成 `if getattr(item, "image_spec", None):`，
  与消费者 `canvas/items.py:166` 同一条判据。回归
  `tests/test_gui_layout_image_spec_predicate.py`（7 用例）。
  **但真值 ≠ dict，见「仍然存在」B-21。**
- ~~**编译失败留下半提交的作业（spec 新 / lines 旧）**~~ —— **状态原子性已修**
  （第 13 轮）。新增 `MainWindow._set_job_and_recompile`（`main_window.py:1561`）：
  赋 spec + 重编译做成一步，编译不成就把 `_job_spec` **与 `_job_from_layout`
  来源标记一起**回滚；`_recompile`（`:1855`）多返回一个「是否真出了新 lines」
  的布尔值。回归 `tests/test_gui_layout_recompile_atomicity.py`（7 用例）。
  **但四个调用方都不看返回值，见「仍然存在」A-8。**

### 仍然存在

#### A. 会动刀（坐标 / 切割次序 / 切了什么内容）

1. **执行中删当前页 ⇒ 作业页永久握着已删页几何**。症状：作业跑着，切到排版页点
   「－」删掉当前页 —— 画布当场换成存活页，作业页仍握**已删页**的 `JobSpec`；
   控制台只打一句「执行中，参数改动本次作业结束后生效」，`jobDone` 的补编译用的
   还是那份没被换过的 spec ⇒ **作业页永远回不来**，再点执行，机器收到的是模型里
   已不存在的坐标。**本轮实测**：送出作业后 `_job_lines` X=[100,140] → 执行中删页
   → `jobDone` 后**仍是 [100,140]**、`run_btn` 仍 enabled。根因链：`_refresh_page_bar`
   在 `blockSignals(True)` 里重置页签 → 后续 `setCurrentIndex` 同值不发信号 →
   `_on_page_del`（`layout_page.py:841`）末尾那次 `_maybe_emit_job_sync` 走的是
   **执行中门禁**（`main_window.py:1629`，在赋 spec **之前** return）。
   非执行期同一路径已修好。
2. **组编辑态下读写坐标系混用 ⇒ 一次改宽把整组瞬移到原点**（第 9 轮引入的
   配套缺口，**会切错位置**）。`_page_box` 改成页面系之后，**读侧**是页面系、
   **写侧**仍是容器局部系：`_apply_size`(`layout_page.py:1249`) 的多选分支用
   `anchor_point(u)`（`u` 来自 `_unit_bbox` = 页面系）算绝对锚点，却把
   `it.pos`（**局部系**）按该锚点回写；`_apply_scale`(`:1292`)、`_align`(`:1570`)
   同一形状。组编辑态下 `_selected_units`(`:552`) 返回的**就是叶子**。
   **本轮实测**（真 `_paste` 造两层容器、外层 `pos=(100,0)`，双击进组选 a/b，
   属性面板填宽 20）：切割 AABB `(105,5)-(115,55)` → **`(0,0)-(20,100)`**，
   叶子 `pos` 变成 `(-105,-5)`，整组瞬移 105mm；`_out_of_bed()` 返回 `None`
   （全在 210 床内）、`status_message` **零条** ⇒ 静默切错位置。
   修法提示：只改读侧不够，得让写侧也走页面系（写回前换算）。
3. **画布画的坐标 ≠ 导出/切割的坐标**（非恒等祖先变换时，偏整段 pos/scale）。
   渲染通路只施加叶子**自身**变换：`canvas/items.py:90` `apply_model_state` 只
   `setPos/setScale/setRotation(item.pos/scale/angle_deg)`，**从不施加祖先变换**；
   `canvas/handles.py` 的选择框走 `sceneBoundingRect()`。而导出/切割走
   `flatten_visible`（页面系）。**本轮实测**（真 Ctrl+V 造两层容器、外层
   `pos=(100,0)`）：画布 PathItem 在 `(0,0) 10×10`，导出/切割 AABB 在
   `(105,5)-(115,15)`，**差 105mm**。纯 UI 也可达：编组 → Ctrl+C → Ctrl+V，
   粘贴件的 `dz=5` 让原件与副本在画布上完全重合，而副本会被切在 5mm 外。
   与第 2 条同源（渲染不认祖先变换），修一处要一起看。
4. **「置于底层」在选中 ≥2 个图元时与未选中图元同 z，切割次序被劈开**。
   **本轮实测**（X z=1 未选中、M0 z=2、M1 z=3、Y z=4，选 M0+M1 置底）：
   `M0=0.0, M1=1.0`，而未选中的 **X 仍是 1.0** ⇒ 切割序 `[10, 0, 20, 40]`，
   未选中的 X 被切在两个选中件**中间**。根因：`layout_page.py:1526` 的
   `cursor = self.doc.bottom_z() - 1` 起手、`:1563` 的 `cursor += 1` 只增不减 ⇒
   选 n 个叶子占 `[bottom-1, bottom-1+n-1]`，n≥2 时必然爬回已占用区。
   **置顶安全**（游标向上，必在全体之上），洞只在置底。
5. **执行中的几何编辑被永久丢弃，且提示文案说谎**。症状：作业跑着在排版页加一条
   线 → 控制台提示「本次作业结束后生效」→ 跑完 → 作业页仍是旧几何、旧位置，
   `run_btn` 仍亮，**再点一次执行，机器切的是旧内容旧位置**。**本轮实测**：
   送出作业后 X=[100,140]，执行中加一条含 X=50 的线，`jobDone` 后**仍是
   [100,140]**，控制台末行仍是「执行中，参数改动本次作业结束后生效」。
   这句文案对**参数**改动成立（`_recompile → _spec_with_current_params` 会重读
   控件值），对**几何**不成立 —— 而 `main_window.py` docstring 写下的缓解手段
   「用户下次切页即重新同步」**不成立**（`self.tabs` 没有 `currentChanged` 接线）。
6. **「锚点归位」把越床几何洗进床内，静默同步这条路根本没有越床闸**（第 11 轮
   修好 placement 不被静默重置后暴露）。`_maybe_emit_job_sync`
   （`layout_page.py:806`）**只**看开关，`MainWindow._on_layout_job_sync`
   （`main_window.py:1589`）**只有来源 + 执行中两道门禁**，`_confirm_in_bed`
   的调用点只有 `_on_save_svg` 与 `_on_export`，静默同步**永远够不着它**。
   而 `compile_job` 的顺序是「placement → work_origin → check_bounds_v2」
   （`job.py:429` → `:438`），`place_at_anchor` 先把内容整体归位 ⇒ 越床件被洗进床内，
   作业页的越界判据看到 `violations=0`。**本轮实测**（真控件：排版页放一个床内
   方块 → 送去作业 → 作业页选『锚点归位 bl→(0,0)』→ 回排版页属性面板把 X 键入
   -40）：`_out_of_bed()` = `(40.0, ['「INBED」(-40.0,0.0)-(0.0,40.0)'])`，
   而作业页 `runnable=True`、`violations=[]`、`_job_lines` 的 X 变成 **[0, 40]**、
   控制台**零新增行**。显式点「送去作业」此时会被闸门拦下（默认拒绝）—— 同一个
   版面、两条路，一个禁一个放行。
7. **锚点归位每次都按整份内容重算 ⇒ 没被编辑的图元被静默搬走；显式「送去作业」
   无条件复位 placement**。`place_at_anchor`（`canvas/coords.py:145`）对
   **整份内容**取一次 bbox 整体平移。**本轮实测**：
   ① 两个图元 A(0,0) B(50,50)，选 anchor 后**只删 A** ⇒ B 的落点从 (50,50)
   变成 (0,0)，**位移 50mm**，控制台零新增行；② 版面一个字没改、再点一次
   「送去作业」⇒ placement 从 `anchor` 变回 `preserve`、落点整刀平移 54/50mm，
   而控制台那一行还是每次都打的那句通用文案。combo 与作业信息行会跟着变，
   但**控制台没有任何一句说「放置方式变了」**。对照组：preserve 下同样操作
   B 一动不动。anchor 的重算本身是**写明的语义**（`job.py:214` Placement
   docstring、`coords.py:152`），可辩为设计；**无人提示 + 批次自测对它无感**
   不可辩。
8. **执行中改放置模式被原子包装当成「编译未成」回滚 ⇒「结束后生效」变丢弃**
   （第 13 轮引入的配套缺口）。`_set_job_and_recompile`（`main_window.py:1561`）
   把 `_recompile()` 的三个 `return False`（执行中 no-op / 无作业 / 参数非法）
   **一视同仁**地当成失败并回滚 spec。可执行期语义里，执行中 no-op 恰恰是
   **延迟生效**机制：参数投影已落进 `_job_spec`，等 `jobDone` 的补编译
   （`main_window.py:2089`）兑现。**本轮实测**：作业跑着把下拉切到『锚点归位
   bl→(0,0)』（`placement_combo` 执行期**未禁用**、`layout_page.py` 里
   `_job_running` 出现 0 次）⇒ 执行中 `spec.placement` 仍是 `preserve`、
   `jobDone` 后仍是 `preserve`、`_job_lines` X 不变 ⇒ **用户改的放置永久蒸发**，
   而 combo 停在「锚点归位」与模型长期不一致。对照：`feed_xy` 走**字段**
   （`_feed_xy`）而不是 spec，所以同一次执行中改 feed 是**生效**的 ——
   同一个触发集里两个字段两种待遇。修法提示：让 `_recompile` 区分「延迟」与
   「失败」两个返回值。
9. **编组/解组撤销栈往返后文档错位 + 幽灵空容器**。**本轮实测**（编组 → 解组 →
   一路撤到底 → 重做回顶 → 再撤）：重做回顶后 `undoText()` 是 **「解组」**而文档
   是**编组态**（`[('组3',['a','b','c'])]`）；再按一次 Ctrl+Z，顶层凭空多出一个
   **同名空容器** `[('组3',), ('组3',['a','b','c'])]`；一路撤到底折线归 0 而模型里
   留着 `[('组3',), ('组3',)]` 两个空壳。根因：`GroupCommand._do_redo`
   （`undo_cmds.py:541`）每次 redo 都 `doc.group_items()` **新建**容器并覆盖
   `self.container`，而 `UngroupCommand` 只捕获**一次**容器对象 ⇒ redo 拿到的是
   已被掏空的旧壳，`ungroup()` 返回 `[]` ⇒ 静默空转。数据未被破坏（A1 不变量
   全绿），但**撤销栈从此不再描述文档**。
10. **层序「跨单元相对次序」在 min(z) 并列时仍由 Qt 决定**（第 12 轮修了一半）。
    `layout_page.py:1548` 的排序键只有 `min(叶子 z)`、**没有次序键**，而
    `list.sort` 稳定 ⇒ 并列时是 no-op、原样沿用 `scene.selectedItems()` 的返回序
    （`items.py`/Qt 侧无序保证）。并列 z 在产品路径上真实存在：Word/Excel 导入
    （`layout_page.py:1891` `_add_doc`）给**全部**条目写同一个 `z = top_z + 1`。
    **本轮实测（诚实标注）**：构造并列场景连跑 6 个独立进程，`selectedItems()`
    在本机**每次都返回同一序**，6/6 收敛到同一个结果 —— **分叉未在本机复现**。
    代码层面的口径缺口是真的（键无次序分量），但「跨启动分叉」这一步本轮**未
    取得证据**。
11. **层序「组内相对次序」在 top/bottom 会被翻转**（读码 + 实测）。游标铺开走
    `iter_leaves` 的 **DFS 先序**（`layout_page.py:1550-1563`），而拍平口径是
    `(z, order)`（`model.py:1429`）⇒ 只要组内 children 的**文档序与 z 序相反**，
    置顶/置底就按相反方向递增 z。**本轮实测**：X(z=3) 先画、Y(z=2) 后画 →
    编组（children 文档序 `[X,Y]`）→ 选整组置顶 ⇒ children z 变 `[X=4, Y=5]`，
    **切割序从 `[Y, X]` 翻成 `[X, Y]`**。代码注释
    `layout_page.py:1545-1547` 写的是「top/bottom 都保持**选中前的相对层序**」
    —— 在**跨单元**口径上成立，在**组内**口径上被证伪（该注释与同批修的跨单元
    排序是同一句）。纯 UI 可达，全程只用按钮。

#### B. 会静默吞操作 / 说谎（不直接改刀，但会诱发错操作）

12. **剪贴板可灌入超深模型树，导出链与撤销双失**。`_item_from_json`
    （`layout_page.py:239`）是纯递归还原、**无深度上限**，`_check_depth`
    （`MAX_TREE_DEPTH=64`）只在**遍历时**才炸。**本轮实测**（真 `_paste`）：
    深度 63 通；**深度 64 静默入档**、`to_job_spec()` 抛
    `ValueError: 模型树深度超过 64`（该异常发生在 `QUndoStack.indexChanged`
    触发的 Python 槽里，被 PySide6 吞成 stderr traceback，**控制台零提示**），
    文档里从此留了一棵永远导不出的树；**深度 65 直接从 `_paste` 抛出**
    （`self.doc.top_z()` 那一行，**未捕获**），排版页从此粘不进任何东西。
    导出口径的安全线是 **63**（`iter_items` 比其余入口多下探一层）。
    入档口只捕 `OwnGeometryContainerError`（`:1398`/`:1421`），其它任何形状的
    载荷（`paths: null` / `children: null` / `children: [null]`）同样**未捕获地**
    抛出、同样零用户提示。**需外部载荷**（UI 造不出这么深的树）。
13. **越床闸口径比「真正送去切的」保守 ⇒ 只误拦、不会漏拦**。`_out_of_bed` 走
    `unit_page_bbox`（不过滤可见性、不丢弃 <2 点折线），`flatten_visible` 两者都
    过滤。**本轮实测**两例：组内**隐藏**件停在 (260,260)-(290,290) ⇒
    `flatten_visible` 只有那条全床内的矩形，闸门却报「超出 80.0mm」；零尺寸折线
    `[(30,213.4)]` ⇒ 拍平直接丢弃，闸门仍把它列进越界清单。方向是**保守超集**
    （不一致全是误拦、漏拦恒为 0），所以是假警报 + 无谓堵死导出（含纯写文件的
    「另存为 SVG」），**不是安全洞**。
14. **属性面板宽/高键入非正数：静默 return + 面板显示与真实 AABB 反号**。
    `sp_w/sp_h` 的 range 是 `(-1000,1000)`（`layout_page.py:1013`），而
    `_apply_size`（`:1256`）/ `_apply_scale`（`:1294`）对 `value <= 0` **裸
    return**。**本轮实测**：对一个真实 AABB 宽 20mm 的图元键入 -10 ⇒ 面板显示
    `-10.0`、真实 AABB 仍是 20.0、scale 仍是 1.0、`status_message` **零条**。
    这是**最响的一个数字谎言** —— 它正是用户拿去预测「切多宽」的那个数。
    提示行（见下）已经在页内了，**只是这根线没接**。
15. **宽/高与镜像 tooltip 仍是无条件全称句**。`sp_w.toolTip()` 实测原文：
    「宽 = 图元在纸面上的轴对齐包围盒(AABB)…**旋转或镜像后 AABB 会变**…数字
    跳变属正常」—— 但权威条件是「**仅当 `angle_deg` 为 90° 整数倍时守恒**」
    （`model.py:101-108`），而 angle=0 是所有新建/导入图元的默认角，
    0°/90°/180° 与轴对齐形状镜像后 AABB **逐位不变**。镜像按钮 tooltip 同样
    无条件。（`docs/PRD_layout_model_tree.md` §11 问题 4 的定稿明确要求「不得
    写成无条件的『AABB 会变』」，这条**尚未兑现**。）
16. **页内提示行：长消息被裁掉后半截**（第 14 轮那条提示行的新问题）。它是
    `QLabel`、**没开 wordWrap**（`_build_status_line` 只有 setObjectName +
    setToolTip + setText），宽度由整条未折行文本撑开。**本轮实测**（真
    MainWindow，1000px 宽窗口）：一条 97 字的中文拒收提示 `sizeHint` 宽 1164px、
    实际只分到 **696px（59.8%）**，被切掉的正是可操作的那半句
    「…已整单撤销粘贴，请确认剪贴板内容。」；排版页的 `minimumSizeHint` 宽被顶到
    **1182px**。消息越长越糟：图元名完全由剪贴板控制（`_item_from_json:262`
    `name=d.get("name","item")` 原样带出、零长度校验），名字一长就从「丢行动句」升级到「丢解释句」。
    `test_gui_layout_status_visibility.py` 只断言「存在一个 QLabel 后代 + 文本
    含该串 + `isVisible()` 为 True」，**看不见**这件事。
17. **页内提示行只会被下一次 `status_message` 改写，而全文件 14 个 emit 点里 11 个是拒绝 ⇒ 成功之后那行会长期停在与模型相反的陈述上**。`_show_status`
    （`layout_page.py:730`）只做 `setText`；而镜像**成功**分支
    （`:1466` 只 push 命令）与 `_exit_group_edit`（`:539`，Esc 可达）
    **都不发 emit**。**本轮实测**：无选中点「水平镜像」→ 提示行
    「未选中图元：请先选中要镜像的图元」；随后选中该图元**再点一次**→ 镜像
    **真的成功了**（`mirror_x=True`、按钮勾上、几何翻了），提示行**一字未变**、
    仍写着「请先选中」。用户此时切到作业页执行，机器切的就是已镜像几何，而
    排版页最大的一行字说没选中。
18. **`_align` 在「本来就对齐」时空转但仍 push 一条恒等命令**。`:1598` 对每个
    叶子的每个轴**无条件** append 一条 change（对比 `_zorder:1565` 的
    `if it.z != nz` 守卫）。**本轮实测**：两个 min-x 相同的图元点「左对齐」⇒
    几何零变化、提示行空、`status_message` 零条，但 `undo.index()` 1→2、
    `undoText()` 变成「对齐」。后果：用户接下来按 Ctrl+Z 撤掉真正的「置顶」之后，
    **第二次 Ctrl+Z 被这条什么都没发生的命令吃掉**（`undoText` 从「对齐」变成
    「添加」，而 z 不动、图元数不动、栈深不降）—— 这正是 E-27 描述的
    「按钮仍 enabled 且挂着命令真名，按下去模型与场景零变化」的**新实例**。
    `_distribute`（`:1606`）代码同样无守卫，但常规等宽输入会真动，只有退化
    输入（末位零宽）才空转。`_zorder` / `_delete_selected` 不受影响（有守卫 /
    有选中必删）。
19. **页签条的 ◀/▶ 在边界上纯静默**。`_move_page`（`layout_page.py:875`）里
    `if not self.doc.move_page(...): return` 是裸 return，而同一行的「－」
    （`:843`）在同样被模型拒绝时发「至少保留一页」。**本轮实测**四种边界情形
    （单页点 ◀/▶、两页停在首/末点 ◀/▶）全部 `status_message` 零条、页序真的没动；
    同一时刻点「－」得到 1 条提示。五个页签条按钮 `isEnabled()` 全 True、
    `toolTip()` 全空 ⇒ 「禁用即自证」这条路也不成立。**不影响切割**（页序不决定
    当前页几何与作业内容），是纯交互诚实性缺口。
20. **Ctrl+C 无选中时把用户剪贴板无条件覆写成 `"[]"`**（最重的一个静默操作）。
    `_copy_selected`（`layout_page.py:1373`）**无 `if not units` 守卫**，最后一行
    `clipboard().setText(json.dumps(data))` 无条件执行，空列表时写入字面量
    `"[]"`；紧接着 Ctrl+V 走 `_paste`（`:1385`）解析出空列表 ⇒ `if items:`
    不成立 ⇒ 又是一次静默。**本轮实测**：剪贴板里先放一段用户自己的文字，无选中
    按一次 Ctrl+C，它就被换成 `[]`，提示行零条、页面上零变化。用户到别处粘贴才
    会发现剪贴板空了，而「排版页」是全文唯一的 Ctrl+C 绑定处。
    **同类还有 Ctrl+D**（`_duplicate:1409` 的 `if items:`，实测无选中 msgs=[]）。
21. **`image_spec` 是真值不等于「是 dict」，也不等于「重追会打开」**（两处，
    均需外部载荷）。① `retrace_image_item`（`layout_page.py:2051`）第一句是
    `spec = item.image_spec or {}` 紧接 `spec.get("source")`，而这个 `.get`
    **在 `try:`（`:2061`）之外** ⇒ 一个真值-非-dict 的 spec（`0.5` / `"nope"` /
    `[1,2]`，经 Ctrl+V 的零校验 `_paste` 进得来）在第 2 次双击时抛
    `AttributeError` 逃出事件处理器：**既不开对话框、也不发 status_message**，
    只在 stderr 留 traceback。② `canvas/items.py:161-163` 的 `text_spec` 分支
    **排在** `image_spec` 分支（`:166`）之前且各自 return ⇒ 载荷同时带两个键时，
    提示说「再双击一次即可重追调参」（`layout_page.py:532` 只看 image_spec），
    第 2 次双击进的却是**文字编辑器**；接受后 `EditTextCommand`
    （`undo_cmds.py:420-422`）只写 paths + text_spec，**不写也不快照 image_spec**
    ⇒ 该图元几何已换成文字轮廓却仍自称「已追图片」。
    现有回归 `test_gui_layout_image_spec_predicate.py` 的不变量把
    `retrace_image_item` 打成桩，**retrace 函数体零执行**，且唯一正例 fixture
    `{"mode":"canny"}` 真跑时兑现的是「无法重追」警告（`:2053` 的
    `if not source` 先 return），**调参对话框从未被打开过**。

#### C. 需外部载荷 / 潜伏（今天没有纯 UI 路径）

22. **同一图元可被挂到两个父下 ⇒ 同一几何切两遍**（模型层，无守卫）。
    `Page.attach(owner=...)` 的守卫判的是「item 与 owner 互为后代」，**从不检查
    item 在 owner 子树之外是否已有归属**（`Page.attach` `model.py:648`，守卫在 `:698-712`）；`Document.add`
    与顶层 `attach(owner=None)` **刻意**不加守卫（`model.py:680-686` docstring
    明写，且被 `test_gui_layout.py::test_attach_restores_group_structure_exactly`
    以「错误路径复现」钉死）。可达性按本轮评审记录复核：全量套件生产栈帧触发
    **0 次**、20 条对抗序列 0 次 ⇒ **今天没有 UI 可达路径**，属潜在口子。
    契约由 `pytest.mark.xfail(strict=True)` 钉着，修好即报红逼人摘标记：
    `tests/test_gui_layout_attach_reparent.py`（R1）、
    `tests/test_gui_layout_tree_writer_enumeration.py`（R2）。
23. **`group_overlay` 不做身份去重**（`group_overlay.py:125-132`）：容器一旦同时
    出现在顶层和某个组的 children 下（22 的形状），同一容器会画两个完全重合的框。
    与 22 同源、同为潜伏。
24. **locked 成员的组永远画不出框，但整组照样切**。组判据是「子树叶子闭包 ⊆
    当前叶子选择集」，而 `_sync_gi`（`layout_page.py:383`）把 locked 图元的
    `ItemIsSelectable` 关掉 ⇒ 永远进不了选择集；`flatten_visible` 又不看 locked。
    **当前 UI 没有任何设置 locked 的控件**，只能由带 `"locked": true` 的
    剪贴板/版面 JSON 造出 ⇒ 需外部载荷。

#### D. 契约（strict xfail：修好即 XPASS 报红逼人摘标记）

25. **缩放路径白做的几何全量重扫**（`handles.py:152` → `_relayout_rotate` →
    `selection_rect()`（`:165`））。`selection_rect()` 是**缩放不变量**，代价是
    O(选中集总点数) 的纯 Python 循环。契约：
    `tests/test_gui_canvas_zoom_geometry_rescan.py`（R3，strict xfail）。
26. **拖动未提交时缩放，5 个手柄劈裂**：4 个角手柄停在拖动开始框、旋转手柄跟到
    实时框。B1 修好缩放漂移时**新造**的状态。契约：
    `tests/test_gui_canvas_drag_zoom_handle_consistency.py`（R4，strict xfail）。
    **注意**：该用例的判定码只比 x 轴 —— 它钉住的是「不要 1 对 4 分裂」这一条，
    不是完整的二维自洽。

#### E. 设计待定

27. **页操作不可撤销**：增/删/复制/重排页不进撤销栈（PRD 未要求，`_on_page_del`
    直接调 `self.doc.remove_page()`，没有 undo 命令）。故栈里旧命令的归属页可能
    变陈旧 —— 越界已被 `_PageCommand._run` 兜住（就地降级不抛），范围内错页时
    撤销静默失效：**按钮仍 enabled 且挂着命令真名，按下去模型与场景零变化**
    （第 18 条是它的新实例）。
28. **组的镜像语义未定义**（PRD FR-08 只定义 Item 级）：当前是**逐成员**各绕自身
    中心翻，不是整组刚性反射。若产品要后者需另立契约（组级镜像字段或成员 paths
    绕组中心重写），两者都会突破 v1.3「不补偿/仅标志」裁决。

#### F. 性能（纯交互，不改刀）

29. **组框判据从 O(#顶层容器) 变成 O(Σ子树点数)**（第 1 轮引入）。`_complete_groups`
    （`group_overlay.py:129`）对**每个**容器调 `iter_leaves([cont])`，而
    `iter_leaves` 搭在 `iter_flattens` 上、对子树里**每个 item 的每条折线**急切
    地做变换（`model.py:1273`）。**本轮实测**：30 组×10 叶×800 点（24 万点）
    文档上，单次判据 **49.5ms**（1.65ms/容器）。与已记账的缺口 25（缩放重扫）
    同类但位置不同（`group_overlay.py:129` vs `handles.py:150`），**无契约钉住**。
    第 3 轮已把「一次手势算 N 次」压成 1 次（`_expand_group_selection` 的
    `blockSignals`），但 **Ctrl+A 全选仍是裸循环**（`layout_page.py:1331`
    逐个 `gi.setSelected(True)`），在同规模下一次全选仍是秒级手势。

### 明确不做 / 待定

- **执行期 M114 轮询**：run_job 期间 LiveMarker 冻结置灰（无位置回报）——
  要做得改 worker `run_job` 发送/暂停/中止流控（零改动红线），明确不做；
  脱节对照靠停机读 M114/人眼复核（§6 硬件问题软件无法构造性解决）。
- **相机叠加 / Print&Cut 双点套准**（对准强制项已由 Frame/点检/设原点覆盖）。
- **G92 / WCS**：与「开串口即复位」冲突；工件原点现为编译期纯平移。
- **TiledPathItem**（上千段折线的 exposedRect 分块渲染）：接口预留，
  段数 >2000 实测需要才做。
- SVG 单位换算（px/in、viewBox 缩放）、越界自动裁剪（只拦截+报告）、
  橡皮筋/凸包 Frame、Word/Excel 样式保真 —— 均按蓝图 §9 明确不做。
- **跨页批量逐页导出**：与 `PRD_layout.md` §5 决策 5「单 SVG」冲突，按当前页
  导出保契约。
- 契约曾列「拖动的对象吸附（端点/中点/边）」「吸附 pitch 联动 grid_steps
  minor」「数值输入撤销合并（手势 token）」为缺口；**本树已实现**并有回归
  （`tests/test_gui_layout_window.py::test_drag_snaps_to_object_key_points`、
  `::test_snap_pitch_follows_zoom`、`::test_numeric_edit_merges_undo_per_gesture`；
  实现在 `canvas/snap.py`、`canvas/items.py`、`layout/layout_page.py`）——
  按「写事实」不列为缺口，若回归丢失再挂账。

### 契约本身的缺口（**测试钉不住的地方 —— 别拿这些绿灯当已验证**）

这一节是本轮对抗性复核里最该写下来的一类：测试全绿，但它钉的不是会出错的事。

1. **`test_gui_layout_bed_vacuum.py` 对真正做决定的那个函数失明**。该文件用 AST
   关口保证「把闸门接到 `doc.page.bed_*` 的改法必须让本文件报红」，但它只
   `inspect.getsource(LayoutPage._out_of_bed)`（`:205`）与
   `LayoutPage._on_save_svg`（`:237`）；而**发不发 `export_requested` 由
   `_confirm_in_bed`（`layout_page.py:2150`）的返回值决定** —— 它只被断言
   「`_on_save_svg` 调用了它」（`:243`），从不被调用、也从不被解析。把越界量
   改成按 `max(BED_W, page.bed_w)` 重算（完全不碰 `_out_of_bed`，默认 210 页时
   行为逐位不变）⇒ 该文件照绿。**判定函数一挪位置，整套关口失明。**
2. **`test_gui_layout_zorder_cut_order.py` 的「切割次序」断言是空的**。该文件的
   `_cut_order(doc)`（`:59-66`）先把 `flatten_visible` 的 x 收成**集合**，再按
   `iter_leaves(doc.items)` 的 DFS 序吐名字 ⇒ 返回的永远是**文档序**，`xs` 只起
   成员过滤作用，对次序**零敏感**。把场景 4 个图元的 z 全部取反，真切割序从
   `['X','M0','M1','M2']` 变成 `['M2','M1','M0','X']`，该助手仍返回
   `['X','M0','M1','M2']`。真正承重的是那些 z 字典比较。
3. **`test_gui_layout_order_vs_baseline.py` 的两条**。①
   `test_group_ungroup_does_not_decouple_order`（`:267`）写
   `a, c = doc.items[0], doc.items[1]`，而 `_fresh(*_ABC)` 建的是 `[a,b,c]`
   ⇒ 变量名叫 `c` 的那个握着 **b**，编的是**相邻**的 `[a,b]`；相邻对的 DFS 先序
   本来就稳定，所以把 tie-break 退回 `key=z`（A4 之前）时它**照样绿** ⇒ 它
   钉不住 A4 真正依赖的**非相邻**并列 z。②
   `test_decoupling_never_pushes_geometry_outside_the_bed` 的越床那半段
   （`:195-201`）把要移动的 `wide` 构造成**末位** ⇒ `remove + add` 是空操作 ⇒
   `gate(out_doc) == out_before` 恒真，钉不住「越界判定与次序无关」。
   另：该文件模块 docstring 写「今天没有 UI 可达路径：生产里 undo 侧一律走
   `attach(owner, index)`（原位复原、不脱钩）」——**这半句不成立**：编组的撤销
   走 `Document.ungroup`（`undo_cmds.py:553`），它用**递增下标**回插
   （`model.py:889` 的 `Page.ungroup` 自己写了「非相邻成员编组再解组 ⇒ 顶层序不可复原」），
   所以「工具条编组 → Ctrl+Z」就能造出与该文件 `_decoupled()` 同形的脱钩文档。
4. **`test_gui_layout_toolbar_feedback.py` 的「整条工具条不再有静默按钮」是假的**。
   它的 parametrize 挂在一份**手写字典** `_TOOLBAR_GUARDS`（`:61-70`）上，
   测试从不读 `_build_toolbar`；方向只有 `guards ⊆ toolbar`，**没有**
   `toolbar ⊆ guards`。运行期枚举工具条有 **32 个 QToolButton**，字典只覆盖
   **8 个** ⇒ 其余 24 个（＋/－/复制/◀/▶/六个工具/吸附/四个导入/镜像×2/编组/
   解组/四个缩放…）不受任何「必须出声」约束。
5. **`model.py` 有四处 + 两份既有测试仍**无条件**声称「对无 children 的文档逐位
   不变」**（`model.py:42`、`:214`、`:1331`、`:1417`；
   `test_gui_layout_bed_enum.py:359/377`、
   `test_gui_layout_bed_enum_structural.py:281`）。但「无 children」只是**必要**
   条件，不是充分条件 —— 零 children 的扁平文档同样可以 `order ≠ 列表序`（已编号
   的对象被 `remove` 后用 `doc.add` 重新入模），此时枚举面与切割序**分叉**
   （几何多重集守恒，只有次序变）。这与
   `tests/test_gui_layout_order_vs_baseline.py` 新写的「只在 order == 列表位置
   时成立」**互相矛盾**；照现存表述去「修」会删掉 A4 依赖的 tie-break。
   生产 undo 侧走 `attach(owner, index)` 原位回插 ⇒ **今天无 UI 可达路径**，
   属文档层不一致，不是现网 bug。
6. **`test_gui_layout_status_visibility.py` 只钉「有个 QLabel、文本对、
   `isVisible()` 为 True」**。`isVisible()` 对一个被 clamp 成 20px 宽的控件同样
   返回 True（探针实测：192px 的中文塞进 20px 框，`isVisible()` 仍 True），
   `.text()` 与实际是否被裁掉毫无关系 ⇒ 缺口 16/17 它一条都抓不住。
7. **`test_gui_layout_image_spec_predicate.py` 的不变量把 `retrace_image_item`
   打成桩** ⇒ retrace 函数体零执行；文件 docstring 写的「真的触发重追**或
   报错**」那半边从未被检查（唯一正例 fixture 真跑时兑现的是「无法重追」）。
8. **本会话新增的 docstring-only 交付**（`layout_page.py` 的 `_out_of_bed` 口径注、
   `to_job_spec` 的 order 说明）在修复前的源码树上**同样全绿** —— 它们的回归文件
   钉的是早已成立的现状，与那两段散文无实质耦合。按本轮「测试在修复前必须失败」
   的规矩，这类交付不满足条件，写清楚是为了让下一个人别把它们当成已验证。

2026-09-29：**交付收口 16 轮**（`0f160c3..67883e8`）。前 12 轮的记录见
`REPORT.md` §14；第 6–16 轮（`0f9a542`..`67883e8`）补记见 `REPORT.md` §15。
- **真修好的**（本轮销账的 7 条见上文「已销账」）：入档口拒绝「组自带折线」、
  `_page_box` 走真祖先链、排版页常驻提示行、工具条 8 个按钮的选择不足提示、
  `image_spec` 判据与消费者统一、`_set_job_and_recompile` 原子回滚、
  组套组每层各画一个框。
- **写成契约但未修的**（4 组 `pytest.mark.xfail(strict=True)`，修好即 XPASS 报红
  逼人摘标记）：`attach_reparent`(R1 双父 DAG)、`tree_writer_enumeration`(R2 枚举
  文本)、`zoom_geometry_rescan`(R3 缩放白做重扫)、`drag_zoom_handle_consistency`
  (R4 手柄 1 对 4 分裂)。本轮确认 4 个标记仍在
  （`grep -rn xfail` 于四个文件均有命中）。
- **修好一条、同时带出一条新的**：`_page_box` 祖先链（缺口 12）修好读侧后，
  写侧（`_apply_size`/`_apply_scale`/`_align` 写 `it.pos`）仍是局部系 ⇒ 组编辑态
  一次改宽把整组瞬移 105mm（上面 A-2）；`_set_job_and_recompile`（第 13 轮）修好
  状态原子性后，把 `_recompile` 的**执行中 no-op** 也当成失败 ⇒ 放置模式改动
  被丢弃（A-8）。**两条都是本轮新写的代码引入的**。

### 需现场（不计 pytest，缺任一条件就无法判定）

- Frame 与预览 bbox 目视一致；设原点后落笔对准 <1mm（人眼）；空跑全程不触纸。
  条件：机器在位、笔已装、纸已铺、能看纸面。
- 贴纸镜像的翻面转印效果（US-3）。条件：需真刻一张。
- Canny 照片/素描在**真实照片**上的参数调优（当前 low=50/high=120/aperture=3
  是用测试内合成样张定的基线）。条件：需真实照片样张。
- **本轮 29 条缺口的用户可观测后果全部只用 offscreen Qt 模拟层取证，未上真机**
  （有串口、有电机、有纸面的只有 §机器事实 那几条）。具体待验：
  - **A-1/A-5/A-6/A-7/A-8（作业页滞留、编辑丢弃、锚点绕过越床、锚点搬走图元、
    放置被丢弃）**：需一次完整作业执行 + 作业期间切排版页交互 + 串口回包。
    待验的是**刀实际落在哪**、画布上的内容与 G-code 是否一致。
  - **A-2/A-3（组编辑态瞬移、画布≠导出）**：需拿一张**几何不同**的图元
    （不同长度/端点）在纸面上肉眼比对，本轮只验到坐标层面。
  - **A-4（置底次序）**：同上，需纸面证据；本轮只有 z 序与 `flatten_visible` 序。
  - **A-9（撤销往返）**：需肉眼确认幽灵空容器会不会进下一刀的 G-code（本轮只
    验到 `flatten_visible` 归 0、两个空壳仍在模型里）。
  - **B-16（提示行裁切）**：需在**真实显示器 DPI / 真实字体**下看那条提示行。
    本轮测的是原生 Qt 字体度量（`Microsoft YaHei UI 9pt`，CJK 12px/字），
    不是真机窗口。

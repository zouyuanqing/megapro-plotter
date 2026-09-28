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

## Current state / next steps (2026-09-23)

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

## 已知缺口 / 后续（2026-09-29 更新；按「确切症状 + 触发条件」写）

### 已销账（**旧文案作废，勿再照抄**）

- ~~**越界预检只警告不拦截**~~ —— **已修**。`layout_page._confirm_in_bed`
  （`layout_page.py:1954`）是「送去执行」与「另存为 SVG」**共用的唯一越床闸**：
  床内直接过；越界弹 Critical 框、「取消」是默认按钮、**关窗等同取消**，
  只有显式点「忽略并继续」才放行。`_on_export`（`:2012`）在 emit **之前**
  return ⇒ 越界几何绝不静默送去动刀。判据 `_out_of_bed`（`:1930`）走
  `iter_units(visible_only=True)` + `unit_page_bbox`（含祖先链）、床面取
  `BED_W/BED_H` 常量（不硬编码 210）。回归 `tests/test_gui_layout_bounds_gate.py`。
  **遗留**：「另存为 SVG」这个纯写文件、不动刀的动作也被同一道闸拦（见下「误拦」条）。
- ~~**撤销/重做按钮文案不刷新**~~ —— **不成立，症状不存在**。工具条按钮是
  `QUndoStack.createUndoAction` 建的 `QUndoAction`，Qt 每次 push/undo/redo/clear
  自动重写文案与可用态；`layout_page.py:562` 的 `_refresh_undo_actions` 只在构造时
  调一次且当时栈为空（`undoText()==''`），是**空转的死代码**。
  `tests/test_gui_layout_undo_actions.py` 6 条钉住文案/可用态。
- ~~**床尺寸在 5 条页操作路径上可能脱节**~~ —— **当前设计下不可表达**。
  `Document.bed_w/bed_h` 改成直通当前页的 property（`model.py:996-1012`），
  床只有一处存储，恒等式从「纪律」变成结构事实。
  回归 `tests/test_gui_layout_bed_enum_structural.py`（含「bed 不得变回
  dataclass 字段」「门面是活读」「两个 Document 共用一个 Page 看到同一份床」）。

### 仍然存在（按对机器的后果排序）

1. **执行中删当前页 ⇒ 作业页永久握着已删页几何（最严重，会切错位置）**。
   症状：作业跑着，切到排版页点「－」删掉当前页 —— 画布当场换成存活页，
   作业页仍握**已删页**的 `JobSpec`；控制台只打一句「执行中，参数改动本次作业
   结束后生效」，`jobDone` 的补编译用的还是那份没被换过的 spec ⇒ **作业页永远
   回不来**，再点执行，机器收到的是模型里已不存在的坐标。实测（offscreen，真
   `run_btn.click()`）：删页后画布 x=[0,10] / 作业页 x=[100,140]，
   `jobDone` 后仍是 [100,140]，`run_btn` enabled。根因链：`_refresh_page_bar`
   在 `blockSignals(True)` 里重置页签 → 后续 `setCurrentIndex` 同值不发信号 →
   `_on_page_del` 的 `_maybe_emit_job_sync` 走的是**执行中门禁**
   （`main_window.py:1603-1605`，在 `self._job_spec = spec` 赋值**之前** return）。
   非执行期同一路径已修好（作业页确实跟到存活页）。
2. **执行中的几何编辑被永久丢弃，且提示文案说谎**。症状：作业跑着在排版页加一条
   线 → 控制台提示「本次作业结束后生效」→ 跑完 → 作业页仍是旧几何、旧位置，
   `run_btn` 仍亮，**再点一次执行，机器切的是旧内容旧位置**（实测 worker 实收
   `G0 X100 Y100 / G1 Z17 / G1 X140 Y140`，画布上新增的线一次都没出现）。
   这句文案对**参数**改动成立（`_recompile → _spec_with_current_params` 会重读
   控件值），对**几何**不成立 —— 而 `main_window.py:1592-1595` 的 docstring 写
   下的缓解手段「用户下次切页即重新同步」**不成立**（`self.tabs` 没有
   `currentChanged` 接线，实测作业↔排版往返不触发同步）。
3. **编组/解组撤销栈往返后文档错位 + 幽灵空容器**。症状：编组 → 解组 → 一路撤到底
   → 重做回顶，`_undo_act.text()` 写着「撤销 解组」但文档是**编组态**；再按一次
   Ctrl+Z，顶层凭空多出一个**同名空容器**；一路撤到底折线归 0 而文档里留着两个
   空壳。实测结构轨迹：`['组3[a,b,c]'] → ['组3', '组3[a,b,c]'] → ['组3','组3']`，
   末态 `flatten_visible` 0 条。根因：`GroupCommand._do_redo`
   （`undo_cmds.py:541`）每次 redo 都 `doc.group_items()` **新建**容器并覆盖
   `self.container`，而 `UngroupCommand.__init__`（`:585`）只捕获**一次**容器
   对象 ⇒ redo 拿到的是已被掏空的旧壳，`ungroup()` 返回 `[]` ⇒ 静默空转。
   数据未被破坏（A1 不变量全绿），但**撤销栈从此不再描述文档**。
4. **「置于底层」在选中 ≥2 个图元时与未选中图元同 z，切割次序被劈开**。
   症状：4 个散件（画线默认 z=1,2,3,4），选中后两条按「置于底层」⇒
   M0=0、M1=1，而未选中的 X=1 **与 M1 同 z**；`flatten_visible` 的 tie 由 `order`
   二次键决定 ⇒ 切割序变成 M0, **X**, M1, Y —— 未选中的 X 被切在两个选中件中间。
   根因：`layout_page.py:1382` 的 `cursor = self.doc.bottom_z() - 1` 起手、
   `:1399` 的 `cursor += 1` **只增不减** ⇒ 选 n 个叶子占
   `[bottom-1, bottom-1+n-1]`，n≥2 时必然爬回已占用区。**置顶安全**（游标向上，
   必在全体之上），洞只在置底。
5. **同一图元可被挂到两个父下 ⇒ 同一几何切两遍**（模型层，无守卫）。
   `Page.attach(owner=...)` 的守卫判的是「item 与 owner 互为后代」，**从不检查
   item 在 owner 子树之外是否已有归属**（`model.py:708-715`）；`Document.add`
   （`model.py:1065`）与顶层 `attach(owner=None)`（`model.py:699-707`）**刻意**
   不装守卫（`model.py:680-685` docstring 明写，且被
   `tests/test_gui_layout.py::test_attach_restores_group_structure_exactly` 以
   「错误路径复现」钉死）。可达性按本轮评审记录复核：全量套件跑下来生产栈帧触发
   **0 次**、20 条对抗序列 0 次 ⇒ **今天没有 UI 可达路径**，属潜在口子。
   契约由 3 条 `pytest.mark.xfail(strict=True)` 钉着，修好即报红逼人摘标记：
   `tests/test_gui_layout_attach_reparent.py`（R1）、
   `tests/test_gui_layout_tree_writer_enumeration.py`（R2）。
6. **带自身折线的容器：导出切、画布不画**。`make_gi`（`undo_cmds.py:79`）对任何
   容器一律 `return None`，而 `iter_flattens` 把容器自身 `paths` 当一等几何拍平
   ⇒ 画布上什么都没有（选不中、拖不动、画不出），但**照样进 JobSpec、照样切**。
   可达路径：`_paste`（`layout_page.py:1264`）对剪贴板文本只做 `json.loads`
   零校验，`_item_from_json`（`:213`）**同时**还原 `paths` 与 `children` ⇒
   Ctrl+V 一段 JSON 就能造。实测：容器 C 自带 `[(5,5)-(305,5)]`，`flatten_visible`
   出 2 条含 x=305（床宽 210），`_out_of_bed` 报超出 95.0mm，而场景里只有 `kid`。
7. **剪贴板可灌入超深模型树，导出链与撤销双失**。`_item_from_json` 是纯递归还原、
   **无深度上限**，`_check_depth`（`MAX_TREE_DEPTH=64`）只在**遍历时**才炸。
   实测边界：`flatten_visible` 在**深度 64 就已抛** `ValueError: 模型树深度超过 64`
   （`iter_items` 比其余入口多下探一层，导出口径的安全线是 **63**）；粘贴 ≥65 层
   还会让 undo 命令的 redo 被 PySide6 吞成 stderr 噪声，栈说已撤销而模型里那棵树
   还在，只能重开文档。
8. **越床闸口径比「真正送去切的」保守 ⇒ 只误拦、不会漏拦**。`_out_of_bed` 走
   `unit_page_bbox`（不过滤可见性、不丢弃 <2 点折线），`flatten_visible` 两者都过滤。
   实测两例：组内**隐藏**件停在 (260,260)-(290,290) ⇒ `flatten_visible` 只有那条
   全床内的矩形，闸门却报「超出 80.0mm」；单点折线 `[(30,213.4)]` ⇒ 拍平直接丢弃，
   闸门仍报「超出 3.4mm」。方向是**保守超集**（100% 不一致都是误拦、漏拦恒为 0），
   所以是假警报 + 无谓堵死导出（含纯写文件的「另存为 SVG」），**不是安全洞**。
9. **界面文案比实现说得绝对**。三处（均已实测）：
   - 宽/高 tooltip 写「旋转或镜像后 AABB 会变…数字跳变属正常」，但 0°/90°/180°
     与轴对齐形状镜像后 AABB **逐位不变**（`model.py:101-108` 的权威条件是
     「仅当 angle_deg 为 90° 整数倍时守恒」）；angle=0 是所有新建/导入图元的默认角
   - 镜像按钮 tooltip 写「旋转件的宽/高会随之变化」，同样无条件
   - 宽/高 spinbox 的 range 是 `(-1000, 1000)`，`_apply_size`（`layout_page.py:1139`）
     对 `value <= 0` **静默 return**：键入 `-10` 后面板显示 `-10.0` 而真实 AABB
     宽仍是 10.0、scale 仍是 1.0，属性通路**没有任何消息通道**（不回弹、不报错）
10. **缩放路径白做的几何全量重扫**（`handles.py:150` → `_relayout_rotate` →
    `selection_rect()`）。`selection_rect()` 是**缩放不变量**（23 次读数只有 1 个
    取值、逐位相等），代价是 O(选中集总点数) 的纯 Python 循环。实测本机越 16.7ms
    帧预算的规模在**约 24 万点**（300 项×800 点），不是小规模。契约：
    `tests/test_gui_canvas_zoom_geometry_rescan.py`（R3，strict xfail）。
11. **拖动未提交时缩放，5 个手柄劈裂**：4 个角手柄停在拖动开始框、旋转手柄跟到
    实时框（屏幕间距变成 −103.97px）。B1 修好缩放漂移时**新造**的状态（修前 5 个
    手柄一致地陈旧、视觉自洽）。契约：
    `tests/test_gui_canvas_drag_zoom_handle_consistency.py`（R4，strict xfail）。
    **注意**：该用例的判定码只比 x 轴，y 轴方向上「坏代码判绿 / 好代码判红」同时
    成立 —— 它钉住的是「不要 1 对 4 分裂」这一条，不是完整的二维自洽。
12. **`_page_box` 丢祖先链（潜伏）**。`layout_page.py:361-370` 遍历从 `[item]`
    自身起步，第一次迭代就命中 `it is item` ⇒ `chain` 恒为 `()`，`unit_page_bbox(())`
    与 `page_bbox()` 逐位相同。实测：外层容器 `pos=(100,0)` 时，内层组 `_page_box`
    给 (0,0,10,20) 而真值是 (100,0,110,20)（偏 100mm）。今天容器的祖先链恒为恒等
    链（`group_items`/`wrap_group` 都造恒等容器，且没有任何 UI 通道给容器写
    pos/scale/angle）⇒ **数值上无差别，潜伏非现网 bug**。同一个入口也是
    `_unit_bbox`（对齐/分布）、属性面板 X/Y、等比缩放 k 的唯一来源。
13. **`group_overlay` 不做身份去重**（`group_overlay.py:125-132`）：容器一旦同时
    出现在顶层和某个组的 children 下（缺陷 5 的形状），同一容器会画两个完全重合的框。
    与缺陷 5 同源、同为潜伏。
14. **locked 成员的组永远画不出框，但整组照样切**。组判据是「子树叶子闭包 ⊆ 当前
    叶子选择集」，而 `locked` 图元 `ItemIsSelectable=False` 永远进不了选择集 ⇒
    `flatten_visible` 又不看 locked。实测：a、b 编组、b 锁定、点 a ⇒ 0 个框，
    `to_job_spec` 仍出 2 条折线。**当前 UI 没有任何设置 locked 的控件**，只能由
    带 `"locked": true` 的剪贴板/版面 JSON 造出 ⇒ 需外部载荷。
15. **页操作不可撤销**：增/删/复制/重排页不进撤销栈（PRD 未要求）。故栈里旧命令的
    归属页可能变陈旧 —— 越界已被 `_PageCommand._run` 兜住（就地降级不抛），范围内
    错页时撤销静默失效：**按钮仍 enabled 且挂着命令真名，按下去模型与场景零变化**，
    用户无法区分「没东西可撤」和「这条撤了等于没撤」。
16. **组的镜像语义未定义**（PRD FR-08 只定义 Item 级）：当前是**逐成员**各绕自身
    中心翻，不是整组刚性反射。若产品要后者需另立契约（组级镜像字段或成员 paths
    绕组中心重写），两者都会突破 v1.3「不补偿/仅标志」裁决。

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

2026-09-29：**交付收口 12 轮**（`0f160c3..6cd513d`，20 个文件 +5648/−109，
红线路径与 `test_gui_layout_window.py` / `test_coords.py` 零 diff）。
- **真修好的**：越床导出闸改成**默认拒绝**（`_confirm_in_bed` 共用闸，取消/关窗
  都不 emit）；编组嵌套判据（`_complete_groups` 走 `iter_units` + 叶子传递闭包）；
  手柄尺寸/旋转手柄间距随视口缩放即时跟上（`handles.py:90` 接 `viewChanged`）；
  排版页四类编辑 + 页操作（增/删/复制/重排）全部接作业预览同步；层序基准移到
  循环外用游标递推；镜像/镜像后 AABB/编组镜像语义/双击进组/Canny 阈值校验的
  中文提示；`group_items` 成员归属校验（跨页/悬空整单拒绝）；床尺寸收敛为
  当前页 property。
- **写成契约但未修的**（4 组 `pytest.mark.xfail(strict=True)`，修好即 XPASS 报红
  逼人摘标记）：`attach_reparent`(R1 双父 DAG)、`tree_writer_enumeration`(R2 枚举
  文本)、`zoom_geometry_rescan`(R3 缩放白做重扫)、`drag_zoom_handle_consistency`
  (R4 手柄 1 对 4 分裂)。
- **仍然是缺口且会动刀的四条**：执行中删页/编辑导致作业页握着已删页或旧几何、
  编组/解组撤销往返后文档错位、置底与未选中图元同 z 劈开切割次序、模型层双父
  与带自身折线的容器（切两遍 / 看不见但会切）。逐条症状与触发条件见下方
  「已知缺口」。**详情见 `REPORT.md` 的 2026-09-29 收口记录。**

### 需现场（不计 pytest，缺任一条件就无法判定）

- Frame 与预览 bbox 目视一致；设原点后落笔对准 <1mm（人眼）；空跑全程不触纸。
  条件：机器在位、笔已装、纸已铺、能看纸面。
- 贴纸镜像的翻面转印效果（US-3）。条件：需真刻一张。
- Canny 照片/素描在**真实照片**上的参数调优（当前 low=50/high=120/aperture=3
  是用测试内合成样张定的基线）。条件：需真实照片样张。
- 缺陷 1/2/3/4 的用户可观测后果（作业页滞留、编辑丢弃、撤销错位、切割次序）
  本轮全部只用 offscreen Qt 模拟层取证，**未上真机**。上真机需要的额外条件：
  一次完整作业执行 + 排版页交互 + 串口。

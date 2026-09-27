# Mega Pro 写字机项目 — 交接报告（给下一个 Agent）

日期：2026-09-06。状态：软件 33/33 绿；真机联机已通但存在**未解决**的偶发“有应答无动作”问题。

## 1. 环境与仓库

- 工作区 `D:\mcu_prj1`（git 已 init，未 push，无 remote）。
- Python 3.14.4，pyserial 3.5，pytest 9.0.3，PySide6 6.11.2（已装好，图形界面开工无障碍）。
- 打印机：Anycubic Mega Pro（Trigorilla 系，ATmega2560），USB 口是 **COM7（CP210x，不是 CH340）**。
- 依赖只有 `requirements-m0.txt`（pyserial==3.5）。

## 2. 已证实的固件结论（实测，非推测）

| 项目 | 值 | 来源 |
|---|---|---|
| 固件 | Marlin 1.1.0-RC8，V1.2.9，Compiled Jun 18 2020 | M115 实测 |
| 波特率 | 250000（115200 为 fallback） | 实测连通 |
| 步进 | X80 Y80 Z400 E384 | M503 实测 |
| 行程 | 210×210×205 | 官网 + 实测一致 |
| 温度 | 常温待机 T~21/B~20，加热从未开启 | M105 实测 |
| 限位 | x_min/y_min/z_min/z2_min，归位后 XY 为 TRIGGERED（正常） | M119 实测 |
| SD 卡 | `SD init fail`（卡槽无卡或损坏，只能走 USB） | 开机自报 |
| 激光 | 出厂 G6/G5 自定义，未动，封存 | 文献 + 未触碰 |

## 3. 串口行为结论（都是坑，都有证据）

1. **开串口主板必重启**：DTR 边沿触发复位，每次打开丢坐标（M114 归零）。缓解：打开后立刻 `dtr=False, rts=False`（`open_link` 已做）。
2. **冷启动刷屏约 16 秒**：开机后倒出 Báo cáo 设置直到第一个 `ok`，短超时必失败。缓解：`settle_link()`（`transport/marlin_serial.py`）排空到首个 ok 或安静 8 秒退出，永不致命。
3. **归位/长移动超时**：G28 全程需 60 秒，单步 30 秒（`HOME_TIMEOUT`/`MOVE_TIMEOUT`，`cli/main.py`）。
4. **软限位钳制**：复位后固件坐标为 0，未归位时负方向相对移动被钳为不动但回 `ok`。教训：复位后先 G28 再用**绝对坐标**走。
5. **安全门**：加热/激光（M104/M109/M140/M190/M3/M4/M5/G6/G5）默认拒发；Z 归位与负 Z 需 `--allow-z`；文件发送的负 Z 还需 profile 有 `pen_down_z`；XY 点动/`move` 自动先抬 5mm 并保持；`home` 命令归位前自动抬 10mm；`jog --draw`/`move --draw` 落笔直走。
6. **单会话通道**：`console` 子命令一次打开多条指令，无中间重启，坐标保持（标定必须走它）。

## 4. 标定数据（profile 同步；2026-09-07 两轮真机实测后更新）

- 安全点：X0 Y50 Z30（`profiles/mega-pro-marlin.yaml`）。
- 落笔高度（实测）：触纸 = 绝对 **Z17**（Z30 安全点下 13mm），
  `pen_down_z: 17.0`。两轮（09-06 中止轮、09-07 成功轮）触纸高度一致，
  笔高稳定。
- **笔架为摩擦固定、无弹簧**（旧文档写"弹簧笔夹"已过时；曾撞歪扶正）。
  测试时建议固定笔即此摩擦夹，下降须防撞。
- 纸已铺，笔已装。

## 5. 已修 bug 清单（全有回归测试）

- `--port` 写子命令后不认 → 全子命令通用。
- `open_session` 元组未拆包（5 处）。
- 单波特率 → 自动扫 250000/115200。
- probe 固件名取到 "ok" → 逐命令抓回显。
- transform rotate/skew 静默忽略 → 完整实现 + 非法抛错。
- 归位 5 秒超时 → 60/30 秒分级。
- `console` 显示回显正文（M114 可查坐标）。

## 6. 未解决问题（首要）：偶发有应答无动作

现象：指令全部回 `ok`，固件 M114 坐标跟着变，但用户现场确认电机没转。归位（G28）此前多次可见动作；G0/G1 在某次撞笔后开始出现无动作。
已排除：链路（探测一次通过）、波特率（8 个扫过）、指令合法性（M114 坐标更新证明固件执行）。
怀疑方向（按可能性排序）：
1. 步进电机供电/驱动过热保护（撞笔堵转后 A4988 过热关断，待冷却自恢复；查电源灯、驱动散热）。
2. DTR 复位竞态（每次打开都重启，位置记忆丢失下的相对/绝对混用）。
3. Z 软限位钳制（复位后坐标 0，负向相对移动静默不动——已用“归位后走绝对”规避，但历史误操作可能留隐患）。
复现：`console` 进 `M114 → G0 Y10 → M114`，看坐标变而机器不动即中招。
现场待确认（需人眼）：屏幕亮否、风扇转否、电源开关状态、笔尖伸出量。

### 6.1 两轮真机实测（2026-09-06/07，见 `logs/pen_trial_2026*.md`）

- **09-06 首轮：复现于回程绝对移动。** 序列 寻零→(0,50,30)→触纸 Z17→画 X20→抬 Z5→
  `G0 X0 Y50` 回安全点。画线正常（一条 ~20mm 线）；**回程后固件 M114 报 X20 Y100**
  （命令是回 X0Y50），用户确认笔离起点很远 → Y 轴逻辑坐标与物理失步。随即 M112
  `Printer halted` 停机、断电重启。
- **09-07 重跑：未复现，序列完整成功。** 同一序列、同笔同高，回程 `G0 X0 Y50`
  干净执行，终态 M114 `X0 Y50 Z22 / Count X0 Y4000 Z8800` 全部一致，纸上一条线。
- **结论：** 该故障为偶发（两轮一次），触发点疑似 Y 轴**回程绝对移动**（非画线
  过程）。固件不自知（回 ok + 错 M114），需人眼/影像确认。软件层无稳定复现，
  仍指向 §6 硬件怀疑（步进供电/驱动/皮带），未排除软件路径偶发触发。

### 6.2 M114 `Count` 字段不可靠（勿作丢步判据）

两轮均实测：逻辑 Z17 时 `Count Z 8802`（应 6800）；逻辑 Y50 时 `Count Y 1495`
（应 4000），随后在无关移动（画 X 线，Y 未动）后**自愈跳回 4000**。此固件
(Marlin 1.1.0-RC8) 的 `Count` 会瞬时与逻辑坐标失同步。判据优先级：
**人眼物理 > 逻辑坐标 (M114 X/Y/Z) > Count**。诊断/自动化勿用 Count 判丢步。

## 7. 验证过的命令序列（标定时用 console 管道单会话）

```
G91 / G0 Z10 F300 / G90 / G28 X Y / G28 Z / G91 /
G0 Z40 F600 / G0 Y50 F600 / G0 Z-16 F600 / G0 X10 F600 / G0 Z10 F600
```

注意：G28 Z 会落到限位高度，笔长时先确认弹簧余量。

## 8. 测试

`python -m pytest -q`：33 项（传输/安全 7、工具链 6、transform 回归 2、CLI 回归 9、相机 2、端到端集成 2、settle 3），全绿。集成测试用本机 TCP 模拟 Marlin 跑探测建档到文件发送全链。

## 9. 下一步建议

1. 现场确认电源/屏幕/风扇，排除电机供电问题（最可能）。
2. 相机转回对准打印机（现被碰歪对着窗户），恢复抽帧确认。
3. 重标落笔高度（2mm 步进，用户报数），回填 `pen_down_z`。
4. 方块空跑 → 写字 → 拖刀（按 `Verification` 章节 V0→V2）。

---

# 2026-09-23 重构记录（预览与排版，阶段 1–5 全部落地）

契约：`docs/preview-layout-blueprint.md`（§2.2 模块清单/退役表、§2.3 根因
对照、§7 阶段 1–5、§8 测试策略）。以下为落地事实与验证结果。

## 10.1 十根因 → 修复对照（均有回归测试）

| # | 根因（原审计） | 修复落点 | 回归 |
|---|---|---|---|
| 1 | 工件原点载入时烘入、设原点不重编译 | `JobSpec.work_origin` 编译期纯平移 + `main_window._recompile()` 全触发集（含工具切换/Z 标定/预设；执行中 no-op、jobDone 补编译） | `test_gui_window.py::test_job_set_clear_origin_recompiles_lines_and_preview`、`::test_job_recompile_noop_while_running_backfills_after_done` |
| 2 | `already_paper` 双入口 + 改参数重读已删临时文件 | 删双入口；JobSpec 内存唯一源；排版→作业 `JobSpec` 直传（无 tempfile 往返） | `::test_job_param_changes_never_reread_file`、`::test_job_layout_export_keeps_source_and_param_changes_ok` |
| 3 | paint `(x,−y)` 与 model CCW 互为镜像 | 场景≡纸面 mm y-up，PathItem 直画、支点=本地原点 | `test_gui_layout_window.py::test_pathitem_matches_model_transform`（θ=30/90、scale≠1） |
| 4 | `flip_y` 按床高 210 整体反射、锚点错乱 | `Placement`（preserve / anchor 九宫格）+ 预览画 bbox/锚点/三框 | `test_coords.py::test_place_at_anchor`、`test_gui_window` 载入用例 |
| 5 | 首段漏定位 G0、裁刀在错误 XY 先下压 | emitter `cur=None`：首段必发定位 G0 且下压在其后 | `test_gui_job.py::test_first_segment_positioning_g0`、`::test_knife_plunge_after_positioning` |
| 6 | 非零角度预览反向 | 同 #3；`gi.mapToScene ≡ model.transformed_paths` golden | 同 #3 |
| 7 | Word/Excel 漏翻 + 另存回读再翻 | 组导入契约 `_import_group`（整组翻→组级一次归位→逐 Item 归一）；导出 y-down 对合 | `test_gui_doc_import.py` 行序断言、`test_gui_layout.py::test_export_svg_ydown_roundtrip` |
| 8 | 拖动不回写 model、undo 误合并 | 拖动 mouseRelease → 手势 token `MoveItemsCommand`（命令写 model，多选整组一条命令） | `test_gui_layout_window.py::test_drag_writes_back_model_with_gesture_token`、`::test_two_drags_two_independent_undo_commands`、`::test_multi_drag_writes_back_whole_group` |
| 9 | 竖标尺 0 点恒偏一个床高（label L 实落 paper 210+L） | `RulerWidget` 用 `view_from_mm` 浮点、0 刻度对齐 paper y=0 | `::test_ruler_ticks_at_paper_positions`（只断言修复后位置） |
| 10 | 数值定位/吸附与几何脱节、多选塌缩、高不生效 | `normalize_local` + 九宫格锚点数值定位 + 多选按选择集 bbox + W/H 联动 + `SnapEngine`（网格 pitch=grid_steps minor，对象吸附端点/中点/边） | `::test_multi_select_move_no_collapse`、`::test_sp_h_scales_item`、`::test_grid_snap_draw_tool_and_item`、`::test_drag_snaps_to_object_key_points`、`::test_snap_pitch_follows_zoom`、`::test_numeric_edit_merges_undo_per_gesture` |

## 10.2 阶段 1–5 概要

- **阶段 1 纯逻辑地基**：`gui/canvas/coords.py`（唯一坐标/翻转权威）、
  `gui/gcode_parse.py`（lines→Segment）、`job.py`（JobSpec/compile_job/
  check_bounds_v2/ZMap）、`controller.py`（Frame/MoveTo/GotoOrigin/
  can_start_job）。
- **阶段 2 画布核心 + 排版 y-up**：`gui/canvas/{view_transform,paper_scene,
  paper_view,rulers,items,handles,snap,undo_cmds}.py`；`layout/{model,
  layout_page,doc_import,text_to_svg}.py` 组导入契约/拖动回写/锚点定位；
  删 `layout/{items,canvas,undo}.py`。
- **阶段 3 导出链统一 + 预览≡发送**：`export_svg` y-down 对合、排版
  `JobSpec` 直传、`main_window` 单一 `_recompile()` 汇流 + PaperView/
  GcodePathItem/LiveMarker、删 `already_paper`/`_draw_preview`/tempfile
  往返/`job.flip_y` shim。
- **阶段 4 对准工具 + homed 门禁**：worker `sequenceDone(bool)`（入口早退
  也 emit False）、对准工具条、`can_start_job` 接线、`_send` 预检 allow_z
  传 UI 真值、`_refresh_gate_ui` 统一使能（DISCONNECTED/未勾允许 Z 的 Z
  动作禁用 + 中文原因；`self._allow_z` 死字段删除）。
- **阶段 5（2026-09-23，本记录）收尾**：
  1. **退役**：删 `toolchain.emit_gcode`/`svg_file_to_gcode`（连带死代码
     `_fmt`）；`preview/to_svg.py` 的 `toolpath_to_svg`（polylines 入口）改
     `preview_svg_from_segments(segments, *, work_origin)`（吃
     `gcode_parse.Segment`，三色 + 工件原点十字）；`test_toolchain.py` 的
     `test_emit_allowed_codes_and_guard`/`test_emit_pen_cycle` 随退役删除，
     `test_preview_writes_svg` 改喂 Segment。
  2. **常量收敛（§8.4）**：`BED_W/BED_H` 数值字面全 src 只留
     `canvas/coords.py` 定义处——`main_window` 材料表/默认参、`presets.py`、
     `preview/to_svg.py` 全部改引常量；`edge_to_svg/image_to_svg` 的灰阶
     阈值 (90,130,170,210) 为同数异义（灰度值非床尺寸），改等差式
     `GRAY_THRESHOLDS = tuple(90 + 40 * k for k in range(4))`，取值不变；
     §8.4 白名单清空（`tests/test_coords.py::test_bed_210_literal_convergence`）。
  3. **债务销账 C1–C8**：C1 `test_gui_job.py` 的 `paper_to_svg_ydown as
     flip_y` 别名改正名、`test_flip_y_global`→`test_paper_to_svg_ydown_global`
     （语义等价）；C2 `layout/model.py` 的 sys.modules 旧模块别名块与
     `test_legacy_canvas_alias` 经核查已在此前阶段删除（断言对象已退役），
     本次另清 `snap.py` 引用该别名的过期文案；C3 `test_export_svg_ydown_roundtrip`
     经核查已在 `tests/test_gui_layout.py:132`（对合还原）；C4
     `_on_check_point` 的 MachineError 兜底经核查已在
     `main_window.py:1658`（与 _on_frame/_on_goto_origin 一致）；C5
     `LiveMarker.set_machine` 冻结态迟到回报改为直接丢弃（不点亮置灰虚
     十字、不位移；解冻只由 `set_stale(False)`）；C6 `send_sequence` 入口
     早退 emit `sequenceDone(False)` 经核查已在 `worker.py:222-224`（与
     docstring 一致）；C7 home/jog/笔/发送按钮统一使能刷新经核查已在
     `_refresh_gate_ui`（main_window.py:1342），`self._allow_z` 死字段已删；
     C8 三处 docstring 与实现统一（`check_bounds_v2` 判定区
     `min(travel,material)−margin` + A4 例、`gcode_parse` 未定轴 0.0 占位
     规则、`_placement_for_meta`/`Placement` 的「width/height 均声明
     210×210 才 preserve」）。
  4. **补测（§8.3）**：新增 `test_set_zoom_anchor_invariance`（恒等 +
     锚点不变）；「多选拖动整组一条 MoveItemsCommand」「drawBackground
     网格水平+竖直 minor/major」两条经核查已有
     `test_multi_drag_writes_back_whole_group` / `test_paper_scene_grid_renders_both_axes`。
  5. **文档收口**：AGENTS.md 架构重写 + 已知缺口、本记录、README.md 过期
     内容修正、PRD.md/PRD_layout.md 顶部状态注记。

## 10.3 验证结果（2026-09-23，阶段 5 实跑）

- 受影响/相邻测试文件（15 个）实跑全绿：
  `PYTHONPATH=src python -m pytest -q --basetemp=.pytest_tmp -p no:cacheprovider
  tests/test_toolchain.py tests/test_coords.py tests/test_gui_job.py
  tests/test_gcode_parse.py tests/test_gui_layout.py` → **71 passed**；
  `… tests/test_gui_layout_window.py tests/test_gui_window.py
  tests/test_gui_presets.py tests/test_gui_image.py tests/test_gui_edge.py
  tests/test_gui_alignment.py`（加 `QT_QPA_PLATFORM=offscreen`）→ **66 passed**；
  `… tests/test_gui_text.py tests/test_gui_doc_import.py tests/test_gui_opt.py
  tests/test_gui_controller.py` → **45 passed**。合计 **182 passed**。
- 全量 pytest 未由本次跑（重构流程由脚本统一跑全量门禁）。
- 静态扫描（测试内执行）：翻转写法全 src 零命中（白名单空）、210 数值
  字面仅 `coords.py` 定义处（白名单空）、独立 `flip_y`/`already_paper`
  符号 src+tests 零命中 —— 由 `tests/test_coords.py::test_flip_symbol_level_zero_hit`
  自动钉死（tokenize NAME 级，覆盖 C1 别名形态；评审 medium#1 补，
  此前仅手工 grep）。全量计数 216 项（2026-09-23 `pytest --collect-only`
  收集，非执行）。guard/transport/dialect 零改动。

## 10.4 已知缺口（同 AGENTS.md「已知缺口/后续」）

执行期 M114 轮询（动 run_job 流控，明确不做）、相机叠加、G92/WCS、
TiledPathItem（>2000 段实测需要才做）、SVG 单位换算/越界自动裁剪/凸包
Frame/Word·Excel 样式保真（蓝图 §9 明确不做）；真机验收清单（Frame 目视、
设原点对准 <1mm、空跑不触纸）待现场执行。契约曾列的「拖动对象吸附/吸附
pitch 联动/数值输入撤销合并」本树已实现（见 10.1 #10 回归列），不列缺口。

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

`PYTHONPATH=src python -m pytest -q`：最近一次**完整执行**的全量实跑是 2026-09-27 收口的
**326 项全绿**（18.61s；基线 217），见「11.4 验证结果」。此后 12 轮收口新增了
大量用例；2026-09-29 文档收口会话实跑
`python -m pytest tests/ --collect-only -q` → **638 tests collected**，但
**全量执行本会话未跑**（约定由脚本统一跑全量门禁），故「638 条中多少 passed /
多少 xfailed」未经该会话实测 —— 见 §14.4。集成测试用本机 TCP 模拟 Marlin 跑探测
建档到文件发送全链。GUI 测试自设 `QT_QPA_PLATFORM=offscreen` 且每文件独占一个
QApplication。

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

---

# 2026-09-27 重构记录（排版模型树化，PRD v1.3 M1–M6 全部落地）

契约：`docs/PRD_layout_model_tree.md`（v1.3）。基线 217 → 收口 **326 passed**
（全量实跑，`PYTHONPATH=src python -m pytest -q --basetemp=.pytest_tmp
-p no:cacheprovider`）。串口链（`safety/`/`transport/`/`dialect/`）、`worker.py`
流控、`main_window._recompile` 触发语义、`export_svg.py`、`coords.py` 的既有
函数——**全程零改动**（`git diff` 逐路径核验为空）。

## 11.1 里程碑 → 落地形态

| 里程碑 | 内容 | 关键落点 |
|---|---|---|
| M1 | 树化地基 | `Item.children`、`MAX_TREE_DEPTH=64`、`remove` 组感知 + `DetachInfo` |
| M2 | 编组 | `group_items`/`ungroup`、`Group/UngroupCommand`、`canvas/group_overlay.py` |
| M3 | 镜像 | `coords.mirror_scalar`、`Item.mirror_x/y`、`rebuild_path` 烘镜像 |
| M4 | 多页 | `Document.pages` + 当前页门面、页签 UI、单栈+命令页归属 |
| M5 | 图片重追 | `Item.image_spec`、`RetraceImageCommand`、Canny 模式接线 |
| M6 | 收口 | 全量回归 + 文档状态注记（本节） |

## 11.2 与 PRD 设想不同、值得记的六处

1. **编组不用 QGraphicsItem 父子**：蓝图 `:149` 明说 `pos()` 是唯一返回父坐标
   系的接口，成组即错位。改用「模型树 + 扁平场景 + 自管组框 overlay」。
2. **组变换 = 叶子变换集合**，容器恒为恒等变换（无渲染通路）；组选中 = 叶子集，
   故「拖组 = 一条命令」复用既有 `commit_move` 漂移收集，零新增代码。
3. **镜像支点 = 本地 bbox 中心**（一般式），`pos`/`scale`/`angle` 不补偿；
   组的镜像是**逐成员**翻，不是整组刚性反射（PRD 未定义组的镜像语义）。
4. **多页用当前页门面**而非改造所有消费点：`items`/`bed_w`/… 委托当前页，故
   `export_svg.py` 零改动即按当前页导出——这是「零改动声明」成立的前提。
5. **撤销是单栈 + 命令页归属**，不是每页独立栈：Ctrl+Z 的心智是「时间上的上
   一步」；跨页撤销自动作用到归属页再还原视图页。
6. **吸附候选域修复是 FR-06 的前置**：`_all_paths` 原返回**局部**坐标而吸附喂入
   的是页面坐标（域不一致，`pos≠(0,0)` 的图元对象吸附恒零命中）；现拖动侧与绘制
   侧共用 `snap_candidate_paths` 一份页面域实现。

## 11.3 收口时修的跨里程碑缺口

- **`image_spec` 不在剪贴板白名单**（M2 的 `_item_to_json`/`_item_from_json` 显式
  字段表漏了它）⇒ 复制/粘贴/副本得到的图片图元 `image_spec=None`，**双击不再能
  重追**且无任何报错。已补入两处并加 3 条用例（含旧剪贴板兼容）。
- **收口期自身的覆盖回退（评审拦下）**：重写双击用例时，脚本按「从旧用例处截断
  再拼接」把其后的 `test_retrace_dialog_prefills_from_spec` 一并删掉，且自述未
  提及 —— 实测把预填改回占位后全量仍全绿。即「对话框按 spec 预填」这条 US-5
  能力一度**无回归保护**，而两份文档已把它当已交付能力对外声明。已原样恢复并
  加验：撤掉预填 ⇒ 仅该用例变红。教训：**重写测试文件时不要用「从某处截断再拼接」
  的脚本**，否则尾部用例会被静默吃掉（本条与下条同源）。

## 11.4 验证结果（2026-09-27，M6 实跑）

- 全量 `pytest -q` → **326 passed in 18.61s**（基线 217）。
- `tests/test_coords.py` 12 条全绿：翻转写法全树扫描、符号级扫描（禁 `flip_y`/
  `already_paper`）、210 常量单源、210 收敛扫描——镜像数学全落
  `coords.mirror_scalar`（复用唯一翻转实现），零手写翻转。
- 既有冻结面一字未改仍绿：`test_gui_layout.py` 37、`test_gui_doc_import.py` 10、
  `test_gui_layout_window.py` 21。
- 新增用例：组 19 / 镜像 20 / 多页 18 / 重追 18 / Canny golden 12（含上游 T11）。
- 关键用例经「撤掉实现→必须变红」验证（分布去重、镜像逐轴门控、跨页撤销归属、
  剪贴板 image_spec、重追对话框预填、setattr 通道重建路径）。

## 11.5 已知缺口（同 AGENTS.md「已知缺口/后续」）

~~新增记入：越界预检**只警告不拦截**~~ —— **该条已于 2026-09-29 销账，见 §14.1**，
不要再照抄。**页操作不可撤销**、**组的镜像语义未定义**仍然成立。其余沿用 10.4。
真机验收（贴纸镜像的翻面转印效果、Canny 真实照片调参）**需现场**。

---

# 2026-09-29 交付收口记录（12 轮，0f160c3 → 6cd513d）

范围：`git diff --stat 0f160c3..6cd513d` = 20 个文件、+5648/−109，其中源码只有
4 个：`layout/model.py`(+220/−)、`layout/layout_page.py`(+279/−)、
`canvas/handles.py`(+61/−)、`canvas/group_overlay.py`(+54/−)，其余 16 个是测试。
红线（`safety/`/`transport/`/`dialect/`、`worker.py` 流控、`main_window._recompile`
触发语义）与两条冻结用例（`test_gui_layout_window.py` 21 条、`test_coords.py` 12 条）
`git diff` 为空。

## 14.1 修好的（附回归）

| # | 缺陷 | 修法落点 | 回归 |
|---|---|---|---|
| 1 | **越床导出只警告不拦截**：弹完框无条件 emit ⇒ 切纸机静默切到床外 | `layout_page._confirm_in_bed`（`:1954`）成为「送去执行」与「另存为 SVG」共用的唯一闸；默认拒绝、「取消」为默认按钮、关窗等同取消，只有「忽略并继续」放行；`_on_export:2012` 在 emit 前 return | `tests/test_gui_layout_bounds_gate.py`（含把 `BED_W/BED_H` 猴补丁成 123/456 的构造性用例，硬编码 210 的实现必红） |
| 2 | 编组嵌套判据只看直接子项 ⇒ 外层容器（唯一子项是内层容器）永远画不出框 | `group_overlay._complete_groups:108-132` 改走 `iter_units(doc.items, visible_only=False)` + `iter_leaves` 传递闭包 `leaf_ids <= sel` | `test_gui_canvas_group_nested.py`(7) + `test_gui_canvas_group_overlay_nested.py`(9) |
| 3 | 组框几何随缩放缩放（旧 `setScale(1/ppm)`，注释称「不改变几何」是错的） | 去掉该 setScale；`GroupFrameItem` 加 `container` 字段，框 ↔ 容器一一对应 | 同上 |
| 4 | 手柄屏幕尺寸/旋转手柄屏幕间距只在「选中变化」后才对上 | `handles.py:90` 自接 `view.viewChanged`（`PaperView._apply_transform` 是 set_zoom/fit/pan_by/wheel 的唯一汇流点）；`_on_view_changed` 先比缓存 ppm，平移早退；`update_sizes` 兼调 `_relayout_rotate` | `test_gui_canvas_handle_zoom.py`(7) |
| 5 | 排版页四类编辑（镜像/移动/新增/删除）与页切换不同步作业预览 ⇒ 作业页握陈旧几何且可执行 | 触发点挂 `QUndoStack.indexChanged`（覆盖拖动 `MoveItemsCommand` 这条从 `canvas/items.py` 直接 push 的路径）；门禁走 `main_window._on_layout_job_sync` 既有实现 | `test_gui_layout_job_sync.py`（开关开：4 类各发恰好一次 + payload 是编辑**之后**的 `flatten_visible(doc)`；开关关：一律不发） |
| 6 | 删当前页 / 页重排 0 次同步 | `_on_page_del`、`_move_page` 各加一次 `_maybe_emit_job_sync()`；`_on_page_add`/`_on_page_dup` **故意不加**（已经页签信号发过，加了会双发） | `test_gui_layout_job_sync.py::test_delete_current_page_resyncs_job_to_surviving_page` / `::test_move_page_resyncs_job_to_current_page`（修复前实测 3 failed / 2 passed） |
| 7 | 层序 top/bottom 基准写在循环内 ⇒ 单元内部递推、单元之间不递推，混选时散件穿进组里 | 基准移到循环外算一次、循环内用游标递推、变更循环后一次 push | `test_gui_layout_zorder_and_jobsync.py`(35) |
| 8 | `group_items` 不校验成员归属 ⇒ 跨页成员两页同存、悬空成员凭空进切割序列 | `model.py` `Page.group_items` 加「每个成员必须在当前页树里」的前置校验，拒则整单 return（去重后、自含拒绝前 ⇒ 拒绝零副作用） | `test_gui_layout_group_ownership.py`(9)；修复前 4 红 |
| 9 | 床尺寸在 5 条页操作路径上可能脱节 | 收敛为 `Document.bed_w/bed_h` 直通当前页的 property，恒等式从纪律变结构事实 | `test_gui_layout_bed_enum.py`(28) + `test_gui_layout_bed_enum_structural.py`(12，钉因不钉症状) |
| 10 | 界面无反馈：无选中镜像静默、镜像后 AABB 跳变无说明、组镜像语义未写明、进组双击无提示、Canny low>high 无校验 | `layout_page.py` 补中文状态提示 + 4 处 tooltip + `_validate_canny_thresholds`（只加说明与校验，**不动数学**） | `test_gui_layout_ui_honesty.py`(13) |
| 11 | 「撤销文案永不刷新」 | **不成立**：按钮是 `QUndoAction`，Qt 每次 push/undo/redo 自动刷新；`_refresh_undo_actions` 是空转死代码 | `test_gui_layout_undo_actions.py`(6)，逐拍比对文案与可用态 |

## 14.2 写成契约但**未修**的（4 组 `pytest.mark.xfail(strict=True)`）

`strict=True` 是刻意的：默认非 strict 时修好会静悄悄 XPASS 继续绿，标记就永久烂在
文件里；strict 让它**修好即报红**，逼人摘标记。仓库不留红，也不让契约消失。

| 契约 | 未修的缺陷 | 修好即报红 |
|---|---|---|
| `tests/test_gui_layout_attach_reparent.py`（R1，3 条） | `Page.attach` 缺「item 已有他处宿主 ⇒ 拒」判据（`model.py:708-715` 只判 item↔owner 互为后代） | ✔ |
| `tests/test_gui_layout_tree_writer_enumeration.py`（R2，2 条） | `model.py` 两处「产品路径不可达」枚举：行号指向散文 + 漏点 `group_items`/`ungroup` 的直接 children 写入 | ✔ |
| `tests/test_gui_canvas_zoom_geometry_rescan.py`（R3，2 条） | 缩放路径白做 O(选中集总点数) 的几何全量重扫 | ✔ |
| `tests/test_gui_canvas_drag_zoom_handle_consistency.py`（R4，1 条） | 拖动未提交时缩放，5 个手柄 1 对 4 分裂 | ✔ |

⚠ **接手须知**：R1–R4 的修复对象多在 `model.py` / `canvas/handles.py`。修好后这 4 组
会 XPASS(strict) ⇒ **全量变红**，那是提醒摘标记，不是回归失败。

## 14.3 本轮复核出、仍然存在的缺口（本会话 offscreen 实测取证）

按对机器的后果排序；触发条件与症状见 `AGENTS.md`「已知缺口」。

1. **执行中删当前页 ⇒ 作业页永久握着已删页几何**。实测：作业页 x=[100,140]、
   执行中删页后画布 x=[0,10]，控制台只有一句「执行中，参数改动本次作业结束后生效」，
   `jobDone` 补编译后作业页**仍是** [100,140]，`run_btn` enabled。
   非执行期同一路径已修好（作业页确实跟到存活页）⇒ 洞专属于执行期。
2. **执行中的几何编辑被永久丢弃**。实测：加一条 (50,50)-(60,50) 的线后，
   作业页仍 [0,10]；再点执行，worker 实收
   `G0 X100 Y100 / G1 Z17 / G1 X140 Y140` —— 旧内容旧位置，画布上的新线一次都没出现。
   `main_window.py:1592-1595` docstring 给的缓解「用户下次切页即重新同步」
   **实测不成立**（`self.tabs` 无 `currentChanged` 接线，往返切页不同步）。
3. **编组/解组撤销往返后文档错位**。实测结构轨迹：
   `['组3[a,b,c]']`（按钮写「撤销 解组」但文档是编组态）→ 再撤一次
   `['组3', '组3[a,b,c]']`（幽灵空容器）→ 撤到底 `['组3','组3']`、
   `flatten_visible` 0 条而两个空壳还在。根因 `undo_cmds.py:541` 每次 redo 新建
   容器、`:585` 只捕获一次容器对象。**无「切两遍」**（折线数与几何未变），
   但撤销栈从此不再描述文档。
4. **「置于底层」与未选中图元同 z，切割次序被劈开**。实测：4 散件（画线默认
   z=1,2,3,4）选后两条置底 ⇒ M0=0、M1=1、X=1（**与 M1 同 z**）⇒ 切割序
   M0, **X**, M1, Y。根因 `layout_page.py:1382/1399` 游标只增不减。置顶安全。
5. **模型层双父 DAG**（切两遍的形状）与**带自身折线的容器**（看不见但会切）。
   前者生产栈帧 0 次触发（潜伏）；后者 **Ctrl+V 即可达**（`_paste` 零校验 +
   `_item_from_json` 同时还原 paths/children），实测 `flatten_visible` 出含
   x=305 的越床直线而画布只有 `kid`。
6. **剪贴板超深树毒化文档**：导出口径的深度安全线是 **63**（`iter_items` 比其余
   入口多下探一层，64 就已让 `flatten_visible` 抛 `ValueError`）；≥65 层还会让
   undo 的 redo 被 PySide6 吞成 stderr 噪声（栈说已撤销、模型里树还在）。
7. **越床闸口径比真正送去切的保守 ⇒ 只误拦不漏拦**：隐藏后代与 <2 点折线会被
   报成越界（实测分别报 80.0mm / 3.4mm，而 `flatten_visible` 根本不会送它们）。
   100% 不一致都是误拦 ⇒ 假警报 + 无谓堵死导出（含纯写文件的「另存为 SVG」），
   **不是安全洞**。
8. **界面文案比实现说得绝对**：宽/高 tooltip「旋转或镜像后 AABB 会变」在
   0°/90°/180° 与轴对齐形状下**逐位不成立**（实测 identical=True）；宽/高
   spinbox 键入 `-10` 后面板显示 `-10.0` 而真实 AABB 宽仍是 10.0、scale 仍是 1.0，
   静默无提示。
9. **潜伏项**（今天数值上无差别，但结构性缺口在）：`_page_box` 丢祖先链
   （内层组偏 100mm）、`group_overlay` 不做身份去重、locked 成员的组永远画不出框
   但整组照样切（需带 `"locked": true` 的外部 JSON 载荷）。

## 14.4 验证结果（2026-09-29 本会话实跑）

- `python -m pytest tests/ --collect-only -q` → **638 tests collected**。
  ⚠ **全量 `pytest -q` 本会话未跑**（收口流程约定由脚本统一跑全量门禁），
  因此「638 条里多少 passed / 多少 xfailed」**未经本会话实测**；已知其中
  4 组 strict xfail 契约共 8 条按设计 xfail。
- 4 组 xfail 契约实跑（确认仓库在这些契约上**不留红**）：
  `QT_QPA_PLATFORM=offscreen PYTHONPATH=src python -m pytest
  tests/test_gui_layout_attach_reparent.py tests/test_gui_layout_tree_writer_enumeration.py
  tests/test_gui_canvas_zoom_geometry_rescan.py
  tests/test_gui_canvas_drag_zoom_handle_consistency.py -q -rxX
  --basetemp=.pytest_tmp/docclose -p no:cacheprovider` → **10 passed, 8 xfailed**
  （-rxX 逐条列出 8 条 XFAIL，reason 与 §14.2 表格一致）。
- 各文件用例数实跑（`--collect-only -q`）核对 §14.1 引用的计数：
  `bed_enum_structural` 12、`undo_actions` 6、`bounds_gate` 15、
  `group_ownership` 9、`canvas_group_nested` 7、`canvas_group_overlay_nested` 9、
  `canvas_handle_zoom` 7、`ui_honesty` 13、`layout_zorder_and_jobsync` 35、
  `layout_job_sync` 11。
- 本会话实跑的其余命令全部为**只读取证探针**（offscreen Qt，见 §14.3 各条
  所列现象），探针写在系统临时目录并已删除。
- 取证探针写在系统临时目录 `C:\Users\zyq\AppData\Local\Temp\docprobe\`，
  已删除；**本轮未改动任何 `.py`**（`git diff --name-only` 仅本文档四份）。

## 14.5 必须现场验收（本轮全部**未**上真机）

本轮 14.3 的九条缺口取证**全部是 offscreen Qt 模拟层**（真 `LayoutPage` /
真 `MainWindow` / 真 `QUndoStack` / 真 `QWheelEvent`，但**无串口、无电机**）。
上真机需要的条件与待验项：

- **执行中删页 / 编辑丢弃（#1、#2）**：需一次完整作业执行 + 作业期间切排版页交互
  + 串口回包。待验：作业结束后作业页是否仍握旧/已删几何、再执行时机器走的
  是不是画布上的内容。
- **撤销往返错位（#3）**：需肉眼确认幽灵空容器是否会进下一刀的 G-code
  （本轮只验到 `flatten_visible` 归 0、两个空壳仍在模型里）。
- **置底切割次序（#4）**：需用**几何不同**的图元（不同长度/不同端点）才能在纸面
  上看出次序，本轮夹具四条线几何全同（`(0,0)-(10,0)`），只有 z 序证据。
- 其余沿用 11.5 / 10.4：Frame 与预览 bbox 目视一致、设原点后落笔对准 <1mm、
  空跑不触纸、贴纸镜像翻面转印、Canny 真实照片调参。

---

# 2026-09-29 交付收口记录（续：第 6–16 轮，`0f9a542` → `67883e8`）

> 本节由**文档收口会话**写：那一轮的任务是「把技术状态写进文档，不改代码」。
> 所以本节的形态与 §14 不同 —— 主体是**核验与复测**：对每一批已交付的修复，
> 在当前树上重新取证（能复现的写复现，不能复现的明写「未复现」），并把
> §14.3 那份缺口清单按当前实现逐条重判。
> **本轮未改动任何 `.py`**（`git status` 只有四份文档）。

## 15.1 核验方式（可复现）

- **定向回归**（本会话实跑）：

  ```sh
  PYTHONPATH=src python -m pytest -q --basetemp=.pytest_tmp/doccheck/bt \
    -p no:cacheprovider \
    tests/test_gui_layout_bounds_gate.py \
    tests/test_gui_layout_own_geometry_ingest.py \
    tests/test_gui_layout_group_ownership.py \
    tests/test_gui_layout_ungroup_undo.py \
    tests/test_gui_layout_recompile_atomicity.py \
    tests/test_gui_layout_status_visibility.py \
    tests/test_gui_layout_toolbar_feedback.py \
    tests/test_gui_layout_ancestor_chain_box.py \
    tests/test_gui_layout_sync_placement_ownership.py \
    tests/test_gui_layout_zorder_cut_order.py
  ```

  → **106 passed, 5 skipped, 3470 warnings in 0.98s**。
  ⚠ **全量 `pytest -q` 本会话未跑**（按本轮约定，全量门禁由脚本统一跑），
  所以「全仓多少 passed / 多少 xfailed」**未经本会话实测**。

- **取证探针**：11 个独立脚本，写在 `.pytest_tmp/doccheck/`（`p1.py`–`p11.py`），
  独立脚本需在 `import PySide6` **之前**
  `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` 并
  `sys.path.insert(0, r"D:\mcu_prj1\src")`。走的是**真控件路径**：
  真 `QToolButton.click()`、真 `QUndoStack`、真 `QApplication.clipboard()`、
  真 `_paste`、真 `run_btn.click()`，不是直调内部方法。
  取证用完后已删除（`.pytest_tmp/` 本身是 git-ignore 的临时目录）。

## 15.2 销账的缺口（§14.3 的九条里，四条已修 / 一条改写 / 四条仍在）

| §14.3 旧编号 | 现状 | 证据 |
|---|---|---|
| #1 执行中删页 | **仍在** | 实测：送出作业 X=[100,140] → 执行中删页 → `jobDone` 后**仍是 [100,140]**、`run_btn` enabled |
| #2 执行中几何编辑 | **仍在** | 实测：执行中加含 X=50 的线 → `jobDone` 后**仍是 [100,140]**，控制台仍打「本次作业结束后生效」 |
| #3 编组/解组撤销往返 | **仍在**，症状与旧描述逐位一致 | 实测：重做回顶后 `undoText()=='解组'` 而文档是编组态；再撤一次出 `[('组3',), ('组3',['a','b','c'])]`；撤到底折线归 0 而模型留 `[('组3',),('组3',)]` |
| #4 「置于底层」与未选中同 z | **仍在**，形状与旧描述逐位一致 | 实测：X(z=1) M0(z=2) M1(z=3) Y(z=4)，选 M0+M1 置底 ⇒ `M0=0.0, M1=1.0`，未选中的 **X 仍 1.0** ⇒ 切割序 `[10,0,20,40]`，X 被切在两选中件中间 |
| #6 带自身折线的容器 | **已修**（入档口整单拒绝 + 中文提示） | 实测粘贴自带折线的容器 ⇒ `doc.items==[]`、提示 1 条。`layout_page.py:217` 新增 `OwnGeometryContainerError`，`:251` 判定、`:1398`/`:1421` 捕获 |
| 旧 §12 `_page_box` 丢祖先链 | **读侧已修** | `layout_page.py:393` 改走 `iter_ancestors(self.doc.items)`；`test_gui_layout_ancestor_chain_box.py` 6 用例在 106 passed 里。**但写侧没跟上 → 新缺口，见 §15.4** |
| 旧「排版页零提示通道」 | **已加常驻提示行** | `layout_page.py:711` `_build_status_line()`，`:730` `_show_status`。**但提示行自己有两个新问题，见 §15.3** |
| 旧「工具条 8 个静默按钮」 | **已接中文提示** | `_zorder:1520` / `_align:1578` / `_distribute:1610` / `_delete_selected:1371`。**但键盘入口与页签条没跟上，见 §15.3** |
| 旧「组内图片双击判据分叉」 | **已统一为真值** | `layout_page.py:532` 改为 `if getattr(item,"image_spec",None):` |
| 旧「编译失败半提交」 | **状态原子性已修** | `main_window.py:1561` `_set_job_and_recompile`；`_recompile:1855` 返回 bool。**但四个调用方都不看返回值，且它把「执行中 no-op」也当成失败 → 新缺口，见 §15.4** |
| 旧「组套组画 0 个框」 | **已修** | `_complete_groups`（`group_overlay.py:108`）走 `iter_units` + 叶子传递闭包；`test_gui_canvas_group_nested.py`(7) + `test_gui_canvas_group_overlay_nested.py`(9) 在 106 passed 里。**但判据单次成本涨了，见 §15.6** |

## 15.3 旧 §14.3 未列、**本轮新查出**的缺口（按对机器的后果排序）

以下每条都标了触发条件。完整措辞见 `AGENTS.md`「已知缺口」A/B 组。

**A 组 · 会动刀**

1. **组编辑态下改宽/改缩放/对齐，把整组瞬移到原点**（第 9 轮 `_page_box` 修好
   **读侧**后的配套缺口）。`_apply_size`(`:1249`) 多选分支用页面系锚点
   `anchor_point(u)` 算 `it.pos`（**局部系**）回写。实测：真 `_paste` 造两层
   容器（外层 `pos=(100,0)`）→ 双击进组选 a/b → 属性面板填宽 20 ⇒ 切割 AABB
   `(105,5)-(115,55)` → **`(0,0)-(20,100)`**，叶子 `pos` 变 `(-105,-5)`，
   `_out_of_bed()` 返回 `None`（全在 210 床内）、`status_message` **零条**。
2. **画布画的坐标 ≠ 导出/切割的坐标**。渲染只施加叶子**自身**变换
   （`canvas/items.py:90`），从不施加祖先变换。实测：同一份两层容器文档，
   画布 PathItem 在 `(0,0) 10×10`，导出/切割 AABB 在 `(105,5)-(115,15)`，
   **差 105mm**。纯 UI 也可达（编组 → Ctrl+C → Ctrl+V，`dz=5` 让副本在画布上
   与原件完全重合，而副本会被切在 5mm 外）。
3. **「锚点归位」把越床几何洗进床内；静默同步这条路根本没有越床闸**。
   `_maybe_emit_job_sync`(`:806`) 只看开关，`_on_layout_job_sync`
   （`main_window.py:1589`）只有来源 + 执行中两道门禁，`_confirm_in_bed` 的调用点
   只有 `_on_save_svg` 与 `_on_export`。实测（真控件：床内方块 → 送去作业 →
   作业页选『锚点归位 bl→(0,0)』→ 回排版页属性面板把 X 键入 -40）：
   `_out_of_bed()` 报「超出 40.0mm」，而作业页 `runnable=True`、`violations=[]`、
   `_job_lines` 的 X 变成 **[0, 40]**、控制台**零新增行**。显式「送去作业」此时
   会被闸门拦下 —— 同一份版面，两条路一个禁一个放行。
4. **锚点归位每次按整份内容重算 ⇒ 没被编辑的图元被静默搬走**。实测：两个图元
   A(0,0) B(50,50)，选 anchor 后**只删 A** ⇒ B 落点从 (50,50) 变 (0,0)，
   **位移 50mm**，控制台零新增行。对照组 preserve 下同样操作 B 一动不动。
5. **执行中改放置模式被永久丢弃**（第 13 轮原子包装的配套缺口）。
   `_set_job_and_recompile` 把 `_recompile` 的三个 `return False`（执行中 no-op /
   无作业 / 参数非法）一视同仁当失败回滚；可执行期语义里执行中 no-op 恰恰是
   **延迟生效**机制。实测：作业跑着把下拉切到『锚点归位 bl→(0,0)』（combo 执行期
   未禁用、`layout_page.py` 里 `_job_running` 出现 0 次）⇒ 执行中与 `jobDone` 后
   `spec.placement` 均为 `preserve`、`_job_lines` X 不变 ⇒ 用户改的放置**永久
   蒸发**，而 combo 停在「锚点归位」与模型长期不一致。同一次执行中改 `feed`
   是**生效**的（走字段 `_feed_xy` 不走 spec）—— 同一触发集两种待遇。
6. **层序「组内相对次序」在 top/bottom 会被翻转**。游标铺开走 `iter_leaves` 的
   DFS 先序，拍平口径是 `(z, order)` ⇒ 组内 children 文档序与 z 序相反时会翻转。
   实测：X(z=3) 先画、Y(z=2) 后画 → 编组（children 文档序 `[X,Y]`）→ 选整组置顶
   ⇒ children z 变 `[X=4, Y=5]`，**切割序从 `[Y, X]` 翻成 `[X, Y]`**。
   `layout_page.py:1545-1547` 的注释写的是「top/bottom 都保持选中前的相对层序」
   —— 跨单元口径成立、组内口径被证伪。纯 UI 可达。
7. **层序「跨单元相对次序」在 min(z) 并列时仍由 Qt 决定**。排序键只有
   `min(叶子 z)`、无次序键，`list.sort` 稳定 ⇒ 并列时沿用
   `scene.selectedItems()` 的无序返回。Word/Excel 导入（`layout_page.py:1891`
   `_add_doc`）给全部条目写同一个 z ⇒ 并列真实存在。
   **诚实标注**：构造并列场景连跑 **6 个独立进程，本机 6/6 返回同一序** ⇒
   **分叉未在本机复现**；代码层面的口径缺口成立，跨启动分叉这一步本轮**未取得
   证据**。

**B 组 · 会静默吞操作 / 说谎**

8. **剪贴板可灌入超深模型树**（旧 §14.3 的第 7 条，本轮重新定量）。实测：深度
   63 通；**深度 64 静默入档**、`to_job_spec()` 抛 `ValueError`（该异常发生在
   `QUndoStack.indexChanged` 触发的 Python 槽里，被 PySide6 吞成 stderr
   traceback，**控制台零提示**），文档里从此留了一棵永远导不出的树；**深度 65
   直接从 `_paste` 抛出**（`self.doc.top_z()` 那一行，未捕获），排版页从此粘不进
   任何东西。入档口只捕 `OwnGeometryContainerError`，`paths: null` /
   `children: null` / `children: [null]` 同样未捕获地抛出、同样零提示。
9. **属性面板键入非正宽/高：静默 return，面板与真实 AABB 反号**。实测：真实 AABB
   宽 20mm 的图元键入 -10 ⇒ 面板显示 `-10.0`、真实 AABB 仍 20.0、scale 仍 1.0、
   `status_message` 零条。**这是最响的数字谎言** —— 它正是用户拿去预测「切多宽」
   的那个数。提示行已经在页内了，**只是这根线没接**。
10. **页内提示行：长消息被裁掉后半截**。它是 `QLabel` 且**没开 wordWrap**
    （`_build_status_line` 只做 setObjectName + setToolTip + setText），宽度由
    整条未折行文本撑开。实测（真 MainWindow，1000px 窗口）：97 字的中文拒收提示
    `sizeHint` 宽 1164px、实际只分到 **696px（59.8%）**，被切掉的正是
    「…已整单撤销粘贴，请确认剪贴板内容。」；排版页 `minimumSizeHint` 宽被顶到
    **1182px**。图元名完全由剪贴板控制（`_item_from_json:262` `name=d.get("name","item")` 原样带出、
    零长度校验），名字一长就从「丢行动句」升级到「丢解释句」。
11. **提示行只会被下一次 `status_message` 改写，而成功路径不发 emit** ⇒ 成功之后
    那行会长期停在与模型相反的陈述上。实测：无选中点「水平镜像」→ 提示行
    「未选中图元：请先选中要镜像的图元」；随后选中该图元**再点一次** → 镜像
    **真的成功了**（`mirror_x=True`、按钮勾上、几何翻了），提示行**一字未变**。
    镜像成功分支（`:1466`）与 `_exit_group_edit`（`:539`，Esc 可达）都不发 emit。
12. **`_align` 在「本来就对齐」时空转但仍 push 一条恒等命令**。`:1598` 无条件
    append change（对比 `_zorder:1565` 的 `if it.z != nz` 守卫）。实测：两个
    min-x 相同的图元点「左对齐」⇒ 几何零变化、提示行空、`undo.index()` 1→2、
    `undoText()` 变「对齐」。后果：用户接下来撤掉真正的「置顶」后，**第二次
    Ctrl+Z 被这条什么都没发生的命令吃掉** —— 这正是旧 §14.3 第 15 条描述的症状的
    **新实例**。
13. **页签条 ◀/▶ 在边界上纯静默**。`_move_page:875` 裸 return，同一行的「－」
    （`:843`）发「至少保留一页」。实测四种边界情形全部 `status_message` 零条、
    页序真的没动；五个页签条按钮 `isEnabled()` 全 True、`toolTip()` 全空。
    **不影响切割**（页序不决定当前页几何与作业内容），是纯交互诚实性缺口。
14. **Ctrl+C 无选中时把用户剪贴板覆写成 `"[]"`**。`_copy_selected:1373` 无
    `if not units` 守卫，最后一行 `clipboard().setText(...)` 无条件执行。
    实测：剪贴板里先放用户自己的文字，无选中按一次 Ctrl+C 就被换成 `[]`，
    提示行零条、页面零变化；紧接着 Ctrl+V 也是静默。同类还有 Ctrl+D
    （`_duplicate:1409`，实测无选中 msgs=[]）。
15. **`image_spec` 真值 ≠ dict；`text_spec` 会遮蔽 `image_spec`**。①
    `retrace_image_item`（`:2051-2052`）的 `spec.get("source")` **在 `try:`
    （`:2061`）之外** ⇒ 真值-非-dict（`0.5`/`"nope"`/`[1,2]`）在第 2 次双击时抛
    `AttributeError` 逃出事件处理器，既不开对话框也不发 status_message。
    ② `canvas/items.py:161-163` 的 `text_spec` 分支**排在** `image_spec` 分支
    （`:166`）之前且各自 return ⇒ 两键同存时提示说「再双击一次即可重追调参」，
    第 2 次双击进的却是文字编辑器；接受后 `EditTextCommand`
    （`undo_cmds.py:420-422`）只写 paths + text_spec，**不写也不快照
    image_spec** ⇒ 几何已是文字轮廓却仍自称「已追图片」。**需外部载荷**
    （UI 造不出 `image_spec`）。

## 15.4 本轮同时查出：**修好一条、却带出一条新的**（两条都在本轮新写的代码里）

- `_page_box` 祖先链（第 9 轮）修好**读侧**后，**写侧**（`_apply_size` /
  `_apply_scale` / `_align` 写 `it.pos`）仍是容器局部系 ⇒ §15.3-A-1。
- `_set_job_and_recompile`（第 13 轮）修好状态原子性后，把 `_recompile` 的
  **执行中 no-op** 也当成「编译未成」⇒ §15.3-A-5。

两条都**不是**上一版代码的旧问题；给它们单独记账是为了让下一个人知道这是
「新代码引入的」，而不是历史遗留。

## 15.5 **测试本身**钉不住的地方（本轮最该写下的一类）

绿灯不等于已验证。以下每条都是「测试全绿，但它钉的不是会出错的事」：

1. **`test_gui_layout_bed_vacuum.py` 对真正做决定的那个函数失明**。它用 AST
   关口保证「把闸门接到 `doc.page.bed_*` 必须报红」，但只 `inspect.getsource`
   了 `LayoutPage._out_of_bed`（`:205`）与 `LayoutPage._on_save_svg`（`:237`）；
   而**发不发 `export_requested` 由 `_confirm_in_bed`（`layout_page.py:2150`）
   的返回值决定** —— 它只被断言「`_on_save_svg` 调用了它」（`:243`），从不被
   调用、也从不被解析。**判定函数一挪位置，整套关口失明。**
2. **`test_gui_layout_zorder_cut_order.py` 的「切割次序」断言是空的**。
   `_cut_order(doc)`（`:59-66`）先把 `flatten_visible` 的 x 收成**集合**，再按
   `iter_leaves` 的 DFS 序吐名字 ⇒ 永远返回**文档序**，对次序零敏感。真正承重的
   是那些 z 字典比较。
3. **`test_gui_layout_order_vs_baseline.py` 的两条**。①
   `test_group_ungroup_does_not_decouple_order`（`:267`）写
   `a, c = doc.items[0], doc.items[1]`，而 `_fresh(*_ABC)` 建的是 `[a,b,c]`
   ⇒ 变量名叫 `c` 的握着 **b**，编的是**相邻**的 `[a,b]`；相邻对的 DFS 先序本来
   就稳定 ⇒ 把 A4 的 tie-break 退回 `key=z` 时它照样绿。②
   `test_decoupling_never_pushes_geometry_outside_the_bed` 的越床半段
   （`:195-201`）把要移动的 `wide` 构造成**末位** ⇒ `remove + add` 是空操作
   ⇒ 那一半恒真。另：该文件 docstring 写「今天没有 UI 可达路径：生产里 undo
   侧一律走 `attach(owner, index)` 原位复原」—— **这半句不成立**：编组的撤销走
   `Document.ungroup`（`undo_cmds.py:553`），它用**递增下标**回插
   （`model.py:889` 的 `Page.ungroup` 自己写了「非相邻成员编组再解组 ⇒ 顶层序
   不可复原」），所以「工具条编组 → Ctrl+Z」就能造出脱钩文档。
4. **`test_gui_layout_toolbar_feedback.py` 的「整条工具条不再有静默按钮」是假的**。
   parametrize 挂在一份**手写字典** `_TOOLBAR_GUARDS`（`:61`）上，测试从不读
   `_build_toolbar`；方向只有 `guards ⊆ toolbar`，没有 `toolbar ⊆ guards`。
   运行期枚举工具条 32 个 QToolButton，字典覆盖 8 个。
5. **`model.py` 四处 + 两份既有测试仍**无条件**声称「对无 children 的文档逐位
   不变」**（`model.py:42`、`:214`、`:1331`、`:1417`；
   `test_gui_layout_bed_enum.py:359/377`、
   `test_gui_layout_bed_enum_structural.py:281`）。但「无 children」只是**必要**
   条件：零 children 的扁平文档同样可以 `order ≠ 列表序`（已编号的对象被 `remove`
   后用 `doc.add` 重新入模），此时枚举面与切割序**分叉**（几何多重集守恒，只有
   次序变）。这与 `test_gui_layout_order_vs_baseline.py` 新写的「只在 order ==
   列表位置时成立」**互相矛盾**。生产 undo 侧走 `attach(owner, index)` 原位回插
   ⇒ **今天无 UI 可达路径**，属文档层不一致，不是现网 bug。
6. **`test_gui_layout_status_visibility.py` 只钉「有个 QLabel + 文本对 +
   `isVisible()` 为 True」**。`isVisible()` 对被 clamp 成 20px 宽的控件同样返回
   True（探针实测：192px 中文塞进 20px 框仍 `isVisible()==True`）⇒ §15.3-B-10 /
   B-11 它一条都抓不住。
7. **`test_gui_layout_image_spec_predicate.py` 的不变量把 `retrace_image_item`
   打成桩** ⇒ 函数体零执行；文件 docstring 写的「真的触发重追**或报错**」那半边
   从未被检查（唯一正例 fixture 真跑时兑现的是 `:2053` 的「无法重追」警告）。
8. **两处 docstring-only 交付**（`layout_page.py` 的 `_out_of_bed` 口径注、
   `to_job_spec` 的 order 说明）在**修复前的源码树上同样全绿** —— 其回归文件钉的
   是早已成立的现状，与那两段散文无实质耦合。按本轮「测试在修复前必须失败」的
   规矩，这类交付不满足条件。

## 15.6 性能（新记，契约未覆盖）

- **组框判据从 O(#顶层容器) 变成 O(Σ子树点数)**（第 1 轮引入）。`_complete_groups`
  （`group_overlay.py:129`）对每个容器调 `iter_leaves`，而 `iter_leaves`
  （`model.py:1344`）搭在 `iter_flattens`（`:1273`）上、对子树里每条折线急切做
  变换。**本轮实测**：30 组×10 叶×800 点（24 万点）文档上单次判据 **49.5ms**
  （1.65ms/容器）。与已记账的「缩放白做重扫」同类但位置不同
  （`group_overlay.py:129` vs `handles.py:152`），**无契约钉住**。
  第 3 轮已把「一次手势算 N 次」压成 1 次，但 **Ctrl+A 全选仍是裸循环**
  （`layout_page.py:1329-1332`），同规模下仍是秒级手势。

## 15.7 验证结果（2026-09-29 文档收口会话实跑）

- 定向 10 文件回归 → **106 passed, 5 skipped**（命令见 §15.1）。
- **全量 `pytest -q` 本会话未跑**（本轮约定由脚本统一跑全量门禁）。
- 4 组 strict xfail 契约的标记**仍在**（`grep -rn xfail` 于
  `test_gui_layout_attach_reparent.py` / `test_gui_layout_tree_writer_enumeration.py`
  / `test_gui_canvas_zoom_geometry_rescan.py` /
  `test_gui_canvas_drag_zoom_handle_consistency.py` 均有命中）——
  ⚠ **本会话只确认了标记存在，没有重跑这四个文件看它们是否 xfail**。
- 11 个取证探针见 §15.1；探针与 basetemp 跑完已清理。
- **本轮未改动任何 `.py`**。

## 15.8 必须现场验收（本轮全部**未**上真机）

§15.2 复现的 4 条与 §15.3 的 15 条**全部是 offscreen Qt 模拟层**（真
`LayoutPage` / 真 `MainWindow` / 真 `QUndoStack` / 真 `QToolButton.click()` /
真剪贴板，**但无串口、无电机、无纸面**）。上真机需要的条件与待验项：

- **作业页滞留 / 编辑丢弃 / 锚点绕过越床 / 锚点搬走图元 / 放置被丢弃**
  （§15.2 前两行、§15.3-A-3/4/5）：需一次完整作业执行 + 作业期间切排版页
  交互 + 串口回包。待验的是**刀实际落在哪**、画布内容与 G-code 是否一致。
- **组编辑态瞬移 / 画布≠导出**（§15.3-A-1/2）：需拿一张**几何不同**的图元
  （不同长度/端点）在纸面肉眼比对，本轮只验到坐标层面。
- **置底与组内次序**（§15.2 第 4 行、§15.3-A-6）：需纸面证据；本轮只有 z 序与
  `flatten_visible` 序。⚠ 旧 §14.5 已记「夹具四条线几何全同」—— 本轮改用不同 x
  起点，**几何仍全同**（都是 10mm 水平线），所以纸面仍看不出次序。
- **撤销往返幽灵空容器**（§15.2 第 3 行）：需肉眼确认它会不会进下一刀的 G-code
  （本轮只验到 `flatten_visible` 归 0、两个空壳仍在模型里）。
- **提示行裁切**（§15.3-B-10）：需在**真实显示器 DPI / 真实字体**下看那条提示
  行。本轮测的是原生 Qt 字体度量（`Microsoft YaHei UI 9pt`、CJK 12px/字），
  不是真机窗口。
- 其余沿用 §14.5 / §11.5：Frame 与预览 bbox 目视一致、设原点后落笔对准 <1mm、
  空跑不触纸、贴纸镜像翻面转印、Canny 真实照片调参。

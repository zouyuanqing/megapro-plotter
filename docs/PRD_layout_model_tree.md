# PRD：排版模型树化与编辑能力补齐（编组 / 镜像 / 多页 / 图片二次追踪）

> - 日期：2026-09-27　·　版本：v1.3（终审修订，修订记录见 §12）
> - 依附：`PRD.md`（上位机总 PRD）、`PRD_layout.md`（M3 排版 PRD）、`docs/preview-layout-blueprint.md`（重构契约，阶段 1–5 已落地）。约定冲突处以 `AGENTS.md` 与代码为准。
> - 基线：撰写会话实跑 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -m pytest -q --basetemp=.pytest_tmp -p no:cacheprovider` → **217 passed in 15.41s**（与评审档案记载的 217 基线一致；REPORT.md 记载 216 收集数 +1 为后续新增用例）。

---

## 1. 背景与问题

M3 排版页（Tab1「排版/制作」）已随预览/排版重构（蓝图阶段 1–5）交付：模型 `Item` = 折线集合 + pos/scale/angle_deg 平面变换（`src/megapro/gui/layout/model.py:48-56`），`Document` = 图元平列表 + 床尺寸（`model.py:93-123`），导出走 `flatten_visible` → `document_to_svg` 单 SVG（`model.py:149-156`、`layout/export_svg.py:27-43`）。但 `PRD_layout.md:74`「后续（不做本期）」清单中仍有四项未兑现，且其共同根源都是**模型是平的**：

1. **无编组语义**。Word/Excel 导入产 N 个平铺 Item（`layout/doc_import.py:163-184`），仅靠「组导入契约」（整组一次归位，`layout_page.py:93-138`；"严禁逐 Item 归位" 见 `layout_page.py:99`、`doc_import.py:9/:168`）保证导入瞬间相对布局不塌；导入后是散件，移动/对齐/层序/复制都逐个操作，无结构保证后续编辑不破坏相对布局。
2. **无镜像**。贴纸转印必须水平镜像刻（PRD_layout.md:74 原文「居中/镜像（对裁纸有用）」），现状用户只能在 Inkscape 等外部工具预处理再导入，闭环断裂。
3. **单页**。`Document` 无 pages 字段（`model.py:93-123`），多版本/多卡片排版无承载，只能整页清空重来。
4. **图片一次性追踪**。加图对话框只留文件名留痕（`layout_page.py:789`），改阈值/模式须重新导入重摆；照片/素描类边缘追踪从未接入——grep 实证 `Canny|findContours|approxPolyDP` 在 `src/` **零命中**（撰写会话复核），`requirements-gui.txt:6` 已声明 `opencv-python>=4.10`（cv2 现仅 resize/cvtColor，`gui/edge_to_svg.py:29-44`）。

对齐/分布/层序/撤销重做等基础编辑**已实现**（`layout_page.py:633-704`、`canvas/undo_cmds.py` 全族），不在本 PRD 范围内重做，只做「按组语义适配」。

---

## 2. 目标与非目标

### 2.1 目标

- **G1 模型树化**：`Item` 增 `children` 字段（容器 = children 非空），`transformed_paths`/`page_bbox`/`bbox`/`flatten_visible` 递归组合（父变换∘子变换）；**无 children 的 Item 数学逐位不变**（既有 transform golden `tests/test_gui_layout.py:28-37` 与 roundtrip golden `tests/test_gui_layout.py:132-155` 一字不改保绿）。
- **G2 编组/解组/组编辑**：模型 API + undo 命令 + 场景组适配 + 编辑操作按组 bbox 语义适配。
- **G3 水平/垂直镜像**：贴纸转印可用，几何端到端进 JobSpec。
- **G4 多页管理**：`Document` 容器化 pages + 页签 UI + 导出/送作业按当前页。
- **G5 图片二次追踪**：`Item.image_spec` 记录源参数，就地重追（pos/scale/angle 保持）；新增 Canny 照片/素描模式。

### 2.2 非目标（明确不做）

- 对齐/分布/撤销重做基础能力（已实现，见 §1 末段）；
- 工程文件持久化（现状单页也没有存盘实现，唯一序列化是剪贴板逐 Item JSON `layout_page.py:594-617`）；
- 组内布尔运算；非等比缩放（`model.scale` 标量，`canvas/handles.py:1-13` 契约）；
- Word/Excel 样式保真（蓝图 §9 明确不做，`docs/preview-layout-blueprint.md:451`）；
- G92/WCS、执行期 M114 轮询、越界自动裁剪、SVG 单位换算（蓝图 §9 `blueprint.md:442-453` 既有不做清单）；
- TiledPathItem（蓝图 :452：接口预留、实测需要才做）；
- **跨页批量逐页导出**：与 `PRD_layout.md:120` 决策 5「单 SVG」冲突，本期按当前页导出保契约（如需，见 §10 前置决策）；
- **QGraphicsItem 父子成组方案**：默认不用（理由见 §7 决策 D1），若实施者改选则属前置决策（§10）。

---

## 3. 术语

| 术语 | 定义 |
|---|---|
| 容器 Item | `children` 非空的 Item；自身 paths 可为空，几何 = 子项变换合成后的并集（一般式数学，产品路径恒为恒等）；**组变换 = 叶子变换集合（一条命令）**，容器只承载结构（children/选择语义）；拍平按**全局叶子 z** 排序，容器 z 不参与拍平（v1.2/v1.3 裁决） |
| 模型树 / 扁平场景 | 树只存在于 `model.Item.children`；Qt 场景保持扁平（每个叶子 Item 一个 PathItem），组框用 overlay 自管图形表达 |
| 组框 overlay | 仿 `SelectionHandles` 自管 QGraphicsItem 模式（`canvas/handles.py:68-114`）画组 bbox/命中框，不引入 Qt parent |
| 组导入契约 | 整组 `paper_from_svg_ydown` → 组级一次 `place_at_anchor` → 逐 Item `normalize_local`（`layout_page.py:113-138`）；"严禁逐 Item 归位" |
| 预览≡发送 | `compile_job` 产 `CompiledJob.lines` 唯一真源；预览吃 `parse_lines(lines)`，worker 发同一份 lines（`main_window.py:1751-1759` docstring） |
| JobSpec / `_recompile` | 内存唯一源 + 唯一重编译汇流（`main_window.py:1751`），触发集语义不改 |
| 翻转单源 | y 翻转唯一实现 `coords.flip_y_scalar`（`canvas/coords.py:46-52`）；`BED_W/BED_H=210.0` 唯一定义处 `coords.py:34`；静态扫描钉死 `tests/test_coords.py:148-154`（pattern）、`:277-302`（零容忍）、`:305-327`（符号级）、`:330-371`（210 常量） |
| image_spec | Item 上的图片源参数 dict（源路径/模式/阈值/target_mm），仿 `text_spec` 先例（`model.py:56`） |

---

## 4. 用户故事

- **US-1 贺卡编组**：用户把标题文字、装饰 SVG、日期三个图元框选 → 「编组」→ 拖动一次整体挪位、对齐一次到位；不满意 Ctrl+Z 一步退回编组前；「解组」后各图元精确回到原几何。
- **US-2 Word 整组操作**：导入 Word 贺词（现为多个散件）→ 自动成一个容器 → 整组缩放 80% 后段间距、行序、正立方向全部保持（与导入瞬间一致）。
- **US-3 贴纸转印**：刻贴纸前点「水平镜像」→ 预览、导出 SVG、送作业 G-code 全部镜像一致；刻完翻面转印字是正的（最终确认**需现场**）。
- **US-4 多页卡片**：排版第 1 页卡片 → 「复制页」得第 2 页 → 改第 2 页姓名字段时第 1 页纹丝不动 → 逐页「导出并送去执行」。
- **US-5 照片调参重追**：导入照片（Canny 模式）→ 刻后发现线太碎 → 双击图片 → 阈值/边缘参数就地调整重追 → 新折线出现在原位置、原缩放、原角度；Ctrl+Z 回到旧线条。

---

## 5. 功能需求

### FR-01 模型树化地基（对应 T1，2.5 人日）

`Item` 增 `children: list["Item"]`；`bbox`/`transformed_paths`/`page_bbox`（`model.py:58-90`）对容器递归：子路径先经自身变换，再合成进父变换（一般式：父 scale/angle/pos ∘ 子变换；**容器恒等变换时逐点等于子项现状几何**——FR-07 包装等价性的数学基础）。**与 v1.2 裁决的关系（v1.3 澄清）**：产品路径上容器变换恒为恒等（FR-07 wrap_group、组变换走叶子集合），一般式合成与验收②「父旋转/缩放」golden 是**防御性 pin**——动机：防止递归实现按恒等特化而漏掉父变换项（未来任何路径写入容器变换即静默错位），且 wrap_group 恒等性本身依赖「父恒等∘子」一般式的正确。嵌套容器支持范围以 golden 固化（§11 问题 1）。
**验收**：① 既有 `tests/test_gui_layout.py:20-37` 与 `:132-155` **一字不改**通过；② 新增嵌套 golden（父旋转/缩放手算对照，防御性 pin）进 `tests/test_gui_layout.py`；③ 全量回归不破。

### FR-02 递归拍平与导出（对应 T2，1 人日）

`flatten_visible`（`model.py:149-156`）改为**递归收集全部叶子，按叶子自身 z 全局升序（稳定）展开（v1.3 钉死）**，容器 z 不参与拍平排序——单一容器 z 无法再现 z 不连续编组在全局序中的交错（z=0、2 夹 z=1），唯此定义使 FR-03① 对任意选择成立；对无 children 文档与现状 `sorted_items`（`model.py:110-116`）逐位相同 → 既有 golden 不变。实证基础（§12 #7，实跑 §13.1）：extract_docx 夹具 z 稳定序 [0,2,1,3,4,5,6]≠列表序（z 赋值 `doc_import.py:94/:139/:159`）；`wrap_group` 保持叶子 z，FR-07② 等价随即成立。`export_svg.document_to_svg`（`export_svg.py:27-43`）零改动自动生效。
**验收**：`tests/test_gui_layout.py:132-155` roundtrip golden 扩树用例（嵌套容器 → 导出 → `parse_svg` + `paper_from_svg_ydown` 回读**含折线顺序**逐点恒等）；新增 z 交错编组夹具（0、2 夹 1）编组前后拍平顺序 golden。

### FR-03 编组/解组模型 API + 选择语义（对应 T3，1.5 人日）

`Document.group_items/ungroup`：编组把所选项收进新容器（几何恒等重组；**不设「容器 z 归位」**——v1.3 删除该未定义操作，拍平按全局叶子 z，FR-02）；解组精确恢复原几何。选择语义：点组内子项先选中整组；双击进组选子项（组编辑态，操作单元规则见 FR-05）。
**验收**：offscreen 测试证①编组/解组前后 `flatten_visible(doc)` **含折线顺序**逐点恒等对**任意选择（含 z 不连续）**成立（全局叶子 z 口径 FR-02；新增 z=0、2 夹 z=1 夹具）；②选中/双击行为符合 US-1（进 `tests/test_gui_layout_window.py`）。

### FR-04 树/组 undo 命令族（对应 T4，1.5 人日）

`GroupCommand`/`UngroupCommand`（重建式 `make_gi`，`undo_cmds.py:46-56`）；`Add/Remove/Move/ChangeItemProps` 对树的适配——拖组 = 子项漂移收集进**一条**命令（**v1.2 裁决：选定语义**，与 FR-07 一致；复用既有机制：多选拖动收集 `items.py:120-138`、多选整体平移 `layout_page.py:464-480`、多选等比缩放 `handles.py:6-8/:150-166`）。遵守既有契约：**命令只写 model.Item、`_sync_gi` 唯一同步入口**（`undo_cmds.py:1-11` docstring）。
**验收**：offscreen 测试证编组后拖动整组为一条可撤销命令；`tests/test_gui_layout_window.py` 全部 21 个既有用例（撰写会话 `grep -c "^def test_"` = 21）不破。

### FR-05 场景层组适配（对应 T5，2 人日）

扁平场景 + 组框 overlay（仿 `SelectionHandles` 自管 gi 模式，`canvas/handles.py:28-65`）；**不引入 QGraphicsItem 父子**，保住 `items.py:129-134`（`commit_move` 读 `gi.pos()`）与 `handles.py:122-127`（`pivot` 读 `gi.pos()`）的父坐标语义安全。组框命中/框选/Ctrl 多选纳入。**组选中 = 叶子集（v1.2）**：点组内子项 → 选中容器全部叶子，组级移动/缩放/旋转由此走既有多选机制（`handles.py:150-166`、`layout_page.py:464-480`），组框 overlay 只做显示与命中，**不新增容器变换渲染通路**（理由见 FR-07）。**操作单元规则（v1.3，按选择态二分）**：普通选择态混选只能是「整组 + 组外散件」——组按容器 `page_bbox` 参与、散件按自身 bbox，落在组上的结果 = 叶子集一条命令同步更新，组内布局保持；组编辑态（双击进组后）操作单元 = 叶子，与组外图元混选按各选中单元自身 bbox——此时改变组内布局是进组的显式意图。
**验收**：组 bbox overlay 随编辑刷新；`test_gui_layout_window.py` 全部通过（无 Qt parent 引入 = `blueprint.md:149` 坑不触发）。

### FR-06 吸附候选域修复 + 编辑操作按组语义适配（对应 T6，1.5 人日）

**前置修复（v1.1 增）——吸附候选域 bug**：绘制工具候选 `_all_paths`（`layout_page.py:384-385`）返回**局部坐标** `it.paths`，而 `_snap_pt` 喂入页面坐标（`layout_page.py:381`），与拖动侧页面域候选 `transformed_paths()`（`canvas/items.py:108-110`）不一致。离屏探针复现（§13.1）：`pos=(50,60)` 图元，`_all_paths()` 给 `[(0,0),(20,0)]`，页面点 (51,61) 吸附零命中；页面域候选则命中 (51.0,60.0)。既有 `test_grid_snap_draw_tool_and_item`（`tests/test_gui_layout_window.py:294-331`）空文档只验网格（:320-321 自证）掩盖该 bug。本 FR 先把候选源切到页面域（`transformed_paths`），再谈容器展开；吸附 pitch 联动与 `SnapEngine`（`canvas/snap.py:104-132`）不动。
其后做组语义适配：对齐/分布/层序（`layout_page.py:633-704`）把组当整体（组 bbox = 容器 `page_bbox`；对齐/分布 = 叶子集按组 bbox 整体平移，层序 = 组内叶子 z 同步更新且保持组内相对次序，均**一条**命令，与 FR-04 语义一致；操作单元按 FR-05 选择态二分）；复制/粘贴/副本剪贴板序列化递归化（现为逐 Item 平面 JSON，`layout_page.py:594-629`）；吸附候选含容器展开（页面域）。
**验收**：①新增 offscreen 用例：`pos≠(0,0)` 图元旁绘制时对象吸附在页面坐标命中（上述探针场景即验收样例：查询 (51,61) → 吸附到 (51,60)）；②编组参与对齐/分布按组 bbox 计算；③组复制粘贴后几何与层序恢复；④既有拖动回写/吸附/数值定位回归不破（`test_gui_layout_window.py` 全部，含 :294 用例改后仍绿）。

### FR-07 Word/Excel 容器包装（对应 T7，拆 T7a/T7b，共 1.5 人日）

**v1.1 修订**：`_finish`（`doc_import.py:163-184`）**不改产容器、extract 平铺返回契约不变**——若只返回 `[容器]`，冻结用例必挂：`_yc/_xc`（`tests/test_gui_doc_import.py:87-98`）顶层名字查找直接 StopIteration，`len(items) >= 3`（:41-53）同样挂。改为：
- 新增纯函数 `wrap_group(items) -> Item`（落 `doc_import.py` 或 `model.py`）：**恒等容器包装**——`paths=[]`、`pos=(0,0)`、`scale=1.0`、`angle_deg=0.0`，`children` = 原 Item 集；子项数据零改写，由 FR-01 合成数学（父恒等 ∘ 子变换）保证 `flatten_visible([容器])` 逐点等于 `flatten_visible(平铺列表)`；
- 「严禁逐 Item 归位」导入期语义仍由既有 `_finish` 组导入契约承担（`layout_page.py:93-138` 不动）；容器把「相对布局不散」升级为结构属性——**靠结构而非容器变换（v1.2 裁决）**：容器恒为恒等、不承载用户变换，组移动/缩放/旋转 = 叶子变换集合（一条命令，FR-04）。理由：D1 扁平场景下容器变换无通路——容器无 gi 则画布不可见；烘进叶子渲染则破坏 `items.py:84-92`（只渲自身模型态）与 `undo_cmds.py:1-11`（场景≡纸面）契约、`items.py:129-134` 漂移比对产生幻影命令；引入容器 gi 违反 D1/`blueprint.md:149`；
- 调用侧接线（`_add_doc`，`layout_page.py:811-821`）**放 M2 与场景/undo 树适配同批翻转**（T7b，见 §9），M1 只落纯函数与 golden（T7a）——消除 M1→M2 功能回退窗口。
**验收**：①`tests/test_gui_doc_import.py` 全部用例**一字不改**通过（返回契约未变）；②新增等价 golden：对 extract 产物，`flatten_visible([wrap_group(items)])` ≡ `flatten_visible(items)` **含折线顺序**逐点恒等；③（M2·T7b）`_add_doc` 包装后画布整组可见可拖，行序正立 + 相对布局保持。

### FR-08 水平/垂直镜像（对应 T8，1.5 人日）

`Item` 增 `mirror_x/mirror_y` 布尔；**镜像支点 = 本地 bbox 中心（一般式，v1.3 定义）**：`coords.py` 新增纯函数 `mirror_scalar(v, lo, hi) = flip_y_scalar(v − lo, hi − lo) + lo`（`coords.py:46-52` 复用唯一翻转实现，垂直/水平同式），合成顺序 mirror→scale→rotate→translate（无 mirror 数学不变）。**不依赖 `normalize_local` 不变量**（`model.py:9-11/:129-130` 明言其只在创建/导入入口调用，y0≠0 的直接构造 Item 同样正确）；**pos/scale/angle 不补偿**（原位镜像：内容绕自身中心像点翻转，仅 mirror 标志变更）；y0=0（归一入口常态）时退化等价于 `flip_y_scalar(y, bbox 高)`。`PathItem.rebuild_path`（`canvas/items.py:71-82`）烘镜像坐标显示（镜像绕中心保 bbox，`boundingRect` 稳定）；工具条按钮 + `ChangeItemPropsCommand` 撤销（setattr 通道 `undo_cmds.py:182-188` 现成）；导出/JobSpec 链端到端生效。
**验收**：①镜像与手算值一致——归一（y0=0）与非归一（y0≠0）Item 各一对 golden（含 scale/angle≠1/0 组合）；②导出 SVG 回读镜像正确（roundtrip 对合）；③`tests/test_coords.py:148-154/:277-371` 翻转/210 扫描零命中不破；④镜像几何端到端进 JobSpec（贴纸转印可用，最终目验**需现场**）。

### FR-09 多页管理（对应 T9，3 人日）

**当前页门面（v1.1 增，解决兼容迁移）**：`Document.items` 被多处隐含当作「唯一图元集」——`document_to_svg(doc)` 经 `flatten_visible`/`items_visible` 直读（`export_svg.py:27-43`、`model.py:114-116/:149-156`）、`ClearCommand.saved = list(page.doc.items)`（`undo_cmds.py:232`）、`make_gi` 的 `item not in page.doc.items`（`undo_cmds.py:54`）、`layout_page` 全文 `self.doc.items`。故 `Document` 容器化 `pages: list[Page]`（`Page` = items/bed_w/bed_h，改造 `model.py:93-123`）必须带**当前页门面**：`items`/`bed_w`/`bed_h`/`top_z`/`bottom_z`/`add`/`remove`/`items_visible()` 委托当前页——**默认单页 Document 行为逐位不变**，上述消费点与 Document 级测试无需改动即作用于当前页。据此修正「零改动」声明：`export_svg.py` 零改动**在该门面下成立**（设计约束：门面不满足则须改 export_svg 并解冻 Document 级测试）。页切换 = 换当前页 + 场景全量重建（清场景 → 逐 item `make_gi`，`undo_cmds.py:46-56`）；undo 命令带页归属（跨页撤销不串页，§11 问题 2）。
页签 UI（增/删/复制/切换/重排，`layout_page.py`）；导出/送作业**按当前页**（`to_job_spec` `layout_page.py:946-961`、越界预检 `layout_page.py:979-990`——门面下现实现即作用于当前页）；作业页衔接经既有 JobSpec 通道（`main_window.py:1534-1543` `_on_layout_export`、`main_window.py:1751` `_recompile`），**不改 `_recompile` 触发语义**。
**验收**：①默认单页下 `tests/test_gui_layout.py` 全部 Document 级用例不改字通过（门面逐位不变的可执行证明）；②页签增/删/复制/切换/重排可用，切换后场景与当前页一致；③每页独立编辑与撤销互不串页；④导出与送作业取当前页且单 SVG 契约（`PRD_layout.md:120` 决策 5）不破；⑤越界检查按当前页；⑥多页送作业执行**需现场**。

### FR-10 图片就地二次追踪 + 照片/素描模式（对应 T10+T11，2.5 人日）

`Item.image_spec`（源路径/模式/阈值/多阈值/target_mm）；`RetraceImageCommand`（仿 `EditTextCommand` 的 old/new paths+spec 结构，`undo_cmds.py:197-221`）；双击/右键重追入口；`_add_image`（`layout_page.py:731-791`）补记 spec（现仅名字留痕 `:789`）。
**算法（v1.1 修订）**：`cv2.Canny` 边缘图 → skimage `thin` 骨架化 → **复用既有 `_skeleton_paths` + `_dp_simplify`**（`edge_to_svg.py:57-148`），产出单像素宽中心线折线。**否决 `findContours/approxPolyDP` 路线**：Canny 输出 1px 边缘带，findContours 沿带两侧各出一条轮廓（≈双线），与「单像素宽」验收冲突，且本仓库 centerline 管线正是为「消双线」而建（`edge_to_svg.py:1-8`）。新函数入 `edge_to_svg.py` 或新模块（cv2 已声明 `requirements-gui.txt:6`），接入加图模式下拉（`layout_page.py:739-742` 旁新增「照片/素描（Canny+骨架）」）。重追保持 pos/scale/angle。
**验收**：①对已导入图片改阈值/模式就地重追且可撤销；②Canny+骨架模式对合成照片样张（测试内合成，无需真图）产出单像素宽折线 golden 通过；③`pos/scale/angle` 不变量断言。

---

## 6. 非功能需求

- **NFR-1 零新增核心依赖**：Canny 复用 `requirements-gui.txt:6` 已声明的 `opencv-python>=4.10`；其余全 stdlib/PySide6。
- **NFR-2 回归面冻结**：全部既有 golden/断言一字不改（`test_gui_layout.py:20-37/:132-155`、`test_gui_doc_import.py:101-135`、`test_gui_layout_window.py` 21 用例、`test_coords.py` 各扫描）；收口时全量 ≥217 基线 + 新增全绿（验收命令见 §1 基线注记）。
- **NFR-3 坐标单源不破**：镜像数学必须落 `coords.py` 纯函数；全树出现手写翻转（如 `(x,-y)`/`span−y`）按 bug 处理（`tests/test_coords.py:277-302` 零容忍 + `:305-327` 符号级 + `:330-371` 210 常量扫描钉死）。
- **NFR-4 分层不破**：`model.py`/`snap.py`/`undo_cmds.py` 契约保持纯逻辑可单测；Qt 绑定仍薄（`canvas/` 11 个 .py 共 1592 行现状，撰写会话 `wc -l` 实测）。
- **NFR-5 性能**：递归变换每帧仅在选择/拖动路径触发；千段折线画布可用性不劣于现状；不承诺帧率数字（蓝图 :452 同口径）。
- **NFR-6 UI 中文**，文案与现 GUI 一致。

---

## 7. 总体方案

### 7.1 数据流定位（改动只碰几何来源）

现有构造链不动：`JobSpec`（`gui/job.py:269`，`paths_paper:276`）→ `compile_job`（`job.py:389`）→ `lines` 唯一真源 → worker 发送（`gui/worker.py:250` `run_job`）+ 预览回读。本候选的全部改动只改变 **`JobSpec.paths_paper` 的几何来源**——即 `flatten_visible` 的递归化与 `to_job_spec`（`layout_page.py:946-961`）的当前页化；`compile_job`/`gcode_parse`/`worker` 全不动。串口链（guard/transport/dialect）零触碰。

### 7.2 模块落点

| 层 | 文件 | 改动 |
|---|---|---|
| 纯逻辑模型 | `gui/layout/model.py`（156 行） | Item 树化、Document.pages、递归 bbox/transformed_paths/flatten_visible、mirror 字段、image_spec |
| 导出 | `gui/layout/export_svg.py` | **零改动**（吃 `flatten_visible` 输出自动递归生效；多页下该声明在 FR-09 当前页门面下成立） |
| 导入 | `gui/layout/doc_import.py` | 新增 `wrap_group` 恒等容器包装（`_finish`/extract 平铺返回契约不变，:163-184） |
| 坐标权威 | `gui/canvas/coords.py` | 新增水平镜像纯函数（垂直复用 `flip_y_scalar:46-52`） |
| 图片算法 | `gui/edge_to_svg.py`（或新模块） | Canny→thin 骨架化→复用 `_skeleton_paths` 游走（:57-148）新函数（否决 findContours 双线路线，见 FR-10） |
| Qt 画布 | `gui/canvas/items.py`、`handles.py`、`undo_cmds.py`、`snap.py` | rebuild_path 烘镜像（:71-82）；组框 overlay；Group/Ungroup/Retrace 命令；吸附候选展开 |
| 编排 | `gui/layout/layout_page.py`（1063 行） | 编组/镜像/多页/重追 UI 与命令编排、`to_job_spec` 当前页化、`_add_image` 补 spec |
| 上层 | `gui/main_window.py`（1995 行） | 仅 `_on_layout_export`（:1534-1543）透传当前页 spec，经 `_recompile`（:1751）既有汇流 |
| 测试 | `tests/test_gui_layout.py`、`test_gui_doc_import.py`、`test_coords.py`、`test_gui_layout_window.py` 及新增 | 既有断言不改字 + 新增树/组/镜像/多页/重追用例 |

### 7.3 关键设计决策

- **D1 树化不改场景结构（扁平场景 + 组框 overlay）**。蓝图明文：`pos()` 是唯一返回父坐标系的接口，"一旦引入 parent item（如成组）即错位"（`blueprint.md:149`）；而 `items.py:129-134` 与 `handles.py:122-127` 均按 `gi.pos()`（父坐标语义）读场景——引入 Qt 父子须重写全部读取面并重验全部 golden。故模型树 + 扁平场景 + 组框 overlay（自管 gi，仿 `SelectionHandles` `handles.py:68-114`）。
- **D2 变换合成保旧数学**：容器变换合成 = 父∘子；无 children 的 Item 走原路径逐位不变，由既有 golden 一字不改钉死（`test_gui_layout.py:28-37` 的 `(10,0)→(0,20)` 语义不因重构漂移）。
- **D3 镜像数学进 coords 单源**：合成顺序 mirror→scale→rotate→translate；按既有红线设计而非绕开——`test_coords.py:148-154` `_FLIP_PAT` 全树扫描会把任何手写翻转按 bug 处理。
- **D4 Word/Excel 容器等价性（v1.1/v1.2）**：extract 平铺返回契约不变（冻结载体 `test_gui_doc_import.py:87-98/:41-53`，改产容器必挂）；等价性由 `wrap_group` 恒等包装 golden 承担——`flatten_visible([容器]) ≡ flatten_visible(平铺)`（含折线顺序，FR-02 z 序口径），依据 FR-01 合成数学；「严禁逐 Item 归位」（`layout_page.py:99`）导入期由既有组契约承担，容器把相对布局升级为结构属性（FR-07 裁决）。调用侧翻转放 M2（§9）。
- **D5 多页保单 SVG 契约**：`PRD_layout.md:120` 决策 5「模型拍平 + y 翻转 → 单 SVG」只约束单次导出形态，故多页采用「按当前页导出/送作业」。
- **D6 二次追踪仿 text_spec 先例**：`model.py:56` 的 `text_spec` + `undo_cmds.py:197-221` `EditTextCommand` 是已验证的「参数可重编 + 撤销换 paths」模式，image_spec/RetraceImageCommand 照抄结构。

---

## 8. 选题对比（为何工作量最大）

评审按三准则（常识修正后的子任务规模、跨模块改动深度、集成与测试成本）判定，本目标（候选 2）居首：

1. **改动面最深**：核心模型重写（Item 树化、递归化、Document 容器化 pages）横跨纯逻辑（model/export_svg/doc_import/coords/edge_to_svg）、Qt（canvas）、编排（layout_page 1063 行、main_window 1995 行）约 10–12 个既有文件，且 4 个特性簇互相踩踏——树化改变一切几何来源，其余全建其上（M1 是 M2–M5 硬前置）。
2. **测试成本最高**：重构核心同时冻结全仓最密集回归面（transform/roundtrip golden、doc_import 行序、layout_window 21 用例、coords 静态扫描），并为嵌套变换/容器等价/重追补新 golden。
3. **规模修正后居首**：名义 12 任务粒度粗（1.63 人日/任务），仅 T9 重拆即 ≈6–8 子项，总数超过名义 18 子任务的候选 1；effortDays **19.5 为各候选最高**（每页 undo、Qt parent 坑规避、容器等价性证明均易低估）。
4. **对照**：相机叠加（候选 1）18 子任务但多为 ≤0.5d 胶水项、加法为主不重构契约；run_job（候选 3）硬前置是解封 worker 流控红线；断点续跑（候选 4）self-contained；XY skew（候选 5）代码面最窄；真机验收（候选 6）代码量≈0。

（引自评审记录；行数、grep 零命中、217 基线等撰写会话已复核，见附录。）

---

## 9. 里程碑计划（合计 19.5 人日）

| 里程碑 | 内容 | 人日 | 验收要点 |
|---|---|---|---|
| **M1 模型树化落地（纯逻辑）** | T1 树化地基（2.5）+ T2 递归拍平/导出（1）+ T7a `wrap_group` 纯函数与等价 golden（1） | 4.5 | 全量回归绿；`test_gui_layout.py:20-37/:132-155` 与 `test_gui_doc_import.py` 全部断言一字不改通过；嵌套/容器 roundtrip/`wrap_group` 等价 golden 通过。**纯逻辑收口、无用户可见行为变化**——`_add_doc` 不接线，导入平铺行为保持，回退窗口 = 0（否决「M1 直接产容器」：场景/undo 适配在 M2，且 `make_gi`（`undo_cmds.py:46-56`）对容器只建空 PathItem → 画布不可见，窗口测试零 doc_import 覆盖不会拦截，见 §12 #5） |
| **M2 编组可用（含容器接线翻转）** | T3 编组/解组 API（1.5）+ T4 undo 命令族（1.5）+ T5 场景组适配（2）+ T6 吸附域修复+组语义适配（1.5）+ T7b `_add_doc` 调用侧包装翻转（0.5） | 7 | offscreen 证编组后拖组=一条可撤销命令；对齐/分布/层序按组 bbox；解组精确恢复原几何；吸附候选域修复用例（pos≠0 对象命中）+ `test_gui_layout_window.py` 全部不破；`_add_doc` 包装后 Word/Excel 整组可见可拖、行序正立+相对布局保持 |
| **M3 镜像交付** | T8 | 1.5 | `transformed_paths` 对手算值；导出回读对合 roundtrip；`test_coords.py` 扫描零命中；镜像几何端到端进 JobSpec（贴纸转印最终目验**需现场**） |
| **M4 多页管理** | T9 | 3 | 页签增/删/复制/切换/重排；每页独立编辑与撤销；导出与送作业取当前页、单 SVG 契约不破；越界按当前页（送作业执行**需现场**） |
| **M5 图片二次追踪** | T10 模型/命令/UI（1）+ T11 Canny 算法（1.5） | 2.5 | 就地重追保持 pos/scale/angle 且可撤销；Canny 对合成样张单像素宽折线 golden |
| **M6 收口** | T12 全量回归 + 文档 | 1 | 全量 ≥217 基线 + 新增全绿；AGENTS.md/REPORT.md/PRD_layout.md §4.2 状态注记更新（四项标记已实现）；翻转与 210 静态扫描（`test_coords.py:277-371`）零命中复核 |

依赖序：M1 必须最先（M2–M5 全踩其上）；M2 内 T3+T5 是 T6 前置，T7b 在 T4/T5 之后收口（场景/undo 树适配就绪才翻转调用侧）。合计 4.5+7+1.5+3+2.5+1 = **19.5 人日**（T7 拆 T7a/T7b 不增总量，与 v1.0 相同）。

---

## 10. 风险与红线冲突

### 10.1 红线核对（逐条）

1. **guard/transport/dialect（AGENTS.md「一字不改」）**：改动面全在 `gui/layout`、`gui/canvas`、`gui/edge_to_svg` 与 `layout_page/main_window` 排版分支及测试，串口链零触碰——无前置决策。
2. **worker 流控（执行期 M114 红线）**：不涉及——预览≡发送构造不变，worker 全不动。
3. **coords 翻转单源**：镜像按既有红线落 `coords.py` 纯函数（D3），是遵守设计而非绕开。
4. **Qt parent 坑（`blueprint.md:149`）**：默认档案走扁平方案（D1），无前置决策；**若实施者改选 QGraphicsItem 父子方案**，`items.py`/`handles.py` 的 scenePos() 改写与全部 golden 重验须列为【**需用户拍板的前置决策**】，不得默认推翻。
5. **「严禁逐 Item 归位」契约（`layout_page.py:99`、`doc_import.py:9/:168`）与 `Document.items`「唯一图元集」隐含语义（`undo_cmds.py:54/:232`、`export_svg.py:27-43`）**：前者由既有组契约继续承担 + `wrap_group` 恒等包装升级为结构属性（FR-07，冻结用例不改字）；后者由 FR-09 当前页门面承接——均设计内处理，无前置决策。
6. **单 SVG 契约（`PRD_layout.md:120` 决策 5）**：多页按当前页导出保契约；跨页批量导出与之冲突，本期不做，要做属【**需用户拍板的前置决策**】。
7. **undo 契约**：新命令必须遵守「命令只写 model.Item、`_sync_gi` 唯一同步入口」（`undo_cmds.py:1-11`）。
8. **JobSpec 内存唯一源 + `_recompile` 唯一汇流（`main_window.py:1751`）**：多页切换同步作业预览走既有 JobSpec 重建，不改触发语义；执行中 no-op + 完成后补编译语义保持。

### 10.2 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 嵌套变换数学错（父∘子顺序、嵌套语义） | 高 | 嵌套 golden 手算先行（§11 问题 1）；无 children 数学由既有 golden 钉死 |
| 回归面大（冻结 21+ golden） | 高 | 每里程碑全量回归；先写等价性测试再动 doc_import |
| Canny 对真实照片效果未知 | 中 | 先合成样张 golden 保算法正确；真机效果验收标「需现场」，参数暴露可调 |
| 千段折线下递归拍平开销 | 中 | 递归仅在选择/拖动/导出路径触发；TiledPathItem 仍按蓝图不做（接口预留） |
| 多页 undo 串页 | 中 | 页感知命令过滤或每页栈（§11 问题 2），M4 验收含互不串页断言 |
| 已知硬件问题（ok≠已移动、失步，AGENTS.md §机器事实） | 外部 | 与本候选无关；软件侧照旧 Frame/点检/空跑兜底 |

---

## 11. 开放问题

1. **嵌套容器（组中组）支持范围**：拍平已改全局叶子 z（FR-02）、合成为一般式（FR-01 防御性 pin），嵌套天然支持；是否需要 `normalize_local` 递归重锚与深度上限，M1 以嵌套 golden 定稿。
2. **多页 undo 形态**：每页独立栈 vs 单栈 + 命令页归属（FR-09 门面下均可行，均不改 undo 契约），M4 定稿；页删除/复制的页级命令一并定稿。
3. **Canny 默认参数**（高/低阈值、aperture）：以合成样张 golden 定基线，真机效果调优**需现场**。
4. **镜像与既有 angle 交互的 UI 呈现**：镜像后角度正负号观感是否需要提示文案（不影响数学，golden 钉死）。

---

## 12. 修订记录（v1.0 → v1.3，依据评审员对照代码核验意见）

| # | 评审发现 | 核验结果（撰写会话执行） | 修订落点 |
|---|---|---|---|
| 1 | FR-07/D4/M1 矛盾：`_finish` 改产单容器后冻结用例必挂 | `test_gui_doc_import.py:87-98`（`_yc/_xc` 顶层名字查找，容器下 StopIteration）、:41-53（`len(items)>=3`） | extract 平铺返回契约不变 + `wrap_group` 恒等包装 + 等价 golden；调用侧翻转移 M2（T7b） |
| 2 | FR-06 建立在 `_all_paths` 局部域吸附 bug 上而不自知 | 离屏探针复现（§13.1）：局部域候选对 (51,61) 零命中、页面域命中 (51.0,60.0)；候选源不一致 `layout_page.py:381/:384-385` vs `items.py:108-110`；`test_gui_layout_window.py:294-331` 空文档只验网格掩盖 bug | FR-06 增前置修复（候选源切页面域）+ 新增验收① |
| 3 | FR-09 缺 Document 容器化兼容迁移，与「export_svg 零改动」+ 冻结测试不能同时成立 | `export_svg.py:27-43`、`model.py:114-116/:149-156`、`undo_cmds.py:54/:232` 均隐含「items = 唯一图元集」 | FR-09 增当前页门面（默认单页逐位不变）；「零改动」改为门面下设计约束；验收增① |
| 4 | FR-10 Canny→findContours→approxPolyDP 与「单像素宽 golden」冲突（双线） | `edge_to_svg.py:1-8` docstring：该管线为「消双线」而建 | 算法改 Canny→thin 骨架→复用 `_skeleton_paths`/`_dp_simplify`（:57-148），明文否决 findContours；验收②不变且自洽 |
| 5 | M1 分解不自洽：T7 产容器但场景/undo 适配在 M2，中间态回退且回归不拦 | `undo_cmds.py:46-56`（make_gi 每 doc.items 项建一个 PathItem，容器 paths 空不可见）；窗口测试零 doc_import 覆盖（grep=0） | T7 拆 T7a（M1，1d）/T7b（M2，0.5d）；M1 纯逻辑收口、回退窗口=0；总量 19.5 不变 |
| 6 | FR-07 与 FR-04「拖组」互斥（容器变换 vs 子项漂移），容器变换在扁平场景无通路 | `items.py:84-92`（只渲自身模型态）、`:129-134`（`gi.pos()` 漂移比对）、`undo_cmds.py:1-11`（场景≡纸面契约）、`handles.py:6-8/:150-166`（多选公式即现成通路） | 裁决：组变换 = 叶子集合（一条命令），容器恒为恒等；FR-07/D4/术语改写，FR-04 标注选定语义，FR-05 增组选中=叶子集；工作量不变 |
| 7 | FR-02「子按序」未定义，列表序≠z 序，恒等 golden 必挂 | 实跑夹具（§13.1）：z 稳定序 [0,2,1,3,4,5,6]≠列表序（z 赋值 `doc_import.py:94/:139/:159`）；`flatten_visible` 走 z 升序稳定（`model.py:110-116`） | FR-02 钉死「子按 z 升序（稳定）」+ 顺序 golden 验收；FR-03①/FR-07② 明确含折线顺序 |
| 8 | FR-03① 恒等与 FR-02「容器 z 先序」在 z 不连续编组下数学不可达（z=0、2 夹 z=1：单一容器 z 无法复现全局交错）；「容器 z 归位」未定义 | 几何推理 + 现状 `model.py:110-116/:149-156`（拍平 = 叶子 z 稳定序） | 拍平改**全局叶子 z 升序（稳定）**、容器 z 不参与（无 children 文档逐位同现状 → golden 不变）；删「容器 z 归位」；FR-03① 改「任意选择（含 z 不连续）」+ 交错夹具；组层序 = 叶子 z 集合更新（FR-06） |
| 9 | FR-01 一般式合成 + 父旋转/缩放 golden 与 v1.2「容器恒为恒等」裁决相龃龉（该状态无产品路径可达） | FR-01② 与 FR-07/术语裁决对照 | 声明为**防御性 pin** 并给动机（防递归实现按恒等特化漏父变换项；wrap_group 恒等依赖一般式正确）；FR-01/§11-1 同步 |
| 10 | FR-08「垂直复用 flip_y_scalar(y, 本地bbox高)」仅 y0=0 时成立，支点/pos 补偿未定义 | `model.py:9-11/:129-130`：normalize_local 只在创建/导入入口调用，非模型不变量 | 支点 = 本地 bbox 中心（一般式）；`mirror_scalar(v,lo,hi)=flip_y_scalar(v−lo,hi−lo)+lo` 复用唯一翻转实现；pos/scale/angle 不补偿（原位镜像）；验收① 改 y0=0/y0≠0 golden 对 |
| 11 | 双击进组单选叶子后与组外混选做对齐/分布/层序，操作单元未定义；按叶子算（现 `_align/_distribute` 口径 `layout_page.py:652-704`）会拆散组内布局 | `layout_page.py:652-704` 逐叶子计算现状 | 定义**选择态二分**：普通态操作单元 = 组（叶子集一条命令，布局保持）；组编辑态（双击）= 叶子（显式组内编辑）；FR-05/FR-06 同步 |
| 12 | §13.1 吸附探针记录缺 pitch 参数，不可精确复现 | 重跑三 pitch：0.5/1.0 → 局部 (51.0,61.0)/页面 (51.0,60.0)；默认（fit 后 4.0）→ 局部 (52.0,60.0)/页面 (51.0,60.0)；实质结论各 pitch 一致 | §13.1 补 pitch 设置与默认链路（`layout_page.py:180/:241/:365-373`） |

---

## 13. 附录：证据清单

### 13.1 撰写会话实跑命令与输出

| 命令 | 输出 |
|---|---|
| `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -m pytest -q --basetemp=.pytest_tmp -p no:cacheprovider` | `217 passed, 10956 warnings in 15.41s`（基线；`.pytest_tmp/` 为 AGENTS.md 规定的 git-ignore 临时目录） |
| `grep -rn -E "Canny\|findContours\|approxPolyDP" src/ \| wc -l` | `0` |
| `grep -rni "camera" src/ \| wc -l` | `0` |
| `wc -l src/megapro/gui/main_window.py src/megapro/gui/layout/layout_page.py src/megapro/gui/layout/model.py src/megapro/gui/edge_to_svg.py` | 1995 / 1063 / 156 / 224 行 |
| `wc -l src/megapro/gui/canvas/*.py` | 11 个 .py 共 1592 行 |
| `grep -c "^def test_" tests/test_gui_layout_window.py` | `21` |
| 吸附域探针（§12 #2/#12）：`QT_QPA_PLATFORM=offscreen PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -c "…LayoutPage + Item(paths=[[(0,0),(20,0)]], pos=(50,60))；lp.snap_pitch=P；eng.snap((51,61), lp._all_paths()) 与 eng.snap((51,61), transformed_paths)"` | `_all_paths` = `[[(0,0),(20,0)]]`（局部域）、`transformed_paths` = `[[(50,60),(70,60)]]`（页面域）；**P=0.5/1.0**：局部域 → (51.0,61.0)（对象零命中）、页面域 → (51.0,60.0)（命中边）；**默认 P**（`__init__` 经 `_build_ui`→`_sync_snap_pitch` 按 fit 后 ppm 重算，`layout_page.py:180/:241/:365-373`，环境相关，本环境 4.0）：局部域 → (52.0,60.0)（网格回退）、页面域 → (51.0,60.0)。「局部零命中 vs 页面域命中」结论各 pitch 一致 |
| `grep -n "doc_import\|_add_doc\|DocImport" tests/test_gui_layout_window.py \| wc -l` | `0`（tests 全树 `grep -rn … tests/` 命中 9 处，均在 doc_import 自身测试） |
| z 序 vs 列表序探针（§12 #7）：offscreen 跑 extract_docx 标准夹具（`tests/test_gui_doc_import.py:13-25` 同 fixtures），对比列表序与 `items_visible()` 序 | 列表序 z = [0,1,0,1,1,1,1]；z 稳定序(索引) = [0,2,1,3,4,5,6]；`items_visible()` = [段落:你好 World, 表1:网格, 段落:第二段, 格:A1, 格:B1, 格:A2, 格:B2] |

### 13.2 代码证据（path:line）

- 平面模型现状：`src/megapro/gui/layout/model.py:48-56`（Item 字段，无 children）、`:66-82`（transformed_paths）、`:93-123`（Document，无 pages）、`:125-146`（normalize_local）、`:149-156`（flatten_visible）、`:56`（text_spec 先例）
- 导出单 SVG：`src/megapro/gui/layout/export_svg.py:27-43`
- Word/Excel 平铺导入与组契约：`src/megapro/gui/layout/doc_import.py:163-184`、`:9/:168`；`src/megapro/gui/layout/layout_page.py:93-138`、`:99`
- 编辑操作：`layout_page.py:633-704`（层序/对齐/分布）、`:594-629`（复制/粘贴/副本平面 JSON）、`:384-385`（_all_paths）、`:389-396`（选择）、`:731-791`（_add_image，`:789` 仅名字留痕、`:739-742` 模式下拉）、`:946-961`（to_job_spec）、`:979-990`（越界预检）
- 画布/undo：`canvas/items.py:71-82`（rebuild_path）、`:129-134`（commit_move 读 gi.pos()）；`canvas/handles.py:68-114`（自管手柄）、`:122-127`（pivot 读 gi.pos()）；`canvas/undo_cmds.py:1-11`（契约）、`:46-56`（make_gi）、`:103-146`（Move 手势 token）、`:182-188`（setattr 通道）、`:197-221`（EditTextCommand 先例）；`canvas/snap.py:104-132`
- 坐标单源：`canvas/coords.py:34`（210 唯一）、`:46-52`（flip_y_scalar）
- cv2 现状：`gui/edge_to_svg.py:29-44`（仅 cvtColor/resize）
- 作业链不动面：`gui/job.py:269/:276/:389`（JobSpec/paths_paper/compile_job）；`gui/worker.py:52/:250`（sequenceDone/run_job）；`main_window.py:1534-1543`（_on_layout_export）、`:1751-1779`（_recompile）
- 测试锚点：`tests/test_gui_layout.py:20-37`（transform golden）、`:132-155`（roundtrip golden）；`tests/test_gui_doc_import.py:101-135`（行序断言）；`tests/test_coords.py:148-154/:277-302/:305-327/:330-371`
- 修订核验新增（v1.1）：`tests/test_gui_doc_import.py:41-53`（`len(items)>=3` 冻结载体）、`:87-98`（`_yc/_xc` 顶层名字查找）；`tests/test_gui_layout_window.py:294-331`（空文档网格吸附用例，`:320-321` 自证注释）；`canvas/items.py:108-110`（拖动侧页面域候选源）；`undo_cmds.py:54/:232`（Document.items 唯一图元集隐含语义）；`edge_to_svg.py:1-8`（消双线设计目标）/:57-148（`_skeleton_paths`/`_dp_simplify` 可复用件）
- 修订核验新增（v1.2）：`doc_import.py:94/:139/:159`（段落/网格/格的 z 赋值，z 序≠列表序的来源）；`canvas/items.py:84-92`（`apply_model_state` 只渲自身模型态）；`canvas/handles.py:6-8`（多选等比公式契约）/:150-166（多选缩放/旋转实现）；`layout_page.py:464-480`（`_apply_pos` 多选整体平移）；`canvas/items.py:120-138`（多选拖动漂移收集）
- 修订核验新增（v1.3）：`model.py:9-11/:129-130`（normalize_local 只在创建/导入入口调用，非模型不变量）；`layout_page.py:180/:241/:365-373`（snap_pitch 初始值与 fit 重算链）；`layout_page.py:652-704`（现 `_align/_distribute` 逐叶子口径，操作单元裁决依据）
- 文档锚点：`PRD_layout.md:73-74`（后续清单四项）、`:120`（决策 5 单 SVG）；`docs/preview-layout-blueprint.md:149`（Qt parent 坑）、`:442-453`（§9 明确不做）；`requirements-gui.txt:6`（opencv-python>=4.10）

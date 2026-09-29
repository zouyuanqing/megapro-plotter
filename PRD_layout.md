# PRD：文字/图片 → SVG + CAD 式排版页（M3 — 制作/排版工作流）

> **状态注记（2026-09-23，2026-09-29 更新）**：预览与排版重构已落地（阶段 1–5），
> 排版模型树化 M1–M6 亦已落地（见 §4.2 与
> `docs/PRD_layout_model_tree.md`）。本文中的流程/约定若有与代码不符处
> **以 `AGENTS.md` 与 `docs/preview-layout-blueprint.md` 为准**（现行约定：
> 场景≡纸面 mm y-up、y 翻转只在 view_transform 与 SVG 互换层、组导入契约、
> 拖动手势 token 回写 undo、导出 y-down 对合）。**另**：本文 §1/§4.1/§6/§8 里
> 作为背景与里程碑写的「越界预检」，现为**默认拒绝的确认闸**而非软提醒 ——
> 详见 §4.2 的 2026-09-29 补充第 1 条。

- 日期：2026-09-07　·　版本：v0.1（草案，待审）
- 依附：`PRD.md`（上位机总 PRD v0.2，P0/P1 已完成并真机验收）。本 PRD 独立成文，定义「内容制作/排版」阶段。
- 目标设备不变：Mega Pro 210×210×205 行程，笔（写字）/ 刀（裁纸）Z-lift，Marlin 串口；现有 P1 文件作业流水线为消费端。

---

## 1. 背景与目标

现有 GUI 能 载入 SVG → 越界预检 → 写字/裁纸执行（P1 已验收）。但 SVG **从哪来**靠外部工具（Inkscape 等）。本阶段在**上位机内**直接完成「内容 → 可排版的矢量 → 送到机器」闭环：

1. **文字 → SVG**：输入中/英文，转成笔/刀可画的矢量路径。
2. **图片 → SVG**：导入位图，转成笔可描的线条画。
3. **CAD 式排版页**：在上位机**独立页面**中把多个文字块/矢量/图片组成一版，带**标准单位（mm）坐标格子**、**标定原点 = 0**、标尺、缩放/平移/吸附/数值定位，导出**一个 SVG** 交给现有作业流水线执行。

### 为什么值得做
- 写字机核心价值 = 写字 + 排版；脱离外部工具闭环后，铺纸→归位→选内容→排版→画 全流程一次完成。
- 现有 `parse_svg` 只吃几何（path/polyline/...），不吃 `<text>/<image>` → 排版页必须**先转成路径再排**，这正好是机器可画的唯一形态。

---

## 2. 研究结论（4 个 subagent，2026-09-07）

| 方向 | 结论 | 出处 |
|---|---|---|
| 文字→矢量 | **fontTools**（pip，纯 Python，Py3.14 OK）读 Windows CJK 字体（`msyh.ttc` 本地用、`fontNumber` 选面、glyf/CFF 通吃）→ `getBestCmap`+`SVGPathPen` 出 `<path>`；Y 翻转、hmtx 步进。**分发须用 Noto CJK**（MS 字体禁止转换分发） | fontTools docs、MS 字体 FAQ、Noto CJK |
| 中文单线 | 现成 **LingDong chinese-hershey-font**（MIT/OFL）：预生成单线中文字形（Heiti/Mingti .hf + JSON 折线，U+4E00 起 2 万+字） | github.com/LingDong-/chinese-hershey-font |
| 图片→线条 | **Pillow 阈值 → potrace CLI**（`-a 0` 纯多边形、`-t` 去噪；外置 exe，GPL 隔离）；照片/素描后续 OpenCV Canny→contours→approxPolyDP（cp37-abi3 wheel，Py3.14 OK） | potrace 手册、opencv-python |
| CAD 画布 | **QGraphicsView+QGraphicsScene**（mm 原生场景 + view 变换缩放）；grid 用 `drawBackground` 重写（随缩放 1/2/5×10^k 重定价）；标尺读 view transform；吸附 `itemChange`；**导出=模型重序列化**（不用 QSvgGenerator，避免 `<text>`/样式泄漏） | Qt Graphics View docs、diagramscene 例、QCAD |
| 集成现状 | GUI **无 tab**（需加 QTabWidget）；`_on_load_svg` 走文件对话框（排版成果送作业需接受 path 的小重构）；`parse_svg` 不吃 text/image → 排版页导出前全转路径；**y 约定**：toolchain 无 y-flip（SVG y 向下直进 G-code）→ 排版页屏幕 y 向上须导出时翻转 | Explore agent file:line 核对 |

---

## 3. 用户与场景

| 场景 | 流程 |
|---|---|
| 写一张贺卡/标签 | 排版页加文字块(中/英) → 调字体/字号/位置 → 设工件原点 → 送作业页 → 写字 |
| 图片线稿上纸 | 导入图 → potrace 转线条 → 缩放放到位 → 画 |
| 裁贴纸/卡片 | 文字/形状转路径 → 刀切轮廓（闭合）→ 裁纸 |

---

## 4. 范围

### 4.1 本期（M3）
**A. 文字 → 可画路径（两种模式，用户选定）**
- **轮廓空心字**（默认）：fontTools 读系统/Noto CJK → 每字形轮廓转闭合折线 → 输出 SVG `<path>`。中英通吃；UI 提示「≥8mm 大字清晰」。
- **单线手写体**（选项）：LingDong chinese-hershey-font 预生成字形（需打包字体数据文件随仓库），手写单线、可小字。
- 文本编辑：输入文字、选字体（系统 CJK / Noto / 单线体）、字号 mm、字距行距（v1 简单：固定行距 + 逐字步进）、对齐。

**B. 图片 → 线条 SVG（potrace v1）**
- 导入 PNG/JPG → 预览 → 参数（阈值、去噪 turdsize、多边形/曲线）→ Pillow 阈值 → potrace → 线条 SVG。
- potrace.exe 随附（README 注明来源/许可）。OpenCV 路径标注为「后续」。
- 输出是描边折线（fill=none），非填充区域。

**C. CAD 式排版页（QTab 双页，用户选定）**
- MainWindow 改 **QTabWidget**：Tab0「控制/作业」（现状整块迁入）、Tab1「排版/制作」。
- 画布：QGraphicsView+QGraphicsScene，**mm 原生坐标**；`drawBackground` 画**标准坐标格子**（1/2/5×10^k mm 随缩放重定价）；**标尺**（上/左，读 view transform）；**标定原点=0 标记**（页面 (0,0) 十字 + 210×210 床框）。
- 交互：滚轮缩放(至光标)、中键/空格平移、框选多选、拖拽移动 + **网格吸附**、数值属性栏（X/Y/W/H/旋转，mm/度）、方向键微调。
- 图元：SVG 导入（多个，各成可移动单位）、文字块（改字/字体/字号即时重生成路径）、图片（已追踪线条）、**对齐/分布**（v2 起）。
- **导出**：模型重序列化 → 单个 SVG（绝对 mm 坐标、路径已拍平、y 翻转到导出坐标系）→ 交作业页 `_on_load_svg` 执行（或直接送 job）。
- **送至作业**：排版页「送去执行」→ 临时 SVG → 现有流水线（含工件原点平移、越界预检、写字/裁刀）。

**依赖新增**：`fonttools`、`Pillow`（图片阈值）；potrace.exe 随附；单线字体数据随附。均纯 Python/外置二进制，不动核心依赖（pyserial）。

### 4.2 后续（标注不做本期）

> **2026-09-27 状态更新**：下列五项**均已实现**（`docs/PRD_layout_model_tree.md`
> 的 M1–M6 落地）。每项的**实际落地形态**与当初设想的差异记在括号里 —— 以代码
> 为准；本节保留原「不做本期」原文以留痕。
>
> **2026-09-29 补充（交付收口第 16 轮后）**：五项的实现**仍然成立**，本节只补
> 与实现**不一致 / 已过期**的口径。每条都注明是「读码」还是「本轮实测」。
> 1. **§5 决策 5 所依赖的「越界预检」已不是软提醒 —— 但只覆盖两条显式出口**。
>    原文 §1/§4.1 与 §8 里程碑里的「越界预检」现由 `layout_page._confirm_in_bed`
>    承担，是**默认拒绝**的确认闸（「送去执行」与「另存为 SVG」共用），取消/关窗
>    都不放行。原先记录的「越界预检只警告不拦截」已作废。
>    **两个必须知道的缺口**（本轮实测）：① 排版页编辑触发的**静默同步**这条路
>    **完全够不着这道闸**（`_confirm_in_bed` 的调用点只有「送去执行」与「另存为
>    SVG」），而作业页的放置若是「锚点归位」，`place_at_anchor` 会先把内容整体
>    归位、再做越界判定 ⇒ 越床几何被洗进床内、`violations=0`、run 门禁全绿。
>    ② 闸门口径比「真正送去切的」**保守**：它走 `unit_page_bbox`（不过滤可见性、
>    不丢弃退化折线），而 `flatten_visible` 两者都过滤 ⇒ 组内隐藏件、零尺寸折线
>    会造成**误拦**（方向恒为保守超集，**不会漏拦**）。详见 `AGENTS.md`「已知缺口」。
> 2. **镜像的 AABB 只在特定角度下变化 —— 而界面文案仍写成无条件**。§4.1 假设
>    「原位镜像 ⇒ bbox 稳定」，这在 `angle_deg` 为 90° 整数倍（含默认的 0°）与
>    轴对齐形状下成立，其余角度一般不等 —— 旋转件的宽/高会变。
>    **已落地的是**：宽/高 tooltip 已写明「= 图元在纸面上的轴对齐包围盒(AABB)，
>    不是图形边长」+「在此输入数值 = 以当前 AABB 为基准等比缩放」，镜像按钮
>    tooltip 已写明「多选时是逐个成员各绕自身中心分别镜像，不是把整组作刚性反射」
>    （读 `layout_page.py` 当前 tooltip 原文核对）。
>    **未落地的是**：`docs/PRD_layout_model_tree.md` §11 问题 4 的定稿明确要求
>    「**不得**写成无条件的『AABB 会变』」，但宽/高 tooltip 至今仍写着
>    「旋转或镜像后 AABB 会变…数字跳变属正常」（本轮实测 `sp_w.toolTip()` 原文）
>    —— 在 0°/90°/180° 与轴对齐形状下这句**逐位不成立**。数学未动，**只是文案
>    还欠一次收口**。
> 3. **组的镜像语义仍未定义**（本节第 3 条括号里的说法继续有效）：逐成员各绕自身
>    中心翻，不是整组刚性反射。
> 4. **「床只有一处存储」成立，但那个数今天没有任何生产消费者**。`Document` 的
>    `bed_w/bed_h` 是直通当前页的 property（读即读当前页、写即写当前页），
>    所以 `bed_w == page.bed_w` 的恒等式**不可违反**（结构事实，非纪律）。
>    **但**导出（`export_svg.py:41` 宽度**写死** `BED_W`）、越床闸
>    （`layout_page.py:2143`）、画布床框（`paper_scene.py:99/122`）三处生产链
>    **都读常量**，`git grep` 统计页级床表面在 `model.py` 之外命中 **0**。
>    ⇒ **本节「按当前页导出」指的是「按当前页的**图元**导出」，不是按当前页的
>    **床尺寸**导出**；本节第 5 条括号里「床尺寸因此只有一处存储」这句**不要**
>    读成「改床尺寸会跟着页走」。本轮实测：把 `doc.page.bed_w` 改成 100，
>    `doc.bed_w` 读出 100（门面自洽），而导出仍是 `width="210mm"`、
>    `_out_of_bed()` 仍按 210 判。**页级床当前是惰性的。**
> 5. **「送去执行」不是「直接送 job」——中间有一层用户可控的放置模式**。排版页
>    「送去执行」把 `to_job_spec()` 的结果交给作业页，作业页的**放置模式下拉**
>    （保持版面坐标 / 锚点归位）才是真正决定机器落点的那一环。本轮实测两处
>    边界：① 版面一个字没改、再点一次「送去作业」会**无条件**把放置模式复位成
>    「保持版面坐标」，整刀落点随之平移；② 排版页每次编辑触发的静默同步**保留**
>    用户选定的放置模式，但会**按整份内容重算锚点** ⇒ 没被编辑的图元被静默搬走
>    （实测删掉 A(0,0) 后 B(50,50) 落点变成 (0,0)）。两者都**已记在
>    `AGENTS.md`「已知缺口」**。

- **OpenCV 照片/素描追踪路径** → ✅ 已实现。新增模式「照片/素描（Canny+骨架）」
  接入加图对话框：`cv2.Canny` → `skimage.thin` → 复用既有游走/DP 简化。
  **与设想不同**：默认 `low=50 / high=120 / aperture=3` 是用测试内合成样张定的
  基线，**真机效果需现场调优**；且 Canny 对**实心笔画**只看得见两条轮廓边
  （1px 线实测 edge_px≈2×线长），宽笔画仍应用既有的 `trace_centerline`
  （阈值二值→thin）。Canny 对话框已补 `low ≤ high` 的即时校验。
- **对齐/分布/编组/撤销重做（排版页）** → ✅ 已实现（对齐/分布/撤销重做本就
  存在，本次补的是**编组**）。**与设想不同**：编组用「模型树 + **扁平场景** +
  组框 overlay」，**不引入 QGraphicsItem 父子**（`pos()` 是唯一返回父坐标系的
  接口，成组即错位）；组选中 = 叶子集，组级移动/缩放/旋转走既有多选机制，
  **组变换 = 叶子变换集合**，容器恒为恒等变换。组框判据已按任意深度 + 叶子传递
  闭包实现（嵌套组每层各画一个框）。
- **居中/镜像（对裁纸有用）** → ✅ 已实现（居中本就有；本次补**镜像**）。
  **与设想不同**：镜像支点是**本地 bbox 中心**（一般式），`pos`/`scale`/`angle`
  **不补偿**（原位镜像 ⇒ bbox 在 90° 倍数角下稳定，见上）；数学复用
  `coords.mirror_scalar`（翻转单源）。组的镜像是**逐成员**各绕自身中心翻，
  不是整组刚性反射。
- **图片就地二次追踪** → ✅ 已实现。`Item.image_spec` 记产线参数，双击图片图元
  即可调阈值/模式/最长边**就地重追**（其余参数预填不丢），Ctrl+Z 退回旧线条。
  **与设想不同**：重追**保持 pos/scale/angle 不变**（只换几何）。
- **多页/多版本管理** → ✅ 已实现。`Document` 持 `pages` + **当前页门面**
  （`items`/`bed_w`/… 全部委托当前页），故 `export_svg.py` 零改动即按当前页生效；
  床尺寸因此只有一处存储，`bed_w == page.bed_w` 恒等式不可违反。
  **与设想不同**：撤销是**单栈 + 命令页归属**（不是每页独立栈）—— Ctrl+Z 是
  「时间上的上一步」；跨页撤销自动作用到该命令的归属页再还原视图页。跨页批量
  逐页导出**仍不做**（与本文 §5 决策 5「单 SVG」冲突，见
  `docs/PRD_layout_model_tree.md` §10.1.6）。

---

## 5. 技术选型与关键决策

1. **文字轮廓**：`fontTools`（`TTFont(path, fontNumber)` → `getBestCmap` → `getGlyphSet` → `SVGPathPen`）→ 每字形闭合轮廓折线；**Y 翻转**（font 单位 Y 上 vs SVG Y 下）；font units→mm（`scale = target_em_mm/units_per_em`）；逐字 `width` 步进。MS 字体仅本地（license），分发代码默认 **Noto Sans SC**。
2. **单线字**：打包 LingDong JSON/HF 数据 → 直接得折线（0–1 归一化 → mm）。字形缺失降级：未含字回退轮廓字（标注）。
3. **图片**：Pillow 灰度→缩放(~3–5 px/mm)→阈值→PBM → `potrace -a 0 -t N` → 解析 SVG 输出为折线（或让 potrace 出 EPS/DXF 再转——v1 直接解析其 SVG）。产物 fill=none 描边。
4. **排版页**：QGraphicsView 场景 mm 原生；view 变换缩放/平移；grid `drawBackground`；标尺自绘；吸附 `itemChange`；**图元数据模型 = 折线集合 + 变换(位置/缩放/旋转)**，paint 与导出同源；**导出时把变换拍平进点坐标**、y 翻转到导出坐标。
5. **页面容器**：QTabWidget（现有 central 迁为 Tab0）。E-stop/连接/DRO 常驻（tab 外顶层）。
6. **坐标语义**：页面 (0,0) = 工件原点 = 机器「设工件原点」处（沿用 GUI 偏移、无 G92）。导出 SVG 为工件坐标，作业页按现偏移平移+越界。

---

## 6. 里程碑与验收

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M3a 文字转路径 | 中英文→轮廓/单线 SVG | 单测：字形路径生成、y 翻转、mm 缩放、缺失字降级；GUI：输入"写字机测试"→预览可画路径 |
| M3b 图片转线条 | potrace 导入 | 单测（mock potrace）；GUI：logo 图→线条预览 |
| M3c 排版页 | CAD 画布 + 文字/图片/导入 + 吸附 + 导出 | 单测：grid 定价、导出拍平/y-flip、吸附数学；offscreen：放两对象导出→parse_svg 回读坐标对 |
| M3 收尾 | 送去执行闭环 | 真机：排版 20mm 方块+文字→画/切 |

全量基线：现 71 测试保持绿 + 新增。

---

## 7. 风险

- R1（中）轮廓空心字小字号糊：UI 提示 ≥8mm；单线体作小字选项。
- R2（中）potrace 输出 region 语义 → 用 `-a 0` 多边形 + fill=none，避免双描/填涂。
- R3（中）y 方向约定（排版 y 上 vs 机器 y 下）：导出统一翻转 + 单测覆盖，防成品镜像。
- R4（低）MS 字体许可：本地可用，分发换 Noto CJK。
- R5（中）LingDong 字形质量需真机试：不理想则 v1 轮廓字为主。

---

## 8. 已确认决策

| # | 问题 | 决策 |
|---|---|---|
| 1 | 文字模式 | **两种都要**：轮廓空心字(默认, fontTools) + 单线手写体(LingDong) |
| 2 | 图片追踪 | **先 potrace**（logo/线条画），OpenCV 照片/素描后续 |
| 3 | 排版页入口 | **QTab 双页**：Tab0 控制/作业、Tab1 排版/制作 |
| 4 | 原点 | 排版页 (0,0) = 工件原点（沿用 GUI 偏移，无 G92） |
| 5 | 导出 | 模型拍平 + y 翻转 → 单 SVG mm 坐标，交现有流水线 |

---

## 9. 研究出处

- fontTools：github.com/fonttools/fonttools · readthedocs（ttFont/ttGlyphSet/SVGPathPen/pens）
- MS 字体 FAQ（许可）；Noto CJK：github.com/notofonts/noto-cjk
- LingDong chinese-hershey-font：github.com/LingDong-/chinese-hershey-font
- potrace：potrace.sourceforge.net · pypotrace(过时)；opencv-python (cp37-abi3)；vtracer(填充不适用)；vpype/vectrace
- Qt：doc.qt.io Graphics View / QGraphicsScene.drawBackground / itemChange / QSvgGenerator；diagramscene、40000chips 例；QCAD/LibreCAD 交互参考
- 本仓库：Explore agent 对 main_window/job.py/parse_svg/测试现状的 file:line 核对

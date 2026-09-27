# 预览与排版重构设计蓝图

## 经独立复核的根因
- **已复核** [high] `src/megapro/gui/main_window.py:1199-1204（载入时 translate_paths 烘偏移）；对照 src/megapro/gui/main_window.py:790-806（_on_set_origin/_on_clear_origin）`：工件原点偏移在 SVG 载入时一次性烘进 _job_paths/_job_lines，之后设/清工件原点不重新生成作业——先载入后对准设原点再执行时，实发 G-code 与预览都不含新偏移，图被画在机器坐标处而不是对准点。
  - 证据：_load_svg_path 在 1199-1204 调 translate_paths(paths, ox, oy) 后存 self._job_paths（1221）并 _gen_job_lines（1224）；_on_set_origin（790-801）只写 self._work_origin 并刷新 DRO（_on_position 777-786 用新偏移显示工件坐标），_on_clear_origin（803-806）同——两处均无对 _job_paths/_job_lines 的重生成调用；_on_run_job（1310-1322）原样 reqRunJob.emit(self._job_lines)。_on_feed_changed（1127-1134）从已平移的 _job_paths 重生成，保留的仍是旧偏移。典型触发流程即 UI 自己引导的流程：_on_layout_export 提示『检查预览后点开始执行』（1279），用户看预览→Jog 到纸面目标点→设工件原点→执行，此时偏移仍是载入时刻的 (0,0) 或旧值，落笔位置与对准点相差新旧原点之差。
- **已复核** [high] `src/megapro/gui/main_window.py:1195-1198 与 1276、1157-1162、1147-1148；src/megapro/gui/layout/export_svg.py:5,21-34`：同一 SVG 文件按入口不同走两套 y 约定：排版导出的文件（y-up 数值）经『载入 SVG…』会被 flip_y 再翻一次，整版相对排版所见上下镜像+位置反射；_on_opt_changed 重载永远按外部 SVG 处理（且源临时文件已被删）。
  - 证据：document_to_svg 把 model.transformed_paths 的 y-up 数值原样写进 <polyline>（export_svg.py:27-32，无任何翻转），但其 docstring 声称『y 向下（与机器一致）』（export_svg.py:5）——文件自带两种互相矛盾的约定。消费端：_on_layout_export 写临时文件后 _load_svg_path(tmp, already_paper=True)（main_window.py:1276）跳过 flip_y；而 layout_page._on_save_svg（layout_page.py:713-727）与 _on_export（740）产出的是同一 document_to_svg 输出，用户把『另存为 SVG』的文件再从『载入 SVG…』（1157-1162→1164，默认 already_paper=False）载入即被 flip_y（1195-1198，y'=210−y）二次翻转：设导出点为 (x,y_m)，正确入口纸面视图 y↑∝y_m，错误入口纸面视图 y↑∝210−y_m——关于床中线镜像。另外 _on_opt_changed（1138-1148）用 self._job_source 重载时固定走默认 already_paper=False，而排版作业的 _job_source 是已被 _on_layout_export finally 删除的临时文件（1271-1281），改『优化顺序/笔径』即弹『载入失败：无法解析 SVG』；若源文件存在则会静默镜像。
- **已复核** [high] `src/megapro/gui/layout/items.py:32,49-57（paint 用 (x,−y)+Qt setRotation）对照 src/megapro/gui/layout/model.py:59-67（transformed_paths 用 CCW R(θ)）`：排版画布显示的旋转方向与导出/G-code 相反：角度≠0°/180° 的图元在画布上看到的转向与实际走线转向互为镜像（所见非所画）。
  - 证据：model.transformed_paths：rx=x1·cos−y1·sin+px、ry=x1·sin+y1·cos+py（model.py:65-66）即 y-up 下 +θ 逆时针（test_gui_layout.py:28-37 golden 也钉死 +90° 使 (20,0)→(0,20)）；而 PolylineItem.paint 把模型点画成本地 (x,−y)（items.py:56）再由 QGraphicsItem.setRotation(θ) 作用。本次实跑探针验证 Qt 符号（python -c "from PySide6.QtGui import QTransform; from PySide6.QtCore import QPointF; t=QTransform(); t.rotate(30); print(t.map(QPointF(1,0)))" 与 rotate(90) 同跑）输出：rotate(90)·(1,0)=(0,1)、rotate(30)·(1,0)=(0.866,0.5)（scene 增量，y 向下）；合成 items.paint：模型点 (1,0) 在屏上落在 scene 增量 (0.866,0.5)=纸面增量 (0.866,−0.5)，而导出/机器把同一点放在纸面 (0.866,+0.5)。θ=+90° 时画布显示该点转到纸面 −Y（顺时针），实际走线转到 +Y（逆时针）。sync_to_model 把 Qt 的 rotation 直写回 model.angle_deg（items.py:77），两套数学无任何换向，偏差在 sinθ≠0 时恒存在。
- **已复核** [medium] `src/megapro/gui/job.py:154-161（flip_y y'=bed_h−y，bed_h 恒 210）；消费于 src/megapro/gui/main_window.py:1195-1198；预览床框 src/megapro/gui/main_window.py:1299-1304`：flip_y 按床高 210 整体反射、不锚定画板/内容：工件原点接住的是 SVG 用户空间的 (0,210) 点而非画板左下或内容左下，小画板内容被平移到床尾，且带原点时还会被 check_bounds 误判越界——对准困难/错位。
  - 证据：job.py:161 `y' = bed_h − y`，调用处硬编码 bed_h=210.0（main_window.py:1198）；于是 SVG 点 (0,0)（通常=画板左上）落纸 (0,210)、(0,210) 落纸 (0,0)=工件原点。以仓库自带 examples/square_20mm.svg（viewBox 0 0 20 20，内容 y∈[0,20]）为例：载入后落在纸 y∈[190,210]，距所设原点 190mm；若用户已设原点 oy>0，平移后 y∈[190+oy,210+oy] 越过 210 被 check_bounds（job.py:51-61，调用于 main_window.py:1211-1214，按机器 [0,210] 判）整单拒绝——本可放在原点附近的小图被误拦。预览侧 _draw_preview 只画 210×210 床框、无工件原点/画板标记（main_window.py:1298-1304），看不出 (0,0) 锚在哪，用户按浏览器里的 SVG 版面预期摆放必然对不上。
- **已复核** [medium] `src/megapro/gui/job.py:87-93（gcode_for_drawing）与 123-132（gcode_for_cutting）`：第一条路径起点恰为 (0.0,0.0) 时漏发定位 G0：代码假设起点已在 (0,0)（cur 初值），跳过空移后直接落笔/下压，再在笔/刀落下的状态下 G1 走到 p[0]——纸面多出预览里没有的拖线，裁纸时等于在错误点先下压误切。
  - 证据：gcode_for_drawing：`cur = (0.0, 0.0)`（job.py:87）后 `if p[0] != cur: lines.append(G0 X.. Y..)`（91-92），相等则不发定位；紧接着 `G1 Z pen_down_z`（93）在机器当前 XY 落笔，循环第一发 `G1 X.. Y..`（94-95）含 p[0]——若机器当前不在 (0,0)（常态：停在安全点 X0 Y50 Z30），落笔后从当前位置拖笔画到 p[0]。gcode_for_cutting 同构（cur=(0.0,0.0) 于 123、条件跳过于 127-128），且下压 `G1 Z cut_down_z`（130）发生在移动到 p[0] 之前=在错误 XY 下刀。触发条件为 p[0] 与 (0.0,0.0) 精确相等（元组精确比较），例如整床画板角点经 flip_y 后落 (0,0)，或排版内容贴页面原点且未设工件原点直接导出；_draw_preview（main_window.py:1292-1308）只画 polylines 本体，该拖线/误切点不会出现在预览。无测试覆盖此分支（test_gui_job.py 的 gcode 用例路径起点均非 (0,0)，如 57 行 [[(0,0),(10,0)]...] 的首段起点 (0,0) 恰被 cur 初值吞掉定位移动但用例只断言 Z 行、未断言 G0 定位的存在性）。
- **已复核** [high] `src/megapro/gui/layout/items.py:56（对照 src/megapro/gui/layout/model.py:64-67）`：非零 angle_deg 时预览旋转方向与导出/走线相反：paint 在 y 取负的本地系里被 Qt 正角旋转 = 纸面 R(−θ)，而 transformed_paths 用纸面 R(+θ)，同一角度预览与实际互为反向/镜像。
  - 证据：约定对照：model.py:64-67 `x1,y1=x*s,y*s; rx=x1*cos−y1*sin+px; ry=x1*sin+y1*cos+py`（纸面逆时针；tests/test_gui_layout.py:28-37 断言 (10,0)@scale2,θ90 → (0,20)），items.py:56 `pts=[QPointF(x,−y) for x,y in p]` 后由 setRotation(θ) 在 y-down 场景旋转（Qt 正角=顺时针）。执行验证（PySide6 offscreen 只读片段，非测试）：`python -c "...it=QGraphicsPathItem(); it.setPos(0.0,210.0); it.setRotation(90.0); print(it.mapToScene(QPointF(10.0,-0.0)), it.mapToScene(QPointF(0.0,-5.0)))"` 输出 paint 链 model 点 (10,0)→纸面 (0.0,−10.0)、(0,5)→(5.0,0.0)，而同参数 transformed_paths→(0.0,+10.0)、(−5.0,0.0)：两两镜像。pytest 未跑（本次指示禁跑）。
- **已复核** [high] `src/megapro/gui/layout/layout_page.py:576-586（_add_doc 不调 _to_paper；对照 :486/:552/:571 与 :682-687）`：y 约定三套实现并存且 doc_import 漏翻：Word/Excel 图元保持 SVG y-down 入库，而 paint/export 只认纸面 y-up → Word/Excel 字与表格行序在预览和成品上都上下镜像；另『另存为 SVG 再当普通 SVG 载入』会被作业侧 210−y 再翻一次 → 镜像+错位。
  - 证据：doc_import._text_paths（doc_import.py:30-39）取 text_outline_svg/text_singleline_svg 的 y-down 输出（text_to_svg.py:116-120、:253；tests/test_gui_text.py:56/82 锁定 y-down），段落 `p[i]=(x, yy+y)`、`y += line_h`（doc_import.py:80-89）与表格 `y = r*row_h`（:128-133,:150-152）按 y-down 排，_fit_to_bed（:42-62）只居中不翻；_add_doc（layout_page.py:576-586）不经 _to_paper（:682-687 `y'=max+min−y`）。items.py:56 恒 (x,−y) + export_svg.py:28-32 从不翻 → Word/Excel 内容在预览与 G-code 中均上下颠倒，与同页『加文字』（经 _to_paper 正立）反向。同根因：export_svg.py:5 称『y 向下…当普通 SVG 载入作业流水线即可』，但普通载入走 flip_y(210−y)（main_window.py:1194-1198、job.py:154-161），回读即绕床心镜像且位置变 210−y。tests/test_gui_doc_import.py:50-53 仅断言 0≤x,y≤210、无方向断言。
- **已复核** [high] `src/megapro/gui/layout/items.py:71-79（sync_to_model 唯一实现，写回点仅 layout_page.py:304/714/730）`：鼠标拖动只动 QGraphicsItem 不回写 model.pos：拖动后做微调/对齐/分布/层序/属性/复制中的任何一项，图元都会瞬移回拖动前位置附近，且属性栏 X/Y 一直显示拖动前旧值——数值定位越对越偏、图元无故跳位（拖动还不进撤销栈）。
  - 证据：`grep -rn "sync_to_model|_sync_models|_sync_gi" src` 显示写回仅 _apply_props/_on_save_svg/_on_export 三处；拖动由 ItemIsMovable（items.py:25-27）完成，无 ItemPositionHasChanged 回写。旧值消费点：_refresh_props 显示 it.pos（layout_page.py:270-271）、_nudge 以 it.pos 为 old（:346-347）、_align/_distribute 按 model page_bbox 算（:418,:444）后经 undo.py:131-137 `_apply→page._sync_gi(it)`→layout_page.py:103 `gi.setPos(item.pos[0], bed_h−item.pos[1])` 无条件按旧 model 拉回场景；_copy_selected/_duplicate 同样用旧 pos（:362,:387）。
- **已复核** [medium] `src/megapro/gui/layout/canvas.py:236-242`：竖直标尺的 0 点取错一个床高：刻度整体画在床框上方、床区左侧无任何可用读数，读数恒等于 paper_y−210，用标尺对 Y 必整体偏 210mm 级。
  - 证据：canvas.py:236 `origin = view.mapFromScene(QPointF(0,0)).y()` 取到的是场景 (0,0)=床左上（drawBackground :69-72 床框 QRectF(0, disp_y(BED_H)=0, 210,210)，原点十字在 (0, disp_y(0)=210) 即 paper(0,210)），paper y=mmv 的正确视图 y 是 mapFromScene(0, 210−mmv).y() = origin+(210−mmv)*ppm；代码 :239 `y = origin − mmv*ppm` 比正确值恒小 210*ppm px，且循环 `range(0, 211, step)` 的刻度全部落在床顶之上。水平标尺（:228-234）因 scene x=paper x 才正确。
- **已复核** [medium] `src/megapro/gui/layout/layout_page.py:270-271,286-299（数值定位）与 src/megapro/gui/layout/items.py:59-69（吸附）`：数值定位/吸附锚点与图形几何脱节：X/Y 显示并写入的是本地原点 pos，而绘制/文字/图片/导入图元一律 pos=(0,0) 且 paths 是绝对页面坐标 → X/Y 恒 0、输 X 得不到『左边对齐 X』；『高』输入框不生效，多选改 X/Y 使所有图元塌缩到同一点；吸附只对 pos 取整（图形本体不上格、绘制工具完全不吸、数值输入被静默量化）。
  - 证据：图元创建均 pos=(0,0)：绘制 layout_page.py:667-671、加文字 :490、加图 :553、导入SVG :572、doc_import.py:88/134/153；_to_paper（layout_page.py:682-687）只镜像 y 不归零原点，SVG/位图内容偏移原样留进 paths。_refresh_props 显示 it.pos（:270-271），_apply_props 对 sel 内每个图元写同一 `new["pos"]`（:286-292，循环内 `if sel:` 恒真）→ 塌缩；sp_h 只显示不读（:294-298 仅用 sp_w 改 scale）。吸附 items.py:60-68 仅 `round(pos/pitch)*pitch`（pitch 写死 1.0，layout_page.py:76），且 _sync_gi 的 setPos 同样触发取整（undo.py:136）→ model 与场景可各留一个值。tests/test_gui_layout_window.py 无任何吸附/数值定位断言（本次未跑测试）。

## 成熟软件模式（出处见条目）
- **AxiDraw (evil-mad/axidraw)**：预览=同一动作流的“跳过物理执行”：preview 模式不是重画一张图，而是跑与实绘完全相同的 plot/dripfeed 动作序列，在动作分发层把物理动作跳过（dripfeed.py: “Feed individual motion actions to the AxiDraw during a plot or preview... Skipping physical moves while in preview mode”）；配置项 preview = “simulate plotting only”，rendering 0-3 控制预览渲染（0 不渲染 / 1 只渲染落笔段，另有抬笔段配色 preview_color_up/preview_color_down）；无机器时也可离线渲染预览并计时（axicli -Tvg3）。
  - 解决什么：预览与实际输出共享同一条动作序列和同一 pen-up/down 状态机，从构造上消除了“预览画的是设计、机器走的是另一套”的分叉，是唯一能声称预览≡走线的机制；抬笔/落笔分色还直接暴露 Z 抬落与空移。
  - 出处：github.com/evil-mad/axidraw：inkscape driver/dripfeed.py、inkscape driver/axidraw_conf.py（preview/rendering 注释）、inkscape driver/pen_handling.py（preview_pen_state）、cli/Installation.txt（离线 -Tvg3 渲染）
- **LightBurn（Preview 窗口，反例性参考）**：预览画“最终结果静止图”（Line=黑线、Fill=实心/扫描线）+ 红色空移线（“these lines indicate the path your laser will travel while not firing”，可 Show traversal moves 开关）+ 按真实节拍播放的顺序模拟（Play 动画层顺序=加工顺序，Playback Speed 可调）+ 右下角工时估计；文档自称“strongly recommend previewing every project, before sending it”，定位是检查项而非等价证明。
  - 解决什么：用“结果图+空移+顺序动画”暴露层顺序错误、空移越界、叠形状双丢（duplicate shapes 两件都不输出）等预览≠预期的问题；但也正因不是对将发送 G-code 的回放，它不能从构造上保证预览≡实际——这划出了机制上限。
  - 出处：https://docs.lightburnsoftware.com/2.1/GetStarted/PreviewBeginner/
- **K40 Whisperer**：预览画的是转换后的走线数据而非输入原图，且按数据类型分层开关：View 菜单 “Show Raster Image / Show Vector Engrave / Show Vector Cut / Show G-Code Paths”；栅格显示的就是半调/抖动后的点阵（即实际要打的点），矢量显示为线；唯一例外是 EGV 原始数据——“The raw data is not interpreted by K40 Whisperer so there is no preview of the data to be sent”，明示不保证。
  - 解决什么：所见即转换器输出：用户看到的点/线就是将下发的走线，转换错误（半调、镜像、缩放 1.111 这类）在预览即暴露，而不是打完才发现；“无预览就直说”也避免了假保真。
  - 出处：https://www.scorchworks.com/K40whisperer/k40w_manual.html（View 菜单与显示条目原文）
- **LaserGRBL**：在作业预览图上叠加实机坐标十字：Engraving preview 面板“this area show final work preview. During engraving a small blue cross will show current laser position at runtime”——十字位置来自控制器实时回报，不是本地推算。
  - 解决什么：把“计划走线”和“实际机头位置”画在同一张图上：本机已知的 ok-不走/丢步/软限位静默类问题会立刻表现为十字与走线脱节，是成本最低的预览↔实机偏差探测器。
  - 出处：http://lasergrbl.com/usage/user-interface/（Engraving preview 一节）
- **Universal Gcode Sender (UGS)**：可视化器直接渲染解析后的 G-code：按运动类型分色（“Different colors for paths of different types (G0, G1, G2, G3)”）、“Real time tool position feedback”、右键“jog to location / set the current work position”、编辑器选中行在视图高亮；同时源码明确 G54-G57 等 “These are not used in the visualizer.”（GcodeParserUtils 注释），即可视化器不套 WCS 偏移。
  - 解决什么：预览与发送共用同一解析结果，路径保真；但其“WCS 不进可视化器”的取舍恰好是预览≠实际的一类根因——做 PySide6 预览时必须把 G92/WCS/镜像/缩放算进预览变换，这条反例非常有教育意义。
  - 出处：https://github.com/winder/Universal-G-Code-Sender/wiki/Features；ugs-core/src/com/willwinder/universalgcodesender/gcode/util/GcodeParserUtils.java；ugs-core/src/com/willwinder/universalgcodesender/model/WorkCoordinateSystem.java（G53..G59 枚举）
- **LightBurn（坐标/原点）**：机器原点固定 + Job Origin 可移动 + Start From 三模式（Absolute Coordinates / Current Position / User Origin）+ 3×3 九宫格锚点：锚点取“the green Job Origin indicator is always located on a corner, side, or at the center of an imaginary box”（设计 bbox 的角/边中点/中心），输出整体平移使该锚点落在设定原点上；User Origin = 移动机头后 Set Origin 记住参考点，之后 jog 不影响。
  - 解决什么：把“设计对准物理落点”参数化为选锚点+定原点两个动作：中心锚点直接实现“笔头对物件中心→输出居中”；User Origin 模式即我们可用 G92 实现的“纸角/当前点=工件原点”。
  - 出处：https://docs.lightburnsoftware.com/2.1/Reference/CoordinatesOrigin/
- **LightBurn（Framing 走边框）**：Frame 双形态描边对准：Bounding Box Frame 走“the smallest possible rectangle that will fully contain all graphics”；Rubber Band Frame 走“the smallest possible path that fully contains all graphics... as if a rubber band were stretched around them”（更贴合细长/斜向图形）；速度取 Move 面板 Speed 值，可 Frame Continuously 循环描边直到叫停；用红点指针或低功率 Fire 可见（红点需单独做 Red Dot Framing Offset 校准）。
  - 解决什么：对准只需几行 G0 空移走 bbox（或凸包），零硬件、零标定，是所有对准手段中成本最低的一种；循环描边让人有时间去挪纸/夹具。
  - 出处：https://docs.lightburnsoftware.com/2.1/GetStarted/FramingBeginner/；红点校准见 https://docs.lightburnsoftware.com/2.1/Guides/LaserOffsetSetup/
- **LightBurn（Print & Cut 双点套准）**：手动两点套准：项目带十字标记（单个对象/成组的单矢量）→ 向导中选中第一个十字并 jog 机头到物理标记中心（Fire/脉冲或红点辅助瞄准）→ Set；第二个十字向导自动 jog 到预测点、微调后 Set → 对整个作业施加“位置+旋转”（Scaled 选项再按两标记实测间距修正缩放）变换；强制 Absolute Coords，标记间距越大精度越高。
  - 解决什么：无需相机即可让设计对上已存在物理特征（纸上已印内容、分段大图二次拼接、断点续跑），直接解决“设计对不准物理工件”且能纠正摆放歪斜（旋转）与缩放误差。
  - 出处：https://docs.lightburnsoftware.com/2.1/Reference/PrintAndCut/
- **K40 Whisperer（对准）**：低成本点检式对准：四角+中心按钮 “are used to move the laser head to the corners of the loaded design... useful to verify that the design will fit in the space available on the material”；Move To 按钮“will home the laser then move the laser head to the X and Y position entered”（绝对坐标定位）；Unlock Rail 断电 X/Y 步进电机允许手动推头；另有 home 归位角选项（左上/右上，设错则整体镜像+jog 反向）。
  - 解决什么：不走完整边框也能确认放置（逐角点检），配合绝对坐标 Move To 可把机头当“定位针”用——比 Frame 更快、比相机更便宜，且暴露了归位角/Y 方向约定必须显式配置。
  - 出处：https://www.scorchworks.com/K40whisperer/k40w_manual.html（Design Alignment、Move To、Home、Unlock Rail 各条目）
- **CNCjs**：用宏占位符生成“描边界”宏：宏文本引用 [xmin][xmax][ymin][ymax]（及 zmin/zmax），装入文件后“run the macro for perimeter tracing with respect to current G-code boundary”——即自动按当前文件解析出的边界生成一段 traverse 环绕路径。
  - 解决什么：把走边框做成“任意文件一键描 bbox”的通用宏，不依赖 UI 特性、只需解析边界+几条 G0：对 PySide6+Marlin 可直接照抄为“描作业 bbox”按钮。
  - 出处：https://cnc.js.org/docs/user-guide/（macro 示例：Traverse around the boundary）
- **Inkcut（边界与越界）**：预览按语义分三层边界 + 发送前裁剪：“The red dashed border is the device's x-y plane. The solid black border is the material. The dashed black border is the available area for use. The blue lines is the movement path.”（preview/view.enaml tooltip）；ClipFilter 在作业管线上“clips the geometry to the bounding-box implied by the Job's material settings... prevent us from accidentally sending the plotter head off the gantry”（job/filters.py）；另有 material.padding_path 安全边距路径参与渲染（job/plugin.py）。
  - 解决什么：同屏区分“机器行程/材料/可用区”让越界一眼可见；ClipFilter 属构造性防护（越界内容发不出去），padding_path 则给出安全边距显式模型——正对应“预览=实际位置”与“材料设定”两个痛点。
  - 出处：github.com/inkcut/inkcut：inkcut/preview/view.enaml、inkcut/job/filters.py、inkcut/job/plugin.py（GitHub 代码检索命中原文）
- **Inkcut（虚拟原点/实时/透明化）**：连续作业用 virtual origin：“plot one job after another without having to manually update the device origin. It uses the 'virtual' origin that may be set or cleared from the Control panel. The origin is automatically updated when the 'feed to end' option is selected”；Live 页签实时画“device movement path”+进度条、可暂停/中止；发送前对话框展示作业摘要以及“the data to be sent”（即将下发的原始数据）。
  - 解决什么：虚拟原点=把“工件原点偏移”做成可推进状态，解决多件排版挪版问题；实时走线+发送前展示 payload 让用户在最后一刻核对的正是将发送的字节流，是通往“预览≡实际”的透明化手段。
  - 出处：github.com/inkcut/inkcut docs/tutorial.md（Stacking jobs / Live plot / Step 5 原文）

## Qt 画布架构建议
【总原则】场景坐标 ≡ 机器纸面毫米：X 右、Y 上（纸深方向）、原点=床左下=G-code G0 X/Y 绝对坐标（M114 logical X/Y 与之恒等）。y 翻转只允许存在于 view transform 一处（coords.view_transform）；此外任何文件出现 `BED_H - y`/`(210 - y)`/`(x, -y)` 一律按 bug 处理。现状 grep 实测有 12 处手写翻转（canvas.py:21-22,59-72、items.py:30,56,65-68,75、layout_page.py:103,638,687、undo.py:96,104、main_window.py:1304、job.py:161、text_to_svg.py:253）。

【为什么选 view 翻转而不是场景翻转——带实测证据】当前「场景 y-down + 手写翻转」在旋转下与导出互为镜像：items.py:56 本地画 (x,−y) 再 setRotation(items.py:32)，model.py:63-67 导出是标准 CCW 矩阵。用本次实测的 setRotation(+90) 矩阵 (x,y)→(−y,x)（实测：item 旋转 +90 后 local(1,0)→scene(0,1)）组合两条链，python 实测输出：painted scene (14.0, 193.0) vs export→display (6.0, 187.0) MISMATCH——凡 angle≠0/180 预览与导出错位，这就是「预览与实际位置不同」的根因之一。改成场景=y-up-mm 后：model/导出/G-code/M114 预览四方零换算，镜像只在 view 矩阵出现一次，setRotation(+θ) 恢复屏幕逆时针（实测链：view 变换 scale(s,-s) 使 mapFromScene(0,0)=(198,148)、(0,100)=(198,−52)，即场景 y-up）。

【唯一换算权威：src/megapro/gui/canvas/coords.py（纯函数、无 Qt 对象状态、可单测）】
```
BED_W = BED_H = 210.0                      # 由 layout.model re-export
def view_transform(ppm, anchor_mm: QPointF, anchor_view: QPointF) -> QTransform:
    # 全仓库唯一允许负比例尺的函数；显式矩阵避免 QTransform 调用顺序歧义
    dx = anchor_view.x() - ppm*anchor_mm.x(); dy = anchor_view.y() + ppm*anchor_mm.y()
    return QTransform(ppm, 0, 0, -ppm, dx, dy)
def mm_from_view(view, vp_pt: QPointF) -> QPointF:      # view→mm，浮点
    inv, ok = view.viewportTransform().inverted(); return inv.map(vp_pt)
def view_from_mm(view, mm: QPointF) -> QPointF:         # mm→view（标尺/手柄），浮点
    return view.viewportTransform().map(mm)
def machine_from_paper(p): return p                     # M114 logical == 纸面 mm（单测锁死恒等）
def paper_from_machine(p): return p
def paper_to_svg_ydown(paths, bed_h=BED_H): ...         # y'=bed_h-y：替代 job.flip_y(job.py:154-161)
def paper_from_svg_ydown(paths, bed_h=BED_H): ...       # 替代 main_window.py:1304、layout_page.py:687、text_to_svg.py:253 各自局部翻转
def grid_steps(ppm, target_px=40) -> (major_mm, minor_mm)  # 1/2/5×10^k（Heckbert）
def snap(v, pitch) -> float
```
禁止调 mapToScene/mapFromScene（见 pitfalls #2），全走 viewportTransform()。

【文件组织（落地到 Qt 类）】
```
src/megapro/gui/canvas/            # 新包：排版页+机器预览页共享的画布核心
  coords.py       # 上表：唯一坐标权威
  paper_scene.py  # PaperScene(QGraphicsScene)：drawBackground 网格+床框(0,0,210,210)+原点十字；sceneRect(-20,-20,250,250)
  paper_view.py   # PaperView(QGraphicsView)：set_zoom()/fit()/wheelEvent/中键平移；所有坐标过 coords
  rulers.py       # RulerWidget(orientation='h'|'v')：tick 用 view_from_mm 浮点定位
  items.py        # PathItem(QGraphicsPathItem)：paths 以 y-up 本地坐标直画（删 items.py:56 的 -y）、boundingRect 直接用 model.bbox()
  handles.py      # SelectionHandles：旋转/缩放手柄（自管，见 Q4）
  snap.py         # SnapEngine：网格/端点/中点/边吸附（纯函数）
  gcode_items.py  # GcodePathItem：落笔/空行程/进度三色分段着色（机器预览页）
  undo_cmds.py    # 现 layout/undo.py 移入；setPos 去掉 `bed_h-y`（undo.py:96,104）
layout/model.py   # 保持纯逻辑唯一模型（Item.paths/pos = 纸面 mm y-up）
layout/export_svg.py  # 导出经 coords.paper_to_svg_ydown 一处翻转
layout/layout_page.py # 只留工具/属性面板/命令编排；_sync_gi(layout_page.py:99-110) 去翻转
main_window.py    # 机器预览页复用 PaperView+GcodePathItem；删除 _draw_preview 的场景级 scale=1.6 与 (210-y)（main_window.py:1295-1306）
```

【Q2 HiDPI 鼠标/拖动映射】
- 实测（PySide6 6.11.2 / Qt 6.11.2，QT_QPA_PLATFORM=offscreen）：QGraphicsView.mapToScene 只有 int 重载（QPoint / x,y / QRect / QPolygonF / QPainterPath），传 QPointF 直接 TypeError；mapFromScene(QPointF) 能收但返回 QPoint。→ 浮点唯一通道：view→mm 用 `mm_from_view(view, event.position())`（QMouseEvent.position() 本身浮点），mm→view 用 `view.viewportTransform().map(mm)`。
- 量化误差实测：0.3 px/mm 时 float 映射 (-289.0, 234.333) vs toPoint 版 (-290.0, 233.0)——差 (1.0, 1.333)mm；mapFromScene float (205.0,133.5) vs 返回值 QPoint(205,134)。canvas.py:133/144/166/183/187/196 六处 `.position().toPoint()` 全部改掉。
- DPR：Qt6 视口/控件坐标都是逻辑像素，绝不手动乘 devicePixelRatio。实测 mapToGlobal→mapFromGlobal→mapToScene roundtrip 精确回 (37.5, 42.5)（dpr=1.5）。只有 grab/QPixmap 是物理像素：实测 400×300 逻辑 → 600×450、devicePixelRatio=1.5。
- 交互优先放 item/scene 层：QGraphicsSceneMouseEvent.scenePos() 天然浮点、无量化；view 层工具（画线/框选）用 mm_from_view。光标跟随（十字线/悬停吸附提示）：mm_from_view(view, view.mapFromGlobal(QCursor.pos()))。拾取容差需「一个逻辑像素覆盖的 mm 面积」时，QGraphicsView 文档建议 mapToScene(QRect(point, QSize(2,2))) 而不是单点。

【Q3 标尺 + 网格 + 缩放/平移惯例】
- 定价 1/2/5×10^k 出处：Paul Heckbert《Nice Numbers for Graph Labels》, Graphics Gems 1990（erich666/GraphicsGems 的 gems/Label.c）。实现 `grid_steps(ppm, target_px=40)`：major = 最小满足 major*ppm ≥ target_px 的 1/2/5×10^k；minor = major/5。实测现有 grid_pitch_mm 的 24–80px 宽带：1 px/mm → 50mm 网格（整床仅 5 条线，太稀）；0.7→50mm、1.5→20mm、12→2mm。收窄到单一 target_px 更跟手。
- drawBackground(painter, rect)：rect 已是场景（mm）坐标（QGraphicsScene 文档：paint in scene coordinates），只遍历 rect 交集范围——y-up 场景下删掉 canvas.py:59-64 的 py_lo/py_hi 换算。pen.setWidthF(0)（cosmetic）。minor 浅/major 深两级（现有单级）；major*ppm < 6px 时只画 major 防糊。可见 mm 区间由 `mm_from_view(view, viewport().rect() 四角)` 求，替代 canvas.py:230 的 range(0,211)（床外也要延续网格/标尺）。
- 标尺 tick 定位：`p = view.viewportTransform().map(QPointF(mm, 0))` 取 p.x()，drawLine 用浮点重载（canvas.py:233/241 的 int() 截断在小数缩放时抖动）；竖标尺同理由 map((0, mm)).y()，翻转自动成立（删 canvas.py:239 的 `origin - mmv*ppm`）。labels 用 major 步距、minor 只画刻度线；删 max(1,int(pitch))（canvas.py:230）——pitch<1mm 时它退化 1mm 刷屏。
- 缩放到光标：现 AnchorUnderMouse+scale(factor)（canvas.py:89,117-121）符合惯例（QGraphicsView 文档：AnchorUnderMouse 保持鼠标下场景点不动），factor 1.15、ppm 钳位 [fit 值, ~100]。更可测的替代：显式锚点 `set_zoom(ppm_new, anchor_view)` = setTransform(coords.view_transform(ppm_new, mm_from_view(anchor), anchor))——offscreen 测试没有真光标（见 Q6+）。fit：fitInView(rect, KeepAspectRatio)（canvas.py:110-114 现状即对）。
- 平移：中键拖动改滚动条（canvas.py:155-164）即惯例；Space+左键 / ScrollHandDrag 备选（经验判断）。Ctrl+滚轮平移、滚轮缩放（经验判断）。

【Q4 吸附 + 手柄 + QUndoStack】
- 拖拽位置吸附 → itemChange(ItemPositionChange) 返回调整值：Qt 文档明言此通知里禁止 setPos、只能 return 改写后的 value；需开 ItemSendsGeometryChanges（items.py:27 已开）。吸附算法放 SnapEngine 纯函数：网格 pitch 取 grid_steps 的 minor；对象吸附（端点/中点/边）用 scene.items(候选矩形) + PathItem 关键点缓存（model.Item.paths 是折线，端点/中点零成本）。当前 items.py:59-69 的 `BED_H - v.y()` 三进三出删除，直接 round 到 mm。
- 手柄（旋转/缩放）→ 自管 SelectionHandles（QGraphicsItem 无内建 resize/rotate handle——经验判断）：scene.selectionChanged（QGraphicsScene 文档：成组选择只发一次信号）→ sync(selectedItems)，摆位用 sceneBoundingRect()（文档：boundingRect 不受自身变换影响，须转场景系）。拖动中只临时 setRotation/setScale，mouseRelease 一次性 push ChangeItemPropsCommand(old,new)。手柄恒定屏幕大小：每帧 setScale(1/ppm)；慎用 ItemIgnoresTransformations（文档警告：该 flag 下坐标/碰撞必须走 deviceTransform()）——经验判断优先前法。
- 不要在 itemChange 里捕获 undo 或摆手柄：它无法区分用户拖拽 vs 程序 setPos（items.py:28 的 `_syncing` 补丁正为此），且拖拽中触发成百上千次。
- QUndoStack（保留现 undo_cmds.py 形态）：① 命令只写 model.Item，场景同步唯一入口 page._sync_gi；Add/Remove 保持重建式 _make_gi（undo.py:34-43，现状正确）。② QUndoStack::push 会立即执行 redo（QUndoCommand 文档）→ redo 必须幂等（MoveItemsCommand 直接 setPos(new) ✓）。③ mergeWith 契约（文档：同 id 且 id≠-1 才尝试合并，redo 需等效两命令之和）：现 MoveItemsCommand.id() 恒 1001（undo.py:107-119）会把两次独立拖拽并成一条撤销——mergeWith 比对手势 token（同一次 press→release 才合），否则 return False。④ QGraphicsItem 非 QObject（undo.py:2 注释已点到）：命令持 model.Item、运行时 _gi_for 查 gi，不长期捕获 gi；_remove_item_obj（layout_page.py:91-97）removeItem 时确保 Python 引用同步清干净。

【Q5 性能（上千段折线）】
- 批量绘制：一图元一条 QPainterPath（moveTo/lineTo 串联），pen.setWidthF(0)；绝不每段一个 QGraphicsLineItem（paint 调用与索引开销爆炸——经验判断）。
- culling 是 item 粒度（QGraphicsItem 文档：boundingRect 驱动场景索引与绘制裁剪）→ 巨型单 item 每帧全量 paint。方案 A（推荐）TiledPathItem：内部按 ~25mm 分块存子 QPainterPath+块 bbox，paint 开 ItemUsesExtendedStyleOption 用 option->exposedRect 跳过不可见块（文档：exposedRect 仅该 flag 置位才有效）；方案 B：每折线一个 QGraphicsPathItem 靠场景索引裁剪——静态场景 BspTreeIndex（文档：增删移动对数级），拖拽/动画密集 setItemIndexMethod(NoIndex)（文档：动态场景推荐，查找线性、变更常数）；bspTreeDepth 按「每格 0–10 项」调（文档给公式 sceneRect/2^(depth-1)）。
- LOD：lod = QStyleOptionGraphicsItem::levelOfDetailFromTransform(painter.worldTransform())（文档定义），lod<0.3 画简化折线/点云；scene.setMinimumRenderSize(≤1.0mm)（Qt 6.7+ 文档）跳绘极小图元——文档警告跳绘的 item 仍可被 items()/itemAt 命中、建议 ≤1。
- 视口更新：平移/缩放留 MinimalViewportUpdate；整选拖拽上千项用 BoundingRectViewportUpdate（文档枚举语义）。QGraphicsView 只支持背景缓存 CacheBackground（文档）→ 网格走 drawBackground 受益。（帧率数字本次未实测。）
- G-code 预览三色（落笔/空行程/进度）= 3 条 QPainterPath 或 TiledPathItem×3，比逐段 item 便宜一个量级（经验判断）。

【Q6 QSvgRenderer vs 自绘（G-code 分段着色）】选自绘。QSvgRenderer 文档实证：只有 render(QPainter*, bounds) 一类绘制接口，成员表无任何 QPainterPath 提取；bounds 非空时「output will be scaled to fill it, ignoring any aspect ratio」——直渲是一整幅不可拆的画，做不到按段着色/高亮当前刀路/段拾取，还引入 SVG viewBox 第二套坐标权威。我们的中间态本就是折线（svg_to_gcode.parse_svg 已把 path/cubic/arc 扁平为折线，_TOL=0.05mm：toolchain/svg_to_gcode.py:11,296-300）→ 自绘 QPainterPath 天然贴合；SVG 仅作交换格式。

【Q6+ offscreen 测试要点】
- QT_QPA_PLATFORM=offscreen + 每测试独立 QApplication（AGENTS.md 现行 test_gui_window.py 模式）。
- QWidget.grab() 是物理像素：实测 400×300 逻辑 → 600×450、dpr=1.5（本机缩放渗入 offscreen）→ 像素断言用 pix.width()/pix.devicePixelRatioF() 归一，勿假设 dpr=1。
- QTest.mouseClick(view.viewport(), pos) 的 pos 是视口逻辑像素：目标 mm 用 view_from_mm 反算后取整，断言容差 ≥1 逻辑 px（0.3 px/mm 时 1px≈3.3mm；吸附测试用大 pitch 避歧义）。
- AnchorUnderMouse 依赖真实光标，offscreen 无光标 → 缩放测试走显式锚点 set_zoom(ppm, anchor_view)。
- 文本度量随字体/平台变，不断言文字像素宽度（经验判断）。
- coords.py 纯函数单测（不进事件循环）：svg_ydown↔paper 双向、machine≡paper 恒等、view_transform 锚点不变（view_from_mm∘mm_from_view = identity）。

【本次未做/未验证】未跑仓库 pytest 全套（本任务为只读调研，未被要求回归）；无性能基准数字（性能建议部分为文档依据+经验判断）；Qt diagramscene 官方文档页经 fetch 实证不含 undo 实现（该页删除/移动均无 QUndoStack），MoveCommand 惯用法标「经验判断」。

### 坑清单
- 【实测·根因】手写 y 翻转 ×12 处导致旋转镜像不一致：items.py:56 本地 (x,−y)+setRotation(items.py:32) 与 model.py:63-67 导出 CCW 数学互为镜像。python 实测（setRotation+90 矩阵 (x,y)→(−y,x) 已由 Qt 实测确认）：painted scene (14.0,193.0) vs export→display (6.0,187.0) MISMATCH——旋转预览≠导出/实刻。
- 【实测】mapToScene 无 QPointF 重载（传入 TypeError，仅 QPoint/int/QRect/QPolygonF/QPainterPath），mapFromScene(QPointF) 返回 QPoint——两者都量化到整数逻辑像素。0.3 px/mm 下 float (-289.0,234.333) vs int (-290.0,233.0)，误差 (1.0,1.333)mm。唯一浮点通道 view.viewportTransform()[.inverted().map()]。
- 【实测】canvas.py:133,144,166,183,187,196 的 event.position().toPoint() 把亚像素丢掉；高倍缩放（ppm>1）下落点抖动 1px≈1/ppm mm。
- 【实测】mapFromScene 也有量化：float 应为 (205.0,133.5) 却返回 QPoint(205,134)（y 差 0.5px）——标尺 tick 若用 mapFromScene 定位会在小数缩放下周期性抖动，须用 viewportTransform().map() 浮点。
- 【Qt 文档原文】pos() 是唯一返回父坐标系的接口（"the only function that does not operate in local coordinates"）；items.py:75 sync_to_model 读 self.pos()——一旦引入 parent item（如成组）即错位。统一读 scenePos() 或经 coords。
- 【Qt 文档原文】boundingRect() 不受 item 自身变换影响、且必须覆盖 paint 全部内容（含 pen 宽）；items.py:34-40 手写 `-max(y1,y0)` 是翻转债的衍生品，换 y-up 后直接用 model.bbox()。改 bbox 前必须 prepareGeometryChange()（undo.py:163 有、items 路径没有）。childrenBoundingRect 不含自身（文档）。
- 【Qt 文档原文】itemChange(ItemPositionChange) 回调内禁止 setPos（"you cannot call setPos() in itemChange()... return the adjusted position"）；ItemPositionHasChanged 与 ItemScenePositionHasChanged 是两个不同通知，后者需另开 ItemSendsScenePositionChanges flag（items.py 未开）。
- 【Qt 文档原文】item 变换应用顺序固定：transform() → transformations() → 绕 transformOriginPoint 旋转 → 绕 origin 缩放，与调用先后无关。加旋转手柄前必须先定 transformOriginPoint（bbox 中心 vs pos），否则旋转跳变（经验判断）。QTransform 链式 scale/translate 的组合顺序易错——coords.view_transform 用显式六元矩阵 QTransform(ppm,0,0,-ppm,dx,dy) 规避（经验判断）。
- 【实测链】view 变换 scale(s,-s) 下 setRotation(+θ) 恢复屏幕逆时针（实测 local(1,0)→scene(0,1) + mapFromScene(0,0)=(198,148)>(0,100)=(198,-52) 确认 y-up）；与 model.py 导出的 CCW 语义统一后镜像消失。注意与现状（y-down 场景）符号相反，迁移时 angle 观感会翻转。
- 【Qt 文档原文】ItemIgnoresTransformations 的 item 必须用 deviceTransform() 做坐标映射与碰撞检测（"you must call deviceTransform() to map coordinates and detect collisions in the view"）——手柄恒定大小优先用 setScale(1/ppm) 自管（经验判断）。
- 【Qt 文档原文】QUndoStack::push 立即执行 redo（push 即成为栈顶命令并生效）→ 已生效的交互拖拽必须让 redo 幂等；mergeWith 仅在同 id 且 id≠-1 时被尝试，且合并后 redo/undo 必须等效两命令之和。
- 【实读代码】MoveItemsCommand.id() 恒返 1001（undo.py:107-119）→ 两次不相关的拖拽会被并成一条撤销记录。mergeWith 需校验同一手势 token（经验判断修法）。
- 【实读代码】QGraphicsItem 非 QObject（undo.py:2 注释）；命令若闭包捕获 gi，RemoveItemsCommand 走 _remove_item_obj（layout_page.py:91-97，removeItem 未 delete）后引用管理易错乱——命令只持 model.Item，gi 生命周期归 page（undo.py:34-43 重建式已符合）。
- 【Qt 文档原文】QSvgRenderer 无 QPainterPath 提取 API（成员表核对），render 有非空 bounds 时无视纵横比拉伸（"ignoring any aspect ratio implied by the SVG"）——G-code 分段着色/拾取必须自绘 QPainterPath，SVG 只作交换格式。
- 【Qt 文档原文】culling 是 item 粒度（boundingRect 驱动场景索引与绘制裁剪）：上千段折线塞进单一 QPainterPath item（boundingRect 盖满全床）每帧全量重画，批量绘制的收益被全量 paint 抵消——需 TiledPathItem 按 exposedRect 跳块（ItemUsesExtendedStyleOption 才有 exposedRect）或拆多 item 吃 BSP 裁剪。
- 【Qt 文档原文】BspTreeIndex 适合静态场景（变更对数级）、NoIndex 适合频繁增删移动的动态场景（查找线性）；bspTreeDepth 内存随深度指数增长、频繁变更还会触发内部 retune 减速，按「每格 0–10 item」固定深度。
- 【Qt 文档原文】minimumRenderSize 跳过渲染的 item 仍被 items()/itemAt 返回、仍参与碰撞（"are still returned by methods such as items() and itemAt(), and participate in collision detection"），且建议 ≤1 以免出现可点不可见的大图元。
- 【Qt 文档原文】DeviceCoordinateCache 只适合「只平移不旋转/缩放」的 item，缩放即缓存重建——缩放动画/缩放中的图元用 NoCache 或 ItemCoordinateCache（后者画质随分辨率下降）。
- 【实测】grid_pitch_mm 的 24–80px 宽带定价粗：1 px/mm → 50mm 网格（实测表：0.02→2000, 0.25→100, 1→50, 3→10, 5→5, 20→2, 100→0.5mm），×2.5 跳变（20→50）导致疏密观感不稳；建议单一 target_px≈40 的 Heckbert 阶梯。
- 【实读代码】Ruler 用 max(1,int(pitch))（canvas.py:230,238）：pitch<1mm 时退化 1mm 刻度刷屏；tick 用 int(x)（canvas.py:233,241）小数缩放抖动；range(0,211)（canvas.py:230）床外无刻度。
- 【实读代码】main_window._draw_preview 把 scale=1.6 px/mm 烘进场景坐标并自带 (210-y) 翻转（main_window.py:1295-1306）——与排版页不同源不同单位，是「预览与实际位置不同」的第二处独立根因；job.py:154-161 flip_y、layout_page.py:682-687 ysum−y、text_to_svg.py:253 (1.0−y) 是另外三个各自为政的翻转边界。
- 【实测】offscreen 下 grab 也是物理像素：400×300 逻辑 → 600×450、dpr=1.5（本机缩放设置渗入 QT_QPA_PLATFORM=offscreen）——测试断言勿假设 dpr=1，用 pix.devicePixelRatioF() 归一；反过来 Qt6 事件/mapFromGlobal 全程逻辑像素（roundtrip 实测精确），不要手动乘 dpr。
- 【经验判断】AnchorUnderMouse + 负行列式（翻转）变换的组合在部分平台/版本有锚点漂移历史；稳妥做法是显式锚点公式 setTransform(view_transform(ppm_new, mm_of(anchor), anchor))，同时天然可 offscreen 测试。
- 【经验判断】offscreen 测试字体度量与真机不同，断言文字宽度会脆；QTest 给 QGraphicsView 的鼠标事件坐标是视口逻辑像素，取整误差 ≤1px 需容差。
- 【未做】仓库 pytest 全套未跑（本任务只读调研、未要求回归）；性能建议无实测帧率数字；Qt diagramscene 文档页经 fetch 证实不含 undo 实现（404 于 raw movecommand.cpp 路径），MoveCommand 模式为经验判断（Qt 社区惯用法）。

## 重构设计蓝图（经独立评审修订）
# 预览与排版重构设计蓝图（Mega Pro 写字/裁纸上位机）· 修订版 v2

> 性质：设计文档（不含实现代码）。实施者可按本文的文件/函数契约直接落地。
> 权威顺序：仓库安全约束（guard 不削弱、Z 永不为负、核心零 Qt 依赖）> 本轮审计证据 > 本蓝图 > 既有文档（README/PRD 与代码不符处以代码为准，见 AGENTS.md）。
>
> **本轮核查（本会话我自己执行的命令与输出，非引用）**：
> 1. `PYTHONPATH=src python -m pytest -q --basetemp=.pytest_tmp -p no:cacheprovider` → `129 passed in 10.80s`（修订轮重跑；上一轮同命令为 `129 passed in 14.96s`）。全量保绿基线。
> 2. `grep -rn "emit_gcode\|svg_file_to_gcode\|toolpath_to_svg" src tests --include=*.py` → src 只有定义处（`toolchain/svg_to_gcode.py:319,334`、`preview/to_svg.py:19`）与 `job.py:3` 注释提及，无生产调用方；仅 `tests/test_toolchain.py:72,89,99` 引用。
> 3. `grep -rn "BED_H - \|bed_h - \|210 - y\|(x, -y)\|1.0 - y" src --include=*.py` → `job.py:161`、`canvas.py:22,59,60`、`doc_import.py:53`（`BED_H - 2 * margin`，尺寸计算非翻转）、`items.py:30,39,56,65,68,75`、`layout_page.py:103,638`、`undo.py:96,104`、`main_window.py:1304`、`text_to_svg.py:253`（另有 `layout_page.py:687` 的 `ysum - y`）。
> 4. **竖标尺几何探针**（PySide6 6.11.2，`QT_QPA_PLATFORM=offscreen`，按 `canvas.py:239` 公式 `y = origin − mmv*ppm`、`origin = mapFromScene(0,0).y()`、`paper = 210 − scene_y` 复算）：`label 0 → paper y=210.0；label 50 → paper y=260.0；label 210 → paper y=420.0；paper y=0 正确刻度 view y=449.0 而代码放在 29.0；paper y=50 处读数 label = −160.0`。结论：label L 落在 paper y=210+L（恒偏/整段脱床），**不是**镜像 210−L。详见 §2.2 rulers 与 §10-10。
> 5. 重读 `profiles/mega-pro-marlin.yaml`：现为 `pen_down_z: "17.0"`（:6）、`cut_touch_z: "17.5"`（:13），全文件无 3.2；本会话早前一轮读到的是 `pen_down_z: 3.2`（:6）——该文件在会话期间被用户改动（git 状态 `M profiles/mega-pro-marlin.yaml`）。以**当前树**为准，风险条已改写（§9）。
> 6. `grep -n "_svg_to_paths" tests/*.py src/megapro/gui/layout/*.py` → 生产与测试双依赖：`layout_page.py:46`（定义）、`doc_import.py:16,39`、`layout_page.py:543,814`、`tests/test_gui_edge.py:43`、`tests/test_gui_layout_window.py:18,24`。
> 7. `sed -n 265,275p src/megapro/gui/layout/layout_page.py` → `_refresh_props` 读的是 model（`it.pos[0]`/`it.pos[1]`，:270-271），证实评审对 break #9 的指正。
> 8. 重读 `src/megapro/gui/presets.py`（全文）与 `src/megapro/gui/worker.py`（全文）核实评审 missing #1 / break #6 引用行号。
> 9. 本轮**只读**：未修改任何仓库文件。

---

## 1. 根因结论

全仓库没有唯一的坐标权威：y 翻转以 12+ 处手写形式散落在模型/画布/作业/导出/导入五条链上，导致「同一数据两个 y 约定」（`layout/export_svg.py:5` 自称 y 向下、`export_svg.py:27-32` 实写 y-up 数值、作业侧再按 `job.py:161` 翻一次）与旋转镜像（`layout/items.py:56` 本地 `(x,−y)` + `setRotation` 对 `layout/model.py:65-66` 的 CCW 矩阵）。预览画的不是将发送的 G-code（`main_window.py:1292-1308` 画 flip+平移后的 polylines，`preview/to_svg.py` 画未翻转 parse 输出），工件原点又在载入时一次性烘进路径（`main_window.py:1199-1204`）而设原点不重编译（`main_window.py:790-806`），于是镜像、错位、拖线（`job.py:87,123` 的 `cur=(0.0,0.0)` 初值吞掉首段定位 G0）既不被预览暴露也不被测试拦截。解法一句话：**场景≡纸面 mm（y-up）+ 唯一换算权威 + 「编译出的 G-code 文本」作为唯一真源（预览=解析同一份文本，发送=同一份文本）+ 工件原点作为编译期纯平移并触发重编译**。

---

## 2. 目标架构

### 2.1 坐标权威定义（每个映射一张表）

**总原则**：机器坐标系是唯一权威；纸面坐标 ≡ 机器 XY（M114 logical 恒等）；工件原点是纯平移；y 翻转数学**只存在于 `canvas/coords.py` 一处**（`flip_y_scalar` 及其两个包装），在两个显式边界被调用 —— **视图层翻转（唯一显示翻转，`canvas/view_transform.py`）**与 **SVG 互换层编解码（唯一格式翻转）**。除这两处调用点外，任何文件出现翻转写法（§8.1 给出精确 pattern）一律按 bug 处理。

| 映射 | 唯一函数（权威文件） | 定义域 | 值域 | 原点 | Y 方向 / 旋转约定 |
|---|---|---|---|---|---|
| 纸面 ↔ 机器 | `machine_from_paper(p)` / `paper_from_machine(p)`（**恒等**，`canvas/coords.py`） | 纸面 mm | 机器 mm（G-code X/Y 绝对 = M114 logical） | 床左下 (0,0) | +Y = 远离操作者（向纸深）；单测锁死恒等 |
| 工件坐标 → 纸面/机器 | `coords.translate_paths(paths, dx, dy)`（**迁入 coords**；`controller.translate_paths`（`controller.py:257`）保留为 re-export shim，阶段 5 删） | 工件 mm（文档/页面坐标） | 纸面 mm | 工件原点可设，= 机器里的一个点 | **纯平移**（无旋转/缩放/镜像）；不发 G92 |
| SVG 文件 ↔ 纸面 | `paper_to_svg_ydown(paths, bed_h)` / `paper_from_svg_ydown(paths, bed_h)`（`coords`；均基于 `flip_y_scalar(y, span) = span − y`） | SVG 用户单位 = mm，y-down | 纸面 mm y-up | `y_svg = bed_h − y_paper`（**双射/对合**，golden 锁 involution） | SVG 侧 y 向下（浏览器所见 = 从上方看床、操作者在图下方）；**格式编解码**，不是显示翻转 |
| 内容归位（放置） | `place_at_anchor(paths, anchor, target)` / `anchor_point(bbox, anchor)`（`coords`，纯平移） | 内容 bbox | 工件 mm | 九宫格锚点（`bl/bc/br/ml/mc/mr/tl/tc/tr`，默认 `bl`）→ 目标点（默认工件原点 (0,0)） | 无翻转、无缩放（单位换算不做，§9） |
| 场景 ↔ 纸面（Qt 场景坐标） | **同值**（PathItem 的 paths/pos 直接用纸面 mm y-up） | 场景单位 = mm | = 纸面 | 场景 (0,0) = 床左下 | 场景 +Y = 纸面 +Y；`angle_deg` +θ = 纸面 CCW = `model.transformed_paths` 的 R(+θ)（`model.py:65-66`）= y-up 场景里的 `setRotation(+θ)` |
| 视图（屏幕）↔ mm | `view_transform(ppm, anchor_mm, anchor_view)`（**全仓库唯一允许负比例尺**）+ `mm_from_view` / `view_from_mm`（`canvas/view_transform.py`，Qt 薄封装） | 视口逻辑像素（浮点） | mm（浮点） | `QTransform(ppm,0,0,−ppm,dx,dy)` | 屏幕上方 = 纸面 +Y；**唯一显示翻转边界** |
| 触纸 Z → 机器绝对 Z | `ZMap(safe_z, down_z)`（`gui/job.py`）：写字落笔 = `pen_down_z`；裁刀下压 = `cut_z_for_depth(cut_touch_z, depth)` = `cut_touch_z − depth`（`job.py:143`）；抬笔 = `safe_z` | profile 标定值（绝对 Z） | 机器绝对 Z | 机器 Z（G28 后 Z0=下限位） | Z 越大越高；编译期断言输出所有 Z ≥ 0 |
| 段间低抬 | `lift_z = min(zmap.safe_z, zmap.down_z + motion.travel_lift_mm)` —— **由 compile 流水线计算**（`travel_lift_mm` 属 `MotionParams`，不属于 ZMap） | MotionParams + ZMap | 机器绝对 Z | — | — |

**「二选一」的明确答案**：Y 翻转选**视图层**（`view_transform` 负行列式）为唯一显示边界。SVG 互换层的 y-down 是文件格式属性（浏览器语义），由 `coords.paper_to_svg_ydown`/`paper_from_svg_ydown`（底层同一 `flip_y_scalar`）编解码。**翻转数学只有一处实现**（`flip_y_scalar`），两个调用边界各司其职。

**单位约定**：SVG 用户单位 = mm（`svg_to_gcode.py:296` `parse_svg` 不读 width/height/viewBox，保持；见 §9）。场景/模型单位 = mm。

**210 常量范围（澄清）**：「禁止第三处 210」专指**翻转/床尺寸常量** —— `BED_W/BED_H` 只在 `canvas/coords.py` 定义一次（数值字面仅此一处），其余（`job.py:38-39` check_bounds 默认参、`job.py:154` flip_y 默认参、`preview/to_svg.py:19`、`main_window` 材料表、`presets.py:47-48` 等）在阶段 1–5 逐步改为引用常量，阶段 5 验收（§8.4）。

### 2.2 模块清单

**新建**（`src/megapro/gui/canvas/` 新包 = 两页共享画布核心）

| 文件 | 职责与契约 |
|---|---|
| `canvas/__init__.py` | 导出公共符号 |
| `canvas/coords.py` | **唯一换算权威 + 唯一翻转数学，零 Qt 依赖**（不 import PySide6）：`BED_W = BED_H = 210.0`（唯一数值定义处）；`flip_y_scalar(y, span) -> span − y`（**全仓库唯一翻转实现**）；`paper_to_svg_ydown`/`paper_from_svg_ydown`（基于 `flip_y_scalar(·, bed_h)`，对合）；`machine_from_paper`/`paper_from_machine`（恒等）；`translate_paths`（自 `controller.py:257` 迁入）；`bbox_of`、`anchor_point`、`place_at_anchor`（纯平移）；`grid_steps(ppm, target_px=40)`（Heckbert）；`snap(v, pitch)` |
| `canvas/view_transform.py` | Qt 值类型薄封装（**唯一允许负比例尺**）：`view_transform(ppm, anchor_mm, anchor_view) -> QTransform`（显式六元矩阵）；`mm_from_view(view, vp_pt)`/`view_from_mm(view, mm)`（走 `viewportTransform()`，浮点）。**禁止 `mapToScene/mapFromScene`**（int 量化，§8.3） |
| `canvas/paper_scene.py` | `PaperScene(QGraphicsScene)`：`drawBackground` 网格（minor/major 两级，`major*ppm<6px` 只画 major）、床框 `(0,0,210,210)`、原点十字、材料/可用区/安全边距三框；`sceneRect(-20,-20,250,250)`；rect 已是场景 mm（删 `canvas.py:59-64` 的 py_lo/py_hi 换算） |
| `canvas/paper_view.py` | `PaperView(QGraphicsView)`：`set_zoom(ppm, anchor_view)`（显式锚点公式，可 offscreen 测试）；`fit`；滚轮缩放（1.15，ppm 钳位 `[fit_ppm,100]`）；中键平移；鼠标事件一律 `mm_from_view(view, event.position())`（修 `canvas.py:133,144,166,183,187,196` 的 `.toPoint()` 量化） |
| `canvas/rulers.py` | `RulerWidget(view, orientation)`：tick 定位 `view_from_mm` 浮点（paper y=mmv 的刻度 = `view_from_mm(view, QPointF(0, mmv)).y()`，**0 刻度对齐 paper y=0**）；刻度范围 = 视口四角 mm 区间（床外延续）；major 带数字；删 `int()` 截断与 `max(1,int(pitch))`。**修 bug 定性**（本轮探针实证，见 §10-10）：现状 `canvas.py:236-242` 取 `origin = mapFromScene(0,0).y()`（= paper y=210 的视口位置）再 `y = origin − mmv*ppm`，label L 实落 paper y=**210+L**（探针：label 50→paper 260；paper 50 处读数 −160）= 整段脱床的恒偏（读数 ≡ paper_y−210），不是镜像 210−L；两者仅在 L=105 对称轴重合。回归测试只断言**修复后正确位置**（`view_from_mm(0,mmv).y()`），不依赖错误形态描述 |
| `canvas/items.py` | `PathItem(QGraphicsPathItem)`：一图元一条 `QPainterPath`，`pen.setWidthF(0)`；paths 以本地纸面 y-up **直画**（删 `items.py:56` 的 `(x,−y)`）；`boundingRect` 用 `model.Item.bbox()`（删 `items.py:39` 的 `-max(y1,y0)`），改前 `prepareGeometryChange()`；`transformOriginPoint=(0,0)`（= pos，与 `model.transformed_paths` 旋转支点严格一致）；`itemChange(ItemPositionChange)` 只 return 吸附值（禁止 setPos） |
| `canvas/snap.py` | `SnapEngine` 纯函数：`snap_point`（网格，pitch=`grid_steps` 的 minor）、`snap_to_objects`（端点/中点/边，关键点缓存）；绘制工具同样接入 |
| `canvas/handles.py` | `SelectionHandles`：旋转手柄 + **等比缩放**手柄（`model.Item.scale` 是标量，非等比不支持：拖角按对角距离比取 k，按 Shift 之外的自由拉伸一律按 min 比例等比化）；缩放/旋转支点 = 本地原点（= pos，与 model 数学一致）；拖动中临时 `setScale/setRotation`，`mouseRelease` 一次性 push `ChangeItemPropsCommand(old,new)`；**多选整体等比缩放公式**：`scale_i' = k·scale_i`、`pos_i' = A + k·(pos_i − A)`（A = 选择集 bbox 锚点的绝对坐标），保相对布局；手柄恒定屏幕大小用 `setScale(1/ppm)` |
| `canvas/gcode_items.py` | `GcodePathItem`：吃 `gcode_parse.Segment`，三条 QPainterPath 分色（TRAVEL 红虚 / DRAW 蓝实、刀紫实 / PLUNGE·RETRACT 灰）；`set_progress(done: int)`（done = 已发送行数，worker 1 基计数；段全亮 iff `seg.line_range[1] <= done`，因 `line_range` 是 0 基半开 `[i0,i1)`，与 1 基 done 比较恰对齐）；`LiveMarker`：M114 logical 十字（注释写明 ok≠已移动、Count 不可信） |
| `canvas/undo_cmds.py` | 自 `layout/undo.py` 移入：命令只写 `model.Item`，同步唯一入口 `page._sync_gi`；Add/Remove 重建式（`undo.py:34-43` 保持）；`setPos` 去 `bed_h−y`（`undo.py:96,104`）；`MoveItemsCommand` 带**手势 token**（press→release 一个 token），`id()` 不再恒 1001，`mergeWith` 先比 token（修误合并）；命令不长期持 gi |

**新建（零 Qt 纯逻辑）**

| 文件 | 职责与契约 |
|---|---|
| `src/megapro/gui/gcode_parse.py` | **将发送 G-code 文本 → 段列表**。`@dataclass(frozen=True) Segment`（**具名字段**，非裸 tuple）：`kind: Literal["SETUP","TRAVEL","PLUNGE","DRAW","RETRACT","SYNC","UNKNOWN"]`、`p0: (x,y,z)\|None`、`p1: (x,y,z)`、`line_range: (int,int)`（**0 基半开 `[i0,i1)`** 行号）。`parse_lines(lines, *, z_down: float, z_safe: float, tool: Literal["pen","knife"]) -> list[Segment]`（**分类需要 Z 参照与工具**：DRAW = G1 含 X/Y 且 \|z−z_down\|≤1e-6；TRAVEL = G0 含 X/Y；PLUNGE = 纯 Z 的 G1 下行到 z_down；RETRACT = 段间/收尾的纯 Z 上行 G0；**首段前的 `G0 Z{z_safe}` 归 SETUP**；M400=SYNC；G28 记 HOME 并置后续位置未知；其他/ M112 = UNKNOWN 但保留行号）。模态机 G90/G91、G21（G20 → ValueError）；**G2/G3/G5/G92 → ValueError**；起点未知 `p0=None`。另 `classify(segments, tool)` 供配色（笔蓝/刀紫） |

**改造**

| 文件 | 改动契约 |
|---|---|
| `src/megapro/gui/job.py` | 保留纯逻辑零 Qt。新增 `JobSpec`（`paths_paper`（工件 mm y-up、**未平移**、**保持源顺序**）、`source_name`、`tool`、`placement: Placement(mode:'anchor'\|'preserve', anchor, target)`、`opt: OptParams(dedup, sort, pen_diameter_mm)`、`motion: MotionParams(feed_xy, feed_z, travel_lift_mm, passes)`、`zmap: ZMap(safe_z, down_z)`、`material: Material(w,h,margin)`、`work_origin \| None`）；`CompiledJob(lines, segments, bounds, meta)` + `runnable` property（= `bounds.ok` 且标定齐）；`compile_job(spec, *, strict=True, dry_run=False) -> CompiledJob`（流水线：`opt.dedup`→（**`opt.sort` 为真才** `nearest_neighbor_sort`）→ placement → `translate_paths(+work_origin)` → `check_bounds_v2` → Z 断言 → `gcode_for_*` → `parse_lines(lines, z_down=…, z_safe=…, tool=…)`；`strict=True` 越界抛 `JobBoundsError(report)`，**`strict=False` 返回同一个 `CompiledJob`**（report 在 `CompiledJob.bounds`，预览画违规、执行按钮绑 `runnable`））。`check_bounds_v2(paths, *, travel, material, margin, pen_radius) -> BoundsReport`：**全在机器坐标判定**（work_origin 平移之后）；行程框=机器 (0,0,210,210)；材料框=机器 (0,0,w,h)（**纸角铺在机器 (0,0)**，见 §5）；可用区=材料内缩 margin；**判定区 = 行程 ∩ 可用区**（A4 210×297 → (0,0,210,210−margin)，显式等价现 `min(material,210)` 语义 `main_window.py:1208-1214`）；pen_radius 只内缩远端（`job.py:51-54` 语义保持）；`BoundsViolation(path_idx, point, frame_name)`。**根因 #5 修复**：`gcode_for_drawing/cutting` 的 `cur=(0.0,0.0)`（`job.py:87,123`）→ `cur=None`：首段**必发**定位 `G0 X.. Y..`，且裁刀下压发生在定位到 `p[0]` 之后。**空跑兼容（评审 break #7）**：emitter 的校验由 `safe_z <= down_z → ValueError`（`job.py:82,118`）改为 **`safe_z < down_z` 才 raise（允许相等=不下压/空跑）**；`compile_job` real 模式断言 `safe_z > down_z ≥ 0`（严格），`dry_run=True` 用 `ZMap(safe_z, down_z=safe_z)`（断言 `down_z == safe_z`）→ 空跑可编译且 Z 恒 ≥ 0。`test_drawing_invalid_z_order`（pen 30 / safe 17）仍 raise ✓。**`flip_y` 阶段 1 保留为转发 shim**（1 行调 `coords.paper_to_svg_ydown`，docstring 标 deprecated），阶段 3 删（见 §7） |
| `src/megapro/gui/gcode_parse.py` 依赖方向 | job → gcode_parse 单向；gcode_parse 不 import job |
| `src/megapro/gui/controller.py` | 保持零 Qt。新增 `build_frame_sequence(bbox_xy, safe_z, feed_xy, rounds=1)`（bbox 矩形环绕 G0）、`build_move_to_sequence(x, y, safe_z, feed_xy)`、`build_goto_origin_sequence(work_origin, safe_z)`、`can_start_job(state_name, homed) -> str\|None`（未归位/未连接 → 中文原因）。`translate_paths` 改为 re-export `coords.translate_paths` 的 shim（名称不变，`main_window.py:1202` 等现有 import 不破）。`sequence_ok` 不变 |
| `src/megapro/gui/opt.py` | `dedup_and_optimize` 不变；**排序语义澄清（评审 missing #4）**：`polylines_for_svg(path, *, sort: bool = True)`（`job.py:28-32`）加参数（默认 True 保旧契约）；**JobSpec 构建一律走 `parse_svg` 原序**（不经 `polylines_for_svg`），NN 排序只在 compile 内按 `opt.sort` 执行 → `opt.sort=False` 真正生效（文件序直通） |
| `src/megapro/gui/layout/model.py` | 契约改写（消灭 `model.py:27` 「y 可上可下」）：`Item.paths` = 本地纸面 y-up mm，**入库归一：内容 bbox 左下 = 本地 (0,0)**（纯函数 `normalize_local(item) -> delta`，**只在创建/导入入口调用，model/export 内部不自动跑**——直接构造 Item 的测试不受影响）；`pos` = 本地 (0,0) 的页面位置；旋转/缩放支点 = 本地 (0,0)（`transformed_paths` 数学**不动**，`test_gui_layout.py:28-37` 保持）；新增 `flatten_visible(doc) -> list[Polyline]`（**评审 unclear #4 补齐**：`items_visible()` 按 z 升序 → 每项 `transformed_paths()` 拍平变换 → 按序拼接，跳过 <2 点折线） |
| `src/megapro/gui/layout/export_svg.py` | **阶段 3 才改**（阶段 2 保持现状契约，见 §7）。目标契约：`document_to_svg(doc, *, bed_h=BED_H)` 经 `coords.paper_to_svg_ydown` 写 y-down 数值；输出 `width="210mm" height="210mm" viewBox="0 0 210 210"`；z 升序、跳 hidden、无 `<text>/<image>`/样式（`test_gui_layout.py:60-66` 契约保持）；docstring 改为事实（删「当普通 SVG 载入即可」误导句） |
| `src/megapro/gui/layout/doc_import.py` | 生成方保持 y-down；入库走**组导入契约**（见下）；`_fit_to_bed` 不变；`from megapro.gui.layout.layout_page import _svg_to_paths`（:16）名称保持（评审 missing #6） |
| `src/megapro/gui/layout/layout_page.py` | 只留工具/属性/命令编排。**组导入契约（评审 break #4）**：`_import_group(paths_svg_list, *, name, anchor='bl', target=(0,0)) -> list[Item]` —— ①整组 `paper_from_svg_ydown`；②**组级一次** `place_at_anchor`（对整组拼接 bbox；**严禁逐 Item 归位**，否则段落/表格塌到原点）；③逐 Item `normalize_local`（paths 平移 + pos 补偿，页面几何不变）。单图元 = 组长度 1；Word/Excel 多 Item 用组契约（保 `_fit_to_bed` 后的相对布局）。**保留 `_svg_to_paths` 名称**（`layout_page.py:46`，被 `doc_import.py:16`、`tests/test_gui_edge.py:43`、`tests/test_gui_layout_window.py:18` 依赖）。绘制工具直接产纸面 y-up，跳过 ①但走 ③。`_sync_gi` 去 `bed_h−`（`layout_page.py:103`）；`finish_draw` 的 `to_paper`（`:637-638`）改经 coords。**拖动回写（根因 #8；评审 break #9 更正）**：`_refresh_props` 本来就正确读 model（`layout_page.py:269-271` 读 `it.pos`，无需改）；病灶是拖动只改 gi 不回写（`sync_to_model` 仅 `_apply_props/_on_save_svg/_on_export` 调用，`:304-308/:714/:730`）→ 修复 = PathItem `mouseRelease` 时把位移写成 `MoveItemsCommand`（**命令写 model.Item**，`_sync_gi` 推场景；一次性 gi→model 回写只发生在构建该命令时，读取源永远是 model，不存在「_refresh_props 经 scenePos() 读取」这种设计）。**数值定位（根因 #10）**：单选 = 九宫格锚点绝对定位（`pos = target − R(θ)S(s)·anchor_offset`）；**多选 = 按选择集 bbox 锚点整体平移/等比缩放**（每项 own old/new，修 `:286-292` 同 pos 塌缩）；W/H 等比联动（`sp_h` 生效：`scale × h_target/h_cur`，修 `:294-298`）。**导出链（阶段 3）**：`export_requested = Signal(object)` 传 `JobSpec`（`to_job_spec()` = `flatten_visible(self.doc)` + `Placement(mode='preserve')` —— 版面坐标即工件坐标，**排版直传不做 anchor 归位**）；`_on_save_svg` 同格式写盘 |
| `src/megapro/gui/main_window.py` | **单一重编译汇流 `_recompile()`**；`self._job_spec` 内存唯一源（修根因 #1/#2）。**完整触发集（评审 missing #2）**：设/清工件原点（`:790-806`）、feed/Z 速度/跳段（`_on_feed_changed`）、去重/顺序/笔径（`_on_opt_changed`，**不重读文件**，删 `:1146-1148`）、材料/切深/边距、**工具切换**（`_on_tool_changed` `:1099-1104` 改为换 ZMap+emitter 后重编译、**保留几何**，不再清空——显式行为变更）、**Z 标定回写**（`_reload_profile_z` `:974-978` 后）、**预设加载/应用**（`_apply_preset_cfg`/`presets.apply_config` 末尾）、载入/排版导出（换 spec）。**执行期互斥（评审 missing #3）**：`_job_running` 期间 `_recompile` **no-op** 并提示「执行中，参数改动本次作业结束后生效」，`_on_job_done` 后自动补一次 `_recompile()`；进度高亮用开始执行时的不可变快照 `self._running_compiled`（与 worker 收到的 `list(lines)` 拷贝同源，`main_window.py:1322`）。删 `already_paper`（`:1164,1276`）与 `_on_layout_export` tempfile 往返（`:1269-1281`）；`_draw_preview`（`:1292-1308`）→ `PaperView + GcodePathItem`；叠加 LiveMarker（`_on_position` `:777-786`）/内容 bbox/三框/违规点。**`homed` 门禁（评审 break #6）**：见下 worker 契约；`homed` 仅在 home 序列 `sequenceDone(ok=True)` 后置位，`open/FAULT/ESTOP/断开` 清零；`can_start_job` 拦执行/Frame/设原点。**`_send` 预检参数更正**：`sequence_ok(lines, allow_z=True, …)` 硬编码（`:872`）改为传 UI 真值（`allow_z_cb`），与 worker 判据一致（否则预检永过而 worker 中途拦——评审 break #6 的假阳性路径之一）。`_load_svg_path` 改为构建 JobSpec（`parse_svg` 原序）+ `Placement`（`parse_svg_meta` 声明 210×210 → `preserve`，否则默认 `anchor@bl→(0,0)`；UI 可切换后重编译） |
| `src/megapro/gui/presets.py` | **评审 missing #1**：`collect_config`（:62-85）/`apply_config`（:88-135）所读写的 `mw._work_origin`（:71-73/:99-106）、`_feed_*`/`_pen_diameter_mm`/`_opt_*`（:107-116）**属性名一律不改** —— `MainWindow` 保留这些字段作为 `JobSpec` 参数的**投影**（字段即 spec 槽位的读写面），`tests/test_gui_presets.py:64` 的假 mw 兼容。唯一行为变更：`apply_config` 末尾把 `mw._on_clear_job()`（:133）换成 `mw._on_preset_applied()`（同步 spec + 有几何则 `_recompile()`，**预设通道必须触发重编译**）；`preset_z_problems`（:138-152）保持 |
| `src/megapro/gui/worker.py` | **最小增补（评审 break #6，阶段 4）**：`sequenceDone = Signal()`（:50）→ **`sequenceDone = Signal(bool)`**（True=每行 `send_line` 都成功）；`send_sequence`（:208-230）记录成功与否再 emit（现状 break 后仍无参 emit 且非 FAULT/ESTOP 就回 READY，GUI 无法区分「跑完」与「被 guard 拦一半」）。**guard/estop/pause/job 语义零改动**（`_guard` :116-118、M112 免检直发 :297-311 不动）。`run_job`/`jobDone(bool)`/`progress(i,total)`（`enumerate(lines,1)` 1 基，:255）不变 |
| `src/megapro/safety/guard.py` | **一字不改**（不削弱不扩展；`BLOCKED_PREFIXES` 唯一事实源；M303 不在阻止列是既定事实，不为它改列表） |
| `src/megapro/toolchain/svg_to_gcode.py` | `parse_svg`/`nearest_neighbor_sort` 保持；新增 `parse_svg_meta(path) -> SvgMeta(width_mm,height_mm,viewBox)`（只读属性，供 Placement 判定）；`emit_gcode`/`svg_file_to_gcode` 阶段 5 退役 |
| `src/megapro/gui/text_to_svg.py` | `:253` 的 `(1.0 - y) * size_mm`（归一化域 y-up→SVG y-down 的**格式编码**）**迁调 `coords.flip_y_scalar(y, 1.0)`**（数学等价，`test_gui_text.py:56/82` 的 y-down golden 不变）→ 翻转实现收敛到唯一一处（评审 missing #5 的裁决） |

**退役**（各阶段时点见 §7）

| 文件/符号 | 退役时点与原因 |
|---|---|
| `layout/items.py`、`layout/canvas.py`、`layout/undo.py` | 阶段 2 删（被 `canvas/*` 取代） |
| `job.flip_y` | 阶段 1 → 转发 shim（兼容 `main_window.py:1196` 现有 import）；**阶段 3 删**（届时 main_window 已不再调用）。评审 break #1：不再存在「删了符号但调用方还在」的中间态 |
| `main_window._draw_preview`、`already_paper`、排版导出 tempfile 往返 | 阶段 3 删 |
| `toolchain.emit_gcode`/`svg_file_to_gcode`、`preview/to_svg.toolpath_to_svg`（polylines 入口） | 阶段 5 删/改 `preview_svg_from_segments(segments, *, work_origin)`（Z0/Z1 双轨语义；grep 证实无生产调用方） |

### 2.3 根因 → 修复位置对照（10 条全收）

| # | 根因（审计） | 修复契约 |
|---|---|---|
| 1 | 工件原点载入时烘入、设原点不重编译 | `JobSpec.work_origin` + `_recompile()` 全触发集（含预设/Z 标定/工具切换）；运行中 no-op + 完成后补编译 |
| 2 | `already_paper` 双入口 + `_on_opt_changed` 重读已删临时文件 | 删 `already_paper`；SVG 单一交换格式（y-down 对合）；JobSpec 内存源；排版→作业传 `JobSpec` 对象 |
| 3 | paint `(x,−y)` 与 model CCW 互为镜像 | 场景=纸面 y-up、PathItem 直画、`transformOriginPoint=(0,0)`；旋转一致性测试 |
| 4 | `flip_y` 按 210 床反射、锚点错乱 | `Placement`（preserve/anchor 九宫格）+ 预览画 bbox/锚点/三框 |
| 5 | 首段漏定位 G0、裁刀误下压 | `cur=None`：首段必发 `G0` 定位且下压在其后 |
| 6 | 非零角度预览反向 | 同 #3；`PathItem.mapToScene ≡ model.transformed_paths` golden |
| 7 | Word/Excel 漏翻 + 另存回读再翻 | **组导入契约**（整组翻转+组级归位，保相对布局）；导出对合 |
| 8 | 拖动不回写 model、undo 误合并 | 拖动 mouseRelease → 手势 token `MoveItemsCommand`（命令写 model）；`_refresh_props` 读取路径本身不动（本就正确） |
| 9 | 竖标尺 0 点差一个床高（恒偏 210mm，见 §2.2 rulers 探针） | `RulerWidget` 用 `view_from_mm` 浮点、0 刻度对齐 paper y=0；测试断言修复后位置 |
| 10 | 数值定位/吸附脱节、多选塌缩、高不生效 | `normalize_local` + 锚点化数值定位 + 多选按选择集 bbox（`pos' = A + k(pos−A)`）+ W/H 联动 + `SnapEngine` 全工具接入 |

---

## 3. 预览≡实际机制

### 3.1 数据流（构造性保证）

```
JobSpec（工件坐标 polylines（源序）+ 全部参数，内存唯一源）
   │  compile_job(spec, strict, dry_run)      [gui/job.py，纯逻辑]
   │   ├─ opt.dedup；opt.sort 才 NN 排序        [gui/opt.py / svg_to_gcode.nearest_neighbor_sort]
   │   ├─ placement（preserve=直用 / anchor=place_at_anchor）
   │   ├─ translate_paths(+work_origin)        [coords]  ← 原点在此生效；改原点 = 重跑本流水线
   │   ├─ check_bounds_v2（机器坐标，行程∩可用区）→ BoundsReport
   │   ├─ Z 断言（real: safe>down≥0；dry_run: down==safe；全 Z≥0）
   │   └─ gcode_for_drawing / gcode_for_cutting（机器绝对 Z；cur=None 保证先定位后下压）
   ▼
CompiledJob.lines: list[str]  ★唯一真源 = 将发送的字节流
   │
   ├──► gcode_parse.parse_lines(lines, z_down, z_safe, tool) → CompiledJob.segments
   │        └──► GcodePathItem 三色 + set_progress(done) + LiveMarker
   └──► worker.reqRunJob.emit(list(lines))    [worker.py:240 逐行 guard + 发送]
```

**构造性保证**：预览渲染器吃 `parse_lines(将发送的同一份 lines)`，发送器吃同一份 `lines`；生成器 bug（如 #5 拖线）会原样出现在预览并被 golden 拦下。UGS 教训（偏移不进可视化器）被规避：work_origin 在编译期并入 lines。

**段分类契约**（`parse_lines(..., z_down, z_safe, tool)`，评审 unclear #1/#9）：

| kind | 判定 | 预览样式 |
|---|---|---|
| SETUP | **首段前的 `G0 Z{z_safe}`**（及 G90/G21） | 不画（或灰） |
| TRAVEL | G0 含 X/Y（在抬升 z） | 红虚线 |
| DRAW | G1 含 X/Y 且 \|z−z_down\|≤1e-6 | 蓝实线（tool='pen'）/紫实线（'knife'） |
| PLUNGE | 纯 Z 的 G1 下行到 z_down | 灰短竖标 |
| RETRACT | 段间/收尾纯 Z 上行 G0 | 灰短竖标 |
| SYNC/UNKNOWN | M400 / 其他 | 不画（保留行号） |

零长段（`p0==p1`，如 `job.py:94-95` 把 p[0] 重复发一次 G1）：解析保留、渲染跳过；`test_compile_parse_roundtrip` 端点比对时**过滤 p0==p1 的 DRAW 段**再比对。

### 3.2 两页共享画布核心

- 共享层：`PaperScene + PaperView + RulerWidget + coords/view_transform`；两页各一实例，坐标语义完全一致。
- 排版页附加层：`PathItem + SelectionHandles + SnapEngine + undo_cmds`。
- 作业预览页附加层：`GcodePathItem + LiveMarker + BoundsReport 违规标记`。
- **抬笔空跑**（评审 break #7 的正确形态）：`compile_job(spec, dry_run=True)`（`ZMap(safe_z, down_z=safe_z)`，emitter 允许 `down==safe`）→ 同一 parse/预览（全部段显示为 TRAVEL/SETUP，无 DRAW/PLUNGE）；可选「物理空跑」把这份 lines 发机器。guard 逐行照查。

---

## 4. 两页重构形态（面板布局与交互）

### 4.1 Tab0「控制 / 作业」= 文件作业预览页

1. **连接条**（不变）。
2. **DRO + 工件原点条**：X/Y/Z（XY 工件坐标=机器−偏移，Z 机器绝对，M114 logical）；「设工件原点」「清原点」「回原点」。**设原点后立即 `_recompile()`**，预览随偏移平移。
3. **对准工具条**（新）：「走边框 Frame」（描内容 bbox，1/3 轮）、「四角点检」×4 +「中心点检」、「空跑校验」（dry_run 版 lines）。
4. **Jog + 笔/刀控制**（不变）；「一键寻零」在未勾『允许 Z』时禁用（内含 `G28 Z`，`guard.py:114-119` 必拦）。
5. **回显/命令台**（不变）。
6. **作业面板**：工具+材料行；载入行（「载入 SVG…」「从排版页取当前版面」，来源标签显示 `source_name` + Placement 模式 + 内容 bbox，模式可切换重编译）；**预览 = PaperView**（三色走线/红虚空移/床框/原点十字/标尺/网格/材料三框/bbox+锚点/违规红点/运行中 LiveMarker+进度高亮+图例）；参数行（全触发 `_recompile()`）；执行行（开始/暂停/继续/中止+进度）。
7. **E-stop 行**（不变，Space/Esc）。

### 4.2 Tab1「排版 / 制作」

1. **工具条**：现有全部 +「吸附开关 + 网格间距（minor）显示」。
2. **画布区**：`RulerWidget`×2 + `PaperView`；网格两级/床框/原点十字；滚轮缩放到光标（显式锚点）、中键平移。
3. **属性面板**：X/Y（九宫格锚点绝对定位，默认 bl）、宽/高（等比联动）、角度°、缩放；多选=选择集 bbox 锚点整体平移/等比缩放；复制/粘贴/副本/微调走 undo；「另存为 SVG…」「导出并送去执行」（发 `JobSpec`）。
4. 交互：拖动结束回写 model 并进撤销栈；手柄 mouseRelease 才入 undo；双击重编文字；绘制工具同样吸附。

---

## 5. 对准与作业原点工作流（铺纸 → 对准 → 执行）

1. **连接**：连接（复位，引导 ~16s）→ **一键寻零**（需『允许 Z』）→ `sequenceDone(ok=True)` 才置 `homed`（ok=False 提示被拦行并保持未归位）。此后 M114 logical 显示；ok≠已移动、Count 不可信（AGENTS/REPORT §6）。
2. **铺纸**：**纸基准角对齐机器 (0,0)**（材料框锚定机器 (0,0)，与工件原点无关）；选材料（A4 提示超程，判定区=行程∩可用区）。
3. **装载内容**：排版「导出并送去执行」（`Placement(mode='preserve')`：版面坐标即工件坐标）或「载入 SVG」（meta 声明 210×210 → `preserve`；否则默认 `anchor@bl→(0,0)`，小画板不再跑床尾）。预览显示 bbox/锚点/三框。
4. **对准**：①**走边框 Frame**（bbox 空移环绕）；②**四角/中心点检**（G0 逐点）；③**设工件原点**（Jog 到纸面目标 → 记 `work_origin=(M114 x,y)`，纯平移不发 G92 → 预览立即重编译平移 → 「回原点」复核）；④排版页锚点数值定位。
5. **执行前检查**：越界预检（violations 非空 → 禁执行并列点）；Z 映射显示（写字 `Z=pen_down_z` / 裁刀 `Z=cut_touch_z−深度`，未标定禁执行）；`can_start_job`（未归位/未连接禁）。
6. **执行**：`reqRunJob(list(compiled.lines))` → 进度高亮（`_running_compost` 快照映射 `line_range`）+ LiveMarker 十字（与走线脱节 → 按已知硬件问题处置：暂停人查，REPORT §6）→ 暂停/继续/中止（`build_park_sequence` 停靠）/急停（M112 唯一免检）。
7. **后续不做**（§9）：Print&Cut 双点套准、相机叠加、G92/WCS。

---

## 6. Z / 安全映射在新架构中的位置

| 语义 | 映射 | 位置 | 不变量 |
|---|---|---|---|
| 写字落笔 | `G1 Z{pen_down_z}` | `ZMap` → `gcode_for_drawing` | real 模式 `safe_z > down_z ≥ 0` |
| 裁刀下压 | `G1 Z{cut_z_for_depth(cut_touch_z, depth)}` | `ZMap` → `gcode_for_cutting` | `depth ≥ 0`（`job.py:149`）；**新增 `cut_down_z ≥ 0` 断言**（`touch_z < depth` 会产负 Z，与 profile 具体值无关的算术面；编译期拒绝） |
| 空跑 | down_z := safe_z | `compile_job(dry_run=True)` | `down == safe`（emitter 允许相等）；全 Z ≥ 0 |
| 抬笔/段间 | 前者 `G0 Z{safe_z}`（SETUP）；段间 `lift_z = min(safe, down+motion.travel_lift_mm)`（**compile 算**，MotionParams 提供） | `job.py:84,96` 语义保持 | 低抬 golden（`test_gui_job.py:54-72`）不变 |
| 空移 | `G0 X Y`（抬升 z） | TRAVEL；Frame/点检同规则 | 先定位后下压（`cur=None`） |
| 越界预检 | `check_bounds_v2`：**机器坐标**，行程框(0,0,210,210) ∩ 材料可用区(0,0,w,h)−margin，pen_radius 内缩远端 | 编译期（translate 后、emit 前）；`CompiledJob.bounds` 同源供预览 | 只拦截+报告不裁剪；执行按钮绑 `runnable` |
| guard | 逐行 `check()`（`worker.py:116-118`）+ `sequence_ok` 预检（`controller.py:243`；**allow_z 传 UI 真值**，修 `main_window.py:872` 硬编码） | 位置不变 | 不削弱不扩展；M112 免检直发不变 |
| 复位/归位 | 打开串口即复位；软限位静默 ok | `homed` 门禁 + `can_start_job` | `homed` **仅在 `sequenceDone(ok=True)` 的 home 序列后置位**（worker 增补成功标志，§2.2；否则 guard 拦一半也会被当成功——评审 break #6 场景） |
| 反馈可信度 | M114 logical（DRO/LiveMarker）；**Count 永不用** | tooltip 注明逻辑值非物理真值 | ok≠已移动，软件不下「已移动」结论 |

**工件原点 = GUI 侧纯平移，不发 G92**：硬约束「纯平移」+「开串口即复位」使 G92 偏移会随复位隐形丢失 + 纯平移可纯逻辑测试。G92/WCS 列 §9 后续可选。

---

## 7. 迁移步骤（5 个可独立合并的阶段；每阶段内部自洽）

**通用保绿命令**（本轮实测 `129 passed in 10.80s`）：`PYTHONPATH=src python -m pytest -q --basetemp=.pytest_tmp -p no:cacheprovider`。

### 阶段 1 — 纯逻辑地基（零 Qt；GUI/导出链零改动）

- **改动**：新建 `gui/canvas/__init__.py`、`gui/canvas/coords.py`（含 `flip_y_scalar`/`translate_paths` 迁入）、`gui/gcode_parse.py`；改 `gui/job.py`（JobSpec/CompiledJob/ZMap/compile_job/check_bounds_v2/`cur=None`/emitter 允许 down==safe/**`flip_y` 改转发 shim 不删**）、`gui/controller.py`（+Frame/MoveTo/GotoOrigin/can_start_job；`translate_paths` 改 re-export）。
- **保绿**：新增 `tests/test_coords.py`、`tests/test_gcode_parse.py`；扩 `tests/test_gui_job.py`（check_bounds_v2 新签名；回归：首段必含定位 G0（含 p[0]==(0,0)）、裁刀先定位后下压、origin 平移进 lines、Z 全 ≥0、compile→parse 端点恒等（滤零长段）、dry_run 可编译且无 DRAW/PLUNGE）。**现有用例一个不改也全绿**（`flip_y` shim 数学等价；`test_gui_job.py:14` 的顶层 import 不破）。
- **验收**：全量绿；`git diff --stat` 只含上述 5 个 src 文件 + 测试（main_window/export_svg/layout/* 零改动 → **不存在 break #1 的 ImportError 中间态**）。

### 阶段 2 — 画布核心 + 排版编辑链 y-up（**导出链保持现状契约**）

- **改动**：新建 `canvas/{view_transform,paper_scene,paper_view,rulers,items,handles,snap,undo_cmds}.py`；改 `layout/{model,layout_page,doc_import,text_to_svg}.py`（组导入契约、normalize_local、拖动回写、锚点定位、`_svg_to_paths` 名称保留、text_to_svg 迁 `flip_y_scalar`）；删 `layout/{items,canvas,undo}.py`。**`export_svg.py`、`export_requested=Signal(str)`、`_on_export/_on_save_svg` 的字符串管道、main_window 全部不动**（现状 y-up 直写 + `already_paper=True` 直通在 θ 任意时仍正确，链路不断——修评审 break #5）。
- **保绿**：`tests/test_gui_layout.py` 的 **transform golden（:15-37）与 export golden（:40-124）本阶段全部不变**（导出契约未变；`normalize_local` 只在导入/创建入口跑，直接构造 Item 的用例不受影响）；新增/改 `tests/test_gui_layout_window.py`（旋转一致性、拖动回写、两次独立拖拽独立 undo、多选不塌缩、sp_h 生效、标尺 tick=view_from_mm 位置、吸附）；`tests/test_gui_doc_import.py` 增行序方向断言；`test_gui_edge.py:43`/`test_gui_layout_window.py:18` 的 `_svg_to_paths` import 保持绿。
- **验收**：§8.1 翻转 pattern 扫描（剥注释/字符串）**白名单 = `main_window.py` 剩 1 处（阶段 3 删）**；θ=30/90 预览与导出同点；Word/Excel 正立且相对布局保持（组契约断言）。

### 阶段 3 — 导出链统一 + 作业页「预览≡发送」（**同一 PR 闭合，修 break #5**）

- **改动**：`layout/export_svg.py`（y-down 对合 + viewBox 声明 + docstring）；`layout/layout_page.py` 导出侧（`export_requested = Signal(object)` + `to_job_spec`（`flatten_visible` + Placement preserve））；`main_window.py`（JobSpec 内存源、`_recompile()` 全触发集（含工具切换/`_reload_profile_z`/预设）、PaperView+GcodePathItem、LiveMarker、删 `already_paper`/`_draw_preview`/tempfile 往返、删 `job.flip_y` shim）；`presets.py`（`apply_config` 尾部改 `_on_preset_applied()`）。**生产端与消费端同一 PR**，无中间断链。
- **保绿（评审 break #3：明列必改 golden）**：`tests/test_gui_layout.py` 导出侧三个用例按 y-down 值更新——`test_document_export_parseable`（:40-57）改为 `paper_from_svg_ydown(parse(...)) ≈ 原纸面值`（或字面 (10,170)/(30,170)）；`test_export_skips_hidden_and_respects_z_order`（:103-111）字面 `"0,1"`→`"0,209"`、`"0,0"`→`"0,210"`、`"0,5" not in`→`"0,205" not in`（或改经 helpers 比对）；`test_export_closes_contour`（:118-124）`svg.count("0,0")>=2` → `count("0,210")>=2`。新增 `tests/test_gui_window.py`：①设/清原点后 lines 含新偏移且预览段同步平移；②改去重/笔径/速度不触发文件读取；③排版导出→作业后源仍在、改参数不炸；④`parse_lines(_job_lines)` DRAW 段 ≡ 预览路径；⑤越界禁执行；⑥工具切换保留几何且换 ZMap；⑦运行中 `_recompile` no-op、jobDone 后补编译。
- **验收**：`grep -rn "already_paper\|flip_y" src`（剥注释/字符串）零命中；roundtrip golden 绿。

### 阶段 4 — 对准工具 + homed 门禁（含 worker 最小增补）

- **改动**：`worker.py`（`sequenceDone = Signal(bool)`）；`main_window.py`（对准工具条、`homed` 置位/清除规则、`can_start_job` 接线、`_send` 预检 `allow_z` 传 UI 真值、未勾允许 Z 禁用一键寻零）。
- **保绿**：`tests/test_gui_controller.py` 增 Frame/MoveTo/GotoOrigin golden（`sequence_ok(...)==[]`）；`tests/test_gui_worker.py` 增 sequenceDone 成功/中断双分支（MarlinSim 或 fake transport）；window 测试：未归位拒绝 执行/Frame/设原点；guard 拦截一半时 homed 不置位（用 `G28 Z` 未 allow_z 场景复刻评审 break #6 的假阳性）。
- **验收**：真机清单（不计 pytest）：Frame 与预览 bbox 目视一致；设原点后落笔对准 <1mm（人眼）；空跑全程不触纸。

### 阶段 5 — 退役、常量收敛、文档（可选性能）

- **改动**：删 `emit_gcode`/`svg_file_to_gcode`（`tests/test_toolchain.py:72,89` 的 `test_emit_allowed_codes_and_guard`/`test_emit_pen_cycle` 随退役改写或删）；`preview/to_svg.py` 改 `preview_svg_from_segments`（`test_preview_writes_svg` 改喂 segments）；210 常量收敛（§8.4）；旧名文案改写清单（`job.py:3` 提 emit_gcode、`layout_page.py:11` 提 disp_y 等 docstring 一并改）；更新 AGENTS/REPORT/PRD；可选 TiledPathItem（段数 >2000 实测需要才做）。
- **保绿/验收**：全量绿；§8.1/§8.4 扫描零命中（剥注释/字符串后）。

---

## 8. 测试策略

### 8.1 坐标/翻转单测 + 静态扫描（零 Qt）

- `test_paper_svg_ydown_involution`、`test_machine_is_paper_identity`、`test_place_at_anchor`、`test_grid_steps_heckbert`、`test_translate_paths_pure_shift`。
- `test_flip_math_single_source`（**评审 breaks #2/missing #5/#7 的可执行形态**）：扫描器用 `tokenize` **剥离注释与字符串**后，对禁止 pattern `d(y'|_y)\s*=\s*(BED_H|bed_h|span)\s*-|BED_H\s*-\s*(py|y|ny|mmv|v\.y\(\))|(210\s*-\s*y)|ysum\s*-\s*y|\(x,\s*-y\)|\(1\.0\s*-\s*y\)` 断言命中 ⊆ 分阶段白名单：阶段 1–2 = {`main_window.py` 1 处}；阶段 3 起 = {}。**注意 pattern 不得匹配 `doc_import.py:53` 的 `BED_H - 2 * margin`（尺寸算术，非翻转）** —— 上一版误伤即因 pattern 过宽。

### 8.2 SVG→G-code→解析回读一致性（golden，纯逻辑）

- `test_compile_parse_roundtrip`（滤零长段后端点序列 ≡ 平移后输入）、`test_compile_golden_text`、`test_first_segment_positioning_g0`、`test_knife_plunge_after_positioning`、`test_work_origin_translation_appears_in_lines`、`test_z_never_negative`（**合成参数**如 `cut_touch_z=2.0, depth=5.0` 必 ValueError；不依赖 profile 当前值）、`test_dry_run_compiles_without_draw`（break #7 回归）、`test_guard_passes_compiled_job`、`test_export_svg_ydown_roundtrip`（阶段 3 起）。
- 排序语义（missing #4 回归）：`test_opt_sort_flag_respected`（sort=False 保持文件序）。

### 8.3 offscreen Qt 测（每文件独占 QApplication，沿 `test_gui_window.py:1-15`）

- 旋转一致性 `test_pathitem_matches_model_transform`（θ=30/90、scale≠1，`gi.mapToScene ≡ model.transformed_paths`）；拖动回写 + 手势 token undo；数值定位/多选/高；**标尺**（break #10）：`test_ruler_ticks_at_paper_positions` 断言 `RulerWidget` 计算的 label mmv tick y == `view_from_mm(view, QPointF(0,mmv)).y()`、0 刻度对齐 paper y=0（**只断言修复后位置**）；阶段 3 的七条作业页用例。
- 断言纪律：像素断言 `pix.width()/pix.devicePixelRatioF()` 归一；`QTest` 坐标容差 ≥1px；缩放走显式 `set_zoom`；不断言文字像素宽度。

### 8.4 常量收敛扫描（阶段 5）

- 剥注释/字符串后 `\b210(\.0)?\b` 仅允许 `canvas/coords.py` 的 `BED_W/BED_H` 定义处（阶段 1–4 白名单：`job.py` 默认参、`preview/to_svg.py:19`、`main_window` 材料表、`presets.py:47-48`，逐项列明）。

---

## 9. 明确不做与风险

### 明确不做（本期）

1. **相机叠加 / Print&Cut 双点套准**（列后续；对准强制项已全覆盖）。
2. **G92 / G54..G59**：与「开串口即复位」冲突且偏移必须显式并入预览（UGS 教训）；本期编译期纯平移。
3. **越界自动裁剪**：只拦截+报告。
4. **SVG 单位换算（px/in、viewBox 缩放）**：`parse_svg` 现状保持；`parse_svg_meta` 仅供 Placement 判定。
5. **橡皮筋/凸包 Frame、Frame Continuously、G2/G3**。
6. **Word/Excel 样式/重排保真**（只修 y 方向与组布局）。
7. **性能基准承诺 / TiledPathItem 必做化**（接口预留，实测需要才做）。
8. **toolchain Z0/Z1 语义复活**。

### 风险与对策

| 风险 | 对策 |
|---|---|
| **profile 是用户在改的活数据**（本会话实证：早前一轮 `pen_down_z: 3.2`，修订轮重读为 `"17.0"`（:6）、`cut_touch_z: "17.5"`（:13），与 notes 一致；`git status` 一直 `M profiles/...`） | 设计不依赖任何具体标定值：Z≥0 与 `safe>down` 编译期断言（负 Z 的算术面：`touch_z < depth`）+ `preset_z_problems`（<5 拦自动加载）+ Z 映射显示行明示将发绝对 Z。标定值本身由用户确认，软件不猜（原「3.2 vs 17.0 冲突」条已按当前树撤下，见 §10-8） |
| Qt 负行列式变换 + AnchorUnderMouse 锚点漂移 | 缩放一律显式 `set_zoom` 公式 |
| 旋转观感翻转（+θ 与旧画布相反） | 面板标注「逆时针为正」；阶段 2 人工目检 |
| 拖动回写/undo token 回归 | 阶段 2 专测；`MoveItemsCommand.redo` 幂等 |
| 已知硬件问题（ok 不走/失步，REPORT §6） | 软件无法构造性解决：LiveMarker 对照 + 空跑 + 人盯机器；Count 永不作证据 |
| worker `sequenceDone` 信号签名变更（bool） | 阶段 4 单一改动点（`worker.py:50,208-230`）；`tests/test_gui_worker.py` 补双分支；不触 guard/estop |
| 129 基线外用例的隐性依赖（如 `_svg_to_paths` 名、presets 属性名） | §2.2 显式保留这些名字；每阶段「改动文件」即测试改动清单；golden 变更新旧值对照 |

---

## 10. 对独立评审意见的处理

> 逐条处理；「接受」=已改进正文（括号注章节）；「部分接受/维持」= 在此与正文给出理由与证据。本轮新做的核查命令见文首「本轮核查」。

### breaks

1. **阶段 1 删 `flip_y` 与 `main_window.py:1196-1198` 运行时 import 矛盾** —— **接受**。阶段 1 把 `flip_y` 改为**转发 shim**（同名同行为，调 `coords.paper_to_svg_ydown`）不删，`main_window` 现有 `from megapro.gui.job import flip_y` 继续可跑；阶段 3（main_window 不再调用后）才删。退役表已改写为「阶段 1 shim / 阶段 3 删」（§2.2、§7 阶段 1）。**不再存在 ImportError 中间态**，也不再有「停止被调用」与改动文件清单的时点矛盾。
2. **阶段 2 grep 验收字面不可能（`main_window.py:1304`、`doc_import.py:53`）** —— **接受**。三处修正：①pattern 收窄为「翻转写法」（`BED_H - py|bed_h - v.y()|(210 - y)|ysum - y|(x, -y)|(1.0 - y)` 等），**明确不匹配 `doc_import.py:53` 的 `BED_H - 2 * margin`**（尺寸算术，本蓝图也要求 `_fit_to_bed` 保留它）；②验收改为**分阶段白名单**：阶段 1–2 允许 `main_window.py` 剩余 1 处，阶段 3 起零命中（§8.1）；③扫描器剥离注释/字符串（兼修 missing #7）。
3. **`test_gui_layout.py:103-111/118-124` 的字面坐标 golden 必破** —— **接受**（上一版「:103-111 不动」确与「导出改 y-down」自相矛盾）。阶段 3 明列三个必改用例与新旧字面值：`"0,1"→"0,209"`、`"0,0"→"0,210"`、`"0,5"→"0,205"`、`count("0,0")`→`count("0,210")`、`test_document_export_parseable` 改经 `paper_from_svg_ydown` 比对（§7 阶段 3）。
4. **逐项 `place_at_anchor` 摧毁 Word/Excel 相对布局** —— **接受**。给出**组导入契约**：`_import_group` 整组翻转 + **组级一次** place_at_anchor + 逐 Item `normalize_local`（平移由 pos 补偿、页面几何不变）；明确「严禁逐 Item 归位」；单图元是特例（§2.2 layout_page，§2.3 #7）。
5. **阶段 2/3 之间导出链断裂** —— **接受**。重排阶段边界：阶段 2 **完全不动导出链**（`export_svg.py`、`Signal(str)`、tempfile 管道原样，链路按现状保持正确）；导出改 y-down、`Signal(JobSpec)`、消费端删 `already_paper` 全部放阶段 3 **同一 PR** 闭合（§7）。同时阶段 2 的 export golden 不再声称「改 y-down」——那是阶段 3 的事（该矛盾已消）。
6. **`homed` 与「worker 不改」矛盾；guard 拦一半的假阳性** —— **接受**。worker 允许**最小增补**：`sequenceDone = Signal(bool)`（成功标志，只动 `worker.py:50,208-230`，guard/estop 语义零改动）；`homed` 仅在 `sequenceDone(ok=True)` 的 home 序列后置位；同时修正 `main_window.py:872` `sequence_ok(..., allow_z=True)` 硬编码为 UI 真值、未勾『允许 Z』禁用「一键寻零」（内含 `G28 Z`，`guard.py:114-119` 必拦）；阶段 4 加「guard 拦一半 → homed 不置位」的回归（§2.2 worker/main_window，§5，§6，§7 阶段 4）。
7. **空跑 `down_z := safe_z` 撞 `safe_z > down_z` 严格断言** —— **接受**。emitter 校验放宽为「`safe_z < down_z` 才 raise（允许相等）」；`compile_job` real 模式仍严格 `safe > down ≥ 0`，`dry_run=True` 断言 `down == safe`（§2.2 job，§3.2，§6）；既有 `test_drawing_invalid_z_order`（30/17）行为不变。
8. **profile 3.2/17.0 冲突与当前树不符** —— **接受（按当前树更正）**。本轮重读：`profiles/mega-pro-marlin.yaml:6` = `pen_down_z: "17.0"`、`:13` = `cut_touch_z: "17.5"`，无 3.2。补注事实：本会话早前一轮读到的是 3.2（该文件被用户实时编辑，git 状态 `M`），故设计不依赖具体值；`test_z_never_negative` 改用合成参数（`cut_touch_z=2.0, depth=5.0`）；风险条重写为「活数据+断言兜底」（§9 首行风险，§8.2）。
9. **`_refresh_props` 修复指向错误（本就读 model；`scenePos()` 是 QGraphicsItem API）** —— **接受**。本轮 `sed` 复核 `layout_page.py:265-275` 证实 `:270-271` 读 `it.pos`。正文更正：`_refresh_props` 不改；病灶是**拖动不回写 model**（`sync_to_model` 仅 `:304-308/:714/:730` 调用）；修复 = 拖动 mouseRelease 构造 `MoveItemsCommand`（命令写 model）；删除上一版「经 scenePos() 读取」的错误指引（§2.2 layout_page，§2.3 #8）。
10. **竖标尺应定性为镜像（label mmv 落 paper y=210−mmv；y=50 读 160）而非恒偏** —— **维持原判，评审此项为误解（按要求在正文说明理由）**。按 `canvas.py:236-242` 逐式复算并经本轮 offscreen 探针实测（文首核查 4）：`origin = mapFromScene(0,0).y()` 是 paper y=**210** 的视口位置，`y = origin − L*ppm` 反解 paper 得 **paper = 210 + L**（探针输出：label 0→paper 210、label 50→paper 260、label 210→paper 420；paper y=50 处读数 **−160** 而非 160；paper y=0 正确刻度在 view y=449，代码画在 29）。若真是镜像（label L 落 paper 210−L），0..210 各刻度会**铺满整个床区**、只是读数反向 —— 与审计原句「**刻度整体画在床框上方、床区左侧无任何可用读数**」直接矛盾；恒偏模型（刻度全部脱床、床区无读数）才与该观察一致。两种错误形态的**修复与测试完全相同**：`RulerWidget` 用 `view_from_mm` 定位、0 刻度对齐 paper y=0，回归测试只断言修复后正确位置、不锚定错误形态（§2.2 rulers 已写入推导+探针结论；§8.3）。

### missing

1. **`presets.py` 不在任何阶段清单** —— **接受**。新增 `presets.py` 改造契约：`mw._work_origin`/`_feed_*`/`_pen_diameter_mm`/`_opt_*` 等**属性名全部保留**（`presets.py:71-73/99-116` 与 `tests/test_gui_presets.py:64` 假 mw 兼容），MainWindow 字段= JobSpec 槽位投影；`apply_config` 尾部的 `mw._on_clear_job()`（`:133`）改 `mw._on_preset_applied()`（同步 spec + `_recompile()`）；列入阶段 3 改动文件与 `_recompile` 触发集（§2.2 presets/main_window，§7 阶段 3）。
2. **`_recompile` 触发集不全** —— **接受**。补入：**工具切换**（`_on_tool_changed` `:1099-1104` 改为换 ZMap+emitter 后重编译、保留几何）、**Z 标定回写**（`_reload_profile_z` `:974-978` 后）、**预设加载/应用**（§2.2 main_window 触发集，§7 阶段 3 用例 ⑥）。
3. **执行期 `_recompile` 互斥未定义** —— **接受**。定义：`_job_running` 期间 `_recompile` no-op+提示，`_on_job_done` 后补一次；进度高亮用执行开始时的不可变快照 `_running_compiled`（与 worker 收到的 `list(lines)` 拷贝同源）（§2.2 main_window，§7 用例 ⑦）。
4. **`polylines_for_svg` 无条件 NN 排序 vs `OptParams.sort`** —— **接受**。JobSpec 构建一律 `parse_svg` 原序（不经 `polylines_for_svg`）；`polylines_for_svg(path, *, sort=True)` 加参保兼容；NN 只在 compile 内按 `opt.sort` 执行（§2.2 job/opt，§8.2 `test_opt_sort_flag_respected`）。
5. **`text_to_svg.py:253` 翻转点无裁决** —— **接受**。裁决：它是「本地 y-up 归一化数据 → SVG y-down 交换格式」的**格式编码**，属 SVG 互换层边界 → 迁调 `coords.flip_y_scalar(y, 1.0)`（数学等价，`test_gui_text.py` golden 不变），翻转实现全仓库只剩 `flip_y_scalar` 一处；`(1.0 - y)` 写入禁用 pattern（§2.2 text_to_svg/coords，§8.1）。
6. **`_svg_to_paths` 私有助手被测试/生产依赖** —— **接受**。阶段 2 显式**保留 `layout_page._svg_to_paths` 名称**（依赖方：`doc_import.py:16,39`、`tests/test_gui_edge.py:43`、`tests/test_gui_layout_window.py:18,24` —— 本轮 grep 实证），并写进保绿清单（§2.2 layout_page，§7 阶段 2）。
7. **grep 验收命中注释/docstring** —— **接受**。扫描器用 `tokenize` 剥离 STRING/COMMENT 再匹配；另列旧名文案改写清单（`job.py:3`、`layout_page.py:11` 等）在阶段 5 处理，双保险（§8.1，§7 阶段 5）。

### unclear

1. **`parse_lines` 分类的 Z 参照/工具来源** —— **接受**。签名显式化：`parse_lines(lines, *, z_down, z_safe, tool)`；DRAW 判据 = G1 含 X/Y 且 `|z−z_down|≤1e-6`；配色由 `tool`；参数来自 `ZMap`+`JobSpec.tool`（编译器传入）（§2.2 gcode_parse，§3.1）。
2. **`translate_paths` 归属矛盾** —— **接受**。裁决：**迁入 `coords.py`**（坐标数学唯一权威）；`controller.translate_paths` 保留为 re-export shim（阶段 5 删），`main_window.py:1202` 现有 import 不破（§2.1 表、§2.2 controller）。
3. **`check_bounds_v2` 参照系 / A4 交集 / `strict=False` 返回类型** —— **接受**。判定全在**机器坐标**；材料框锚定**机器 (0,0 铺纸角**（与 work_origin 无关，§5）；判定区 = 行程 ∩（材料−margin）（A4 → (0,0,210,210−margin)，显式等价现 `min(material,210)`）；`strict=False` 返回**同一 `CompiledJob`**（report 在 `.bounds`，执行按钮绑 `.runnable`）（§2.2 job，§6）。
4. **`flatten` 无出处 / 排版直传的 Placement** —— **接受**。定义 `model.flatten_visible(doc)`（z 升序、`transformed_paths` 拍平、跳 <2 点）；排版直传 `Placement(mode='preserve')`（版面坐标即工件坐标，不做 anchor 归位；anchor 规则只对文件载入）（§2.2 model/layout_page）。
5. **SelectionHandles 缩放语义** —— **接受**。手柄**只支持等比**（`model.scale` 是标量；自由拉伸按 min 比例等比化）；支点=本地原点；多选等比公式 `scale_i' = k·scale_i`、`pos_i' = A + k·(pos_i − A)`（§2.2 handles）。
6. **`Segment` 形状 / `line_range` 基准** —— **接受**。`@dataclass(frozen=True)` 具名字段；`line_range` **0 基半开 `[i0,i1)`**；`set_progress(done)` 的 done = worker 1 基已发送行数（`worker.py:255`），段全亮 iff `line_range[1] <= done`（天然对齐）（§2.2 gcode_parse/gcode_items）。
7. **「禁止第三处 210」范围** —— **接受（澄清）**。原则专指床尺寸/翻转常量：数值字面只在 `coords.py` 的 `BED_W/BED_H`；其余引用常量，阶段 5 收敛（§8.4 独立扫描；§2.1 末段）。
8. **段间低抬归属（ZMap vs MotionParams）** —— **接受**。`lift_z` 由 **compile 流水线**按 `min(safe, down+motion.travel_lift_mm)` 计算；`ZMap` 只管 safe/down 两档（§2.1 表已改）。
9. **PLUNGE/RETRACT 判据 / 首段 `G0 Z{safe}` / 零长 G1** —— **接受**。首段 `G0 Z{z_safe}` 归 **SETUP**；PLUNGE=纯 Z G1 下行到 down；RETRACT=段间/收尾纯 Z 上行 G0；零长 DRAW（p0==p1）解析保留、渲染跳过、roundtrip 比对过滤（§3.1）。

---

### 附：本轮未做/未验证

- 未修改任何仓库文件；pytest 全量仅跑过基线（本轮 `129 passed in 10.80s`，命令文首列出），未跑任何「按阶段」的新测试（尚未实现）。
- 竖标尺结论来自代码复算 + offscreen 探针（文首核查 4 给出输出），未在真机 GUI 目验；若后续实测与「paper = 210 + L」不符，按 §8.3 的「只断言修复后位置」策略仍可无损落地。
- 未跑真机；性能数字未实测；成熟软件模式/Qt 调研材料的「未证实/未核实项」按原材料声明照录，未作为设计依赖。
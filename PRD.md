# PRD：Mega Pro 写字 / 裁纸上位机（Host App）

- 日期：2026-09-07　·　版本：v0.2（定稿，待终审）
- 目标设备：Anycubic Mega Pro（Trigorilla / ATmega2560），原厂 Marlin 1.1.0-RC8（V1.2.9）USB 串口（250000 baud），不改固件、不改硬件
- 现状底座：`src/megapro` CLI + SVG→G-code 工具链 + 工具路径 SVG 预览 + 33 项测试全绿（Python 3.14 + pyserial 3.5；PySide6 6.11.2 已装）

---

## 1. 背景与目标

已有 CLI 可以 probe / jog / home / stream / move / estop / console，但每次调用都重开串口并触发主板复位（DTR 边沿），多步流程只能靠人工在 `console` 里敲。目标是做一个**桌面上位机**：

1. **简单轴控制**（**本期 P0**）：图形化连接管理、实时坐标（DRO）、手动 Jog（X/Y/Z）、归位、笔起落、急停，替代手敲 G-code。
2. **写字 / 裁纸文件工作流**（**P1，后续**）：加载 SVG/G-code → 预览 → 越界预检 → 发送执行 → 进度/暂停/中止，落笔/下压高度与安全点从 profile 映射。
3. **标定与可信执行**（**P2，后续**）：落笔高度向导、安全点设定、（可选）相机验证——直面本机"有应答无动作"与坐标可信度问题。

**第一约束**：一个 GUI 会话 = **一条常驻串口连接**（打开即复位，复位即需重新归位）。这决定连接管理、状态机与一切动轴操作的设计。

---

## 2. 机器事实与已知问题（实测，设计必须遵守）

- 打开串口 = 主板复位（坐标清零、软限位静默钳制返回 `ok` 不动）；冷启动冲刷 ~16s；`G28` 可达 60s、单次移动 30s（超时预算不可缩短）。
- 无 LCD 应急解析器（`EMERGENCY_PARSER` 出厂关闭）→ `M0/M1/M410` 无效；**`M112` 在排队到队首后执行且需断电重启恢复**；SD 卡坏、仅 USB。
- **`ok` ≠ 已移动**：固件 `ok` 只表示已接收排队；物理到位需 `M400` 或经验超时。存在**偶发"有应答无动作 / 坐标失步"**（09-06 复现于回程绝对移动，09-07 未复现）。
- **M114 的 `Count` 字段不可靠**（瞬时错位后自愈）；判据优先级：**人眼/相机 > 逻辑坐标 X/Y/Z > Count**。
- 安全门（`safety/guard.py`）：加热/主轴/激光硬禁、行 ≤96 字符、无 N 行号、Z 运动需 `allow_z`、负 Z / 落笔需 profile 有 `pen_down_z`、XY 平移自动先抬 +5mm（`--draw` 除外）、`home` 先抬 +10mm、Z 永不 < 0。
- 行程 210×210×205mm；触纸实测绝对 Z=17（`pen_down_z: 17.0`，安全点 X0 Y50 Z30，2026-09-07 两轮实测）；笔为**摩擦固定（无弹簧）**。
- 工具链现产 Z0/Z1（0=落笔/1=抬笔）**未映射 profile 标定高度**——GUI 的"画/裁文件"必须自行完成 profile→Z 映射。

---

## 3. 用户与场景

| 用户 | 场景 |
|---|---|
| 操作者（主要） | 铺纸/装笔/换刀 → 连机器 → 归位 → Jog 找位置/对刀 → 落笔写字或裁纸 → 看进度、随时急停 |
| 调试者（维护） | 诊断"有应答无动作"、查坐标与 Count、读日志、重标 `pen_down_z` |
| 未来：批量化 | 同一 SVG 换纸重复跑（需手动换纸；机器无自动换纸） |

交互原则：**任何动轴操作都要先确认机器处于可动状态**；归位/执行中锁定 Jog 面板；急停永远可达；中止/急停后状态标记为"未知，需人工/相机确认"。

---

## 4. 范围

### 4.1 本期必做（P0 —— "简单轴控制上位机"）
| ID | 需求 | 验收要点 |
|---|---|---|
| F1 | 连接管理：端口列表（VID/PID/描述）、记住上次端口、波特率 250000→115200 自动扫描、连接 = 启动引导（排空→就绪）、断开 | 连接走 `open_link` 全序列（dtr 低→2.5s→清缓冲→settle）；断开即整机会话结束并明确提示"坐标已丢、重连需归位" |
| F2 | DRO 轮询 + 状态栏 | 连接 + Ready 时 QTimer 轮询 M114（空闲 1Hz、Jog 2–5Hz），解析 X/Y/Z 更新标签；Busy 时不打断喂行；显示 Busy/Idle 与"最后 ok"时间 |
| F3 | Jog 面板 | 步长 0.1/1/10/自定义；键盘方向键（Shift=大步）；Z 需显式"允许 Z"开关（对应 `--allow-z`）；XY 平移自动抬 +5mm；落笔模式走 `--draw` 语义 |
| F4 | 归位 | Home All = 抬 Z10 → G28 XY → G28 Z；单轴可；运行中面板锁定；不因单个 `ok` 就宣告"已归位"（需 M114 + 完成信号） |
| F5 | E-stop + 常规中止 | E-stop：常驻按钮 + 快捷键，裸发 M112 不等 ok，按下后状态=急停、提示断电重启；常规中止（如 Jog 连续走停）：停止喂行 → 当前段走完 → M400 → 抬 Z → 回安全点 → 状态"未验证" |
| F6 | 笔控制 | PenUp = 绝对 Z `safe_z`、PenDown = 绝对 Z `pen_down_z`（慢速 F300）；笔态 LED（抬/落） |
| F7 | 回显/状态解析 | 解析 `ok/echo:/Error/!!/M114`；温度等闲杂折叠；行级展示（原文） |
| F8 | 命令输入行（可选） | 类似 console 的单行输入，过 guard，供调试 |
| F12 | 会话日志 | 全量 TX/RX 时间戳日志入 `logs/`（复用 RawLog 格式），可打开目录 |

安全底线：E-stop 是唯一免检裸发；所有其他行仍过 `guard.check()`；任何"动作完成"结论需 M114 + 人工复核（Count 不作判据）。

### 4.2 后续里程碑（本期只做架构预留，不实现）
- **P1 写字/裁纸文件工作流**（F9–F11）：SVG/G-code 载入（复用 toolchain）→ 路径预览（复用 preview/to_svg，QGraphicsView + QSvgRenderer）→ **profile→Z 映射** → 行程/纸界越界预检 → 发送 + 进度 + 暂停（= 停喂行至 M400）/中止 → **裁纸模式**：Z 下压深度、闭合轮廓整圈切、尖角过切/回环（拖刀）、**切可达区域 + A4 超程警示**（见 §6）。不做刀偏置补偿与旋转轴。
- **P2 标定与可信**：落笔高度向导（慢速下探→"触纸"→回写 `pen_down_z`）；安全点设定；相机抓帧前后对比确认位移（复用 `scripts/cam_capture.py`）。

### 4.3 明确不做（本期）
- 不改固件（不开 EMERGENCY_PARSER、不加 HOST_KEEPALIVE）；不做切向刀/刀偏置数学补偿；不做 M0/M1/M25 暂停（固件不支持）；不做整张 A4 分区域切/挪纸（后续版本）；不做 Web/多客户端；不依赖 numpy/pyqtgraph（预览用 SVG，零新依赖）。

---

## 5. 架构与关键技术决策

### 5.1 分层
```
src/megapro/gui/                      ← 本期新增（P0）
  __main__.py      python -m megapro.gui 启动
  app.py           QApplication 装配
  main_window.py   Qt 层：连接条 | DRO+状态栏 | Jog+笔控 | 回显台 | E-stop
  controller.py    纯逻辑：状态机/gating/坐标解析/命令构造（无 Qt 依赖，可单测）
  worker.py        常驻 QThread 串口 worker（moveToThread，唯一串口消费者）

src/megapro/ 核心（不动，保持纯逻辑/零 GUI 依赖）：
  transport · safety/guard · dialect · toolchain · preview · profiles
```

### 5.2 关键决策（源自研究：UGS / Pronterface / LaserGRBL / OctoPrint / InkCut 对照 + Marlin 1.1.0-RC8 行为核验）
1. **常驻单会话 worker**（借鉴 Printrun printcore 分层 / OctoPrint 连接管理器）：串口对象存活于 worker、生命周期 = 应用内一次 open 对应一次"归位世界"，杜绝 CLI 式每次重开复位。
2. **GUI 线程绝不碰 pyserial**：唯一串口消费者 = worker（`moveToThread`，不继承 QThread.run）；所有阻塞读在 worker 内带超时；GUI 只经 signal/slot 收发。
3. **DRO 是响应式只读**：QTimer 只发 M114 请求，由 signal 回推更新；轮询仅发生在 Ready/Idle；Busy 不打断喂行。
4. **"运动完成"判定**：单段 30s 超时预算；需物理真值的节点（触纸、抬笔后、相机对比前）先 `M400` 再短等——Marlin `ok` 是流控不是完成信号（1.1.x BUFSIZE=4）。
5. **暂停 = 停喂行 → M400 → 抬 Z 停靠**；急停 = M112（语义 = 断电重启）。两者严格区分，UI 明示"停不是瞬时的"。
6. **预览零依赖**：SVG 用 `QSvgRenderer` + `QGraphicsView`；不引 pyqtgraph/numpy（除非 P2 实时示波才考虑）。
7. **profile 读写保持 flat-YAML**、写前备份；现有解析器不支持嵌套，勿引入嵌套结构。

### 5.3 状态机与可测性
- 状态机：`Disconnected → Connecting(启动引导) → Ready → Busy(执行中) → Fault/Estop`；输入按状态 gating（Busy/归位中禁 Jog、禁改参数、E-stop 常活）。
- 可测性：沿用 `tests/fake_serial.py` FakeSerial 与 `tests/test_integration.py` MarlinSim(TCP) 模式；controller 层抽成纯逻辑、无 Qt 依赖，可单测；Qt 层只做薄绑定。不接真机即可跑绝大部分测试。
- 新测试：`tests/test_gui_controller.py` 等（controller 层用 FakeSerial/MarlinSim）。
- 基线不动：`python -m pytest -q` 33/33 持续全绿。

---

## 6. 材料 / 纸张预设与裁纸边界模型（P1 设计，本期仅预留）

> 决定：默认 A4 + 预设 + 自定义；A4(210×297) 的 297 超出 210×210 行程 → **切可达区域 + 超程警示**。

- **机器硬上界**：刀头行程 210×210×205（profile `work_area`），任何裁切/绘图路径先过越界预检（P1 实现；本期架构留接口）。
- **材料/纸张预设表**：默认 A4(210×297)；另含 A5(148×210)、A6(105×148)、自定义宽高；厚度档预设（薄/中/厚 + 自定义）；安全边距可设（默认建议 5mm）。
- **边界模型**：裁切包围盒 = `min(纸张尺寸, 210×210)` − 安全边距；预设中 A4 标"超出行程（Y 297 > 210）"警示；只切当前装纸可覆盖的区域。
- **不做（标注后续）**：整张 A4 分区域切 / 挪纸 + 手动原点定位（后续版本）；刀偏置补偿与旋转轴（明确不做）。

---

## 7. 里程碑与验收

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M0-GUI（本期 P0）** | F1–F8 + F12（连接/DRO/Jog/Home/笔控/E-stop/回显/日志） | 不接真机：controller 单测（FakeSerial/MarlinSim）绿；接真机：连 COM7 → 归位 → Jog XYZ 每一步人眼确认动作；E-stop 按下生效并提示断电重启；全程日志落盘 |
| M1-GUI（后续 P1） | F9–F11 + §6 裁纸模型（写字/裁纸执行、边界、A4 警示） | 真机 20mm 方块空跑 → 写 → 裁；越界文件被预检拦截；暂停 ≤1 段后停住；中止自动抬 Z 回安全点 |
| M2-GUI（后续 P2） | 标定向导 + 相机验证 | `pen_down_z`/安全点由向导回写 profile；相机帧对比能辅助确认位移 |

---

## 8. 非功能需求

- **平台**：先 Windows（win32 / COM / CP210x）；README 注明"当前仅 Windows"。不引入跨平台抽象，跨平台延后。
- **UI 语言**：中文界面（按钮/状态/对话框/警示）；串口回显、G-code、日志原文不变。
- **依赖**：Python 3.14 + PySide6 6.11.2 + pyserial 3.5（仅串口）；不引 qasync（不支持 Py3.14）、不重写 QtSerialPort（现有 pyserial 层已含 DTR/settle/estop 行为）。
- **并发**：见 §5.2（worker 唯一消费者、QTimer 轮询、signal/slot）。
- **健壮性**：端口被占用 → 明确报错不静默重试；链路丢失 → 状态降级提示不挂死；profile 写前备份。
- **安全**：不削弱现有 guard；E-stop 唯一免检裸发；动作完成需 M114 + 人工/相机复核。

---

## 9. 已确认决策（原开放问题）

| # | 问题 | 决策 |
|---|---|---|
| 1 | 本期范围 | **P0 先行**；P1/P2 架构预留、P0 验收后单独排期 |
| 2 | 代码落位 | **`src/megapro/gui/`** 子包（依赖 `src/megapro`；入口 `python -m megapro.gui`） |
| 3 | UI 语言 | **中文**（回显/G-code/日志仍原文） |
| 4 | 平台 | **先 Windows 跑通**，跨平台延后 |
| 5 | 裁纸材料/厚度 | **默认 A4 + 预设 + 自定义**；A4 超程 → 切可达区域 + 警示（整张分区域切为后续） |

---

## 10. 风险

- R1（高）偶发"有应答无动作/坐标失步"：软件无法根治 → 所有"动作完成"依赖人工/相机确认；长时间无人值守执行默认禁止。
- R2（中）M112 需断电重启、无即时暂停 → UI 措辞诚实（"急停后须断电"、"暂停有 ≤1 段延迟"）。
- R3（中）Z0/Z1 工具链输出 ≠ 标定高度 → P1 必须做 profile→Z 映射，未映射前禁止直发。
- R4（低）PySide6 6.11.2 + Python 3.14 组合成熟度 → P0 先做最小窗口 smoke 验证再铺开。
- R5（信息）A4 超行程 → 见 §6 切可达区域 + 警示；整张分区域切不排期。

---

## 11. 研究出处

- 参考上位机：UGS (github.com/winder/Universal-G-Code-Sender)、LaserGRBL (github.com/arkypita/LaserGRBL)、Printrun/Pronterface (github.com/kliment/Printrun)、OctoPrint (github.com/OctoPrint/OctoPrint)、bCNC (github.com/vlachoudis/bCNC)、InkCut (github.com/inkcut/inkcut)。
- Marlin 1.1.0-RC8 / 1.1.9 `Configuration_adv.h`（EMERGENCY_PARSER 注释状态、HOST_KEEPALIVE、BUFSIZE）与 Marlin 文档（M114/M400/M112/M0-M1/M105）。
- PySide6 线程：python-guis.com multithreading、Qt thread-basics 文档；qasync 不支持 Python 3.14。
- 本机证据：`REPORT.md`、`logs/probe-COM7_250000.txt`、`logs/pen_trial_2026*.md`。

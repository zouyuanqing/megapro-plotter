# Mega Pro Pen Plotter / Paper Cutter (M0)

Goal: drive an Anycubic Mega Pro as a pen plotter / paper cutter over its
stock Marlin USB serial — no firmware flash, no board mods. The laser stays
sealed / disconnected; all work is Z-lift pen or drag-knife moves only.

> Status (2026-09-07): M0 CLI + M1a toolchain built, reviewed, **33/33 pytest
> green**. Live-verified on COM7 @ 250000 against stock Marlin 1.1.0-RC8
> (V1.2.9): probe/home/jog/stream all acked. **Full pen sequence ran
> successfully 2026-09-07** (home → safe point → touch paper at Z17 → draw
> X20 line → lift → return). Known open issue: intermittent
> ok-without-motion / coordinate desync — reproduced 09-06 on a return move,
> not on 09-07; see `REPORT.md` §6 + §6.1. Full handoff: `REPORT.md`.

## Install

```sh
pip install -r requirements-m0.txt
```

Needs nothing beyond the installed `pyserial 3.5` (see
`requirements-m0.txt`, pinned `pyserial==3.5`). No compiler, no PlatformIO,
no firmware toolchain for M0.

## Find the printer port

`--list-ports` is best-effort only: Windows USB-serial enumeration is quirky
and the Mega2560-class board most often shows up as COM6, but the number can
shift after reboot / hub / cable changes.

1. Run the scan:
   ```sh
   python -m megapro --list-ports
   ```
2. Cross-check in Windows Device Manager → Ports (COM & LPT) → look for the
   Mega2560 / USB-Serial entry and note its COM number (most likely COM6).
3. Because of the enumeration quirk, every motion/probe command requires an
   explicit `--port`:

   ```sh
   python -m megapro --port COM6 probe
   ```

   Never rely on a remembered default — always pass `--port` (replace COM6
   with your actual port).

## Usage (intended M0 commands, COM6 placeholder)

```sh
python -m megapro --port COM6 probe
python -m megapro --port COM6 jog --x 10 --y 0 --z 0
python -m megapro --port COM6 home
python -m megapro --port COM6 stream examples/plot.gcode
python -m megapro --port COM6 estop
```

`probe` opens the port, prints the Marlin boot banner and answers `M115`;
`jog` issues one relative move; `home` runs the homing sequence (see Z-home
rule below); `stream` sends a G-code file line by line; `estop` sends the
emergency stop immediately.

## Baud fallback

Try `250000` first (Mega Pro stock Marlin default), then fall back to
`115200` if the banner is garbled or there is no response:

```sh
python -m megapro --port COM6 --baud 250000 probe
python -m megapro --port COM6 --baud 115200 probe
```

## Safety

Blocked codes (rejected by the safety guard, never sent): `M3`, `M4`, `M5`
(spindle/laser), `M104`, `M109` (hotend), `M140`, `M190` (bed), `M303`
(PID autotune heater excitation).

- Z-home rule: always home Z before any XY jog or stream; never drive Z
  below 0.
- E-stop: the `estop` command sends `M112` immediately and closes the port.
  Keep the printer power switch within reach during every run.

## Logs

All runs append under `logs/` (git-ignored): `logs/<action>_<baud>.log`
(e.g. `logs/probe_250000.log`, `logs/stream_250000.log`), plus full
`probe` serial transcripts as `logs/probe-*.txt`.

MANUAL probe acceptance: after running `probe`, paste the contents of
`logs/probe-*.txt` back to the operator — M0 is accepted only on a human
read of a real transcript.

## Roadmap

M1 adds pen-lift-aware G-code generation and drag-knife streaming with
bounds checking; M2 adds calibration (Z-lift height, XY skew) and repeatable
paper-frame workflows.

## GUI 上位机（轴控制 P0 + 写字/裁纸工作流 P1a + 零点/Z 标定 P1c）

Windows 桌面应用（中文界面），提供 连接管理 / 实时坐标 DRO / 手动轴控制 Jog /
一键寻零 / 笔起落 / 急停 / 命令回显 / 会话日志，以及 **文件作业**：载入 SVG →
预览（210×210 行程内）→ 开始执行（写字/裁纸）→ 进度/暂停/继续/中止。
需额外依赖：

```sh
pip install -r requirements-gui.txt   # PySide6==6.11.2（核心仍只依赖 pyserial）
```

从仓库根目录启动（profile/logs 为相对路径）：

```sh
PYTHONPATH=src python -m megapro.gui
```

- 连接 = 打开串口并等主板引导（~16s）；**打开即主板复位，连接后先「一键寻零」**。
- 一键寻零 = 抬 Z→G28 XY→G28 Z→抬 Z→Y 回安全点（终点 X0 Y50 Z30，profile 安全点）。
- Jog：方向键移动（Shift=10×，PageUp/PageDown 走 Z），或按钮 + 步长 0.1/1/10/自定义；
  **XY 平移仅当笔落下时自动先抬 Z+5**（笔抬起直接走，便于对刀）。
- **Z 触纸标定（非标笔每次装笔必做）**：换工具→点「Z 触纸标定」→ 向导先抬到安全
  Z，你逐步下探（1/0.5/0.2mm），笔尖刚触纸点「触到了」→ 自动把当前绝对 Z 写回
  profile（笔 `pen_down_z` / 刀 `cut_down_z`）。此后落笔/写字/裁刀用该高度。
- **工件原点（设原点）**：Jog 到纸/料角 → 点「设工件原点」→ DRO 显示工件坐标
  （XY 归零）；SVG 载入时自动平移到该原点。GUI 维护偏移（不发 G92，最稳）。
- 文件作业：工具选「笔(写字)」或「刀(裁纸)」→ 载入 SVG → 越界预检 → 预览 →
  开始执行。G-code 由 GUI 用**机器绝对 Z** 生成（不用 toolchain 的 Z0/Z1 防撞床）。
  执行中可暂停（≤1 段）或中止（自动抬 Z 回安全点）。
- **裁纸（P1b）**：材料预设 A4/A5/A6/自定义 + 切深 + 安全边距。**A4(210×297) 的
  Y 超 210 行程，只切可达区域并警示**。裁刀深度语义 = **刀尖触纸绝对 Z
  （cut_touch_z，用 Z 标定向导测）− 下压深度**（如 0.5mm）→ 得下压绝对 Z。
  载入越界按「min(材料, 210×210) − 边距」拦截。工具选刀后先做 Z 触纸标定。
- 「急停」裸发 M112，机器 halt 后**需断电重启**。
- **排版/制作（M3，Tab2）**：CAD/PS 式画布（mm 网格、标尺、原点=0、缩放控件、网格吸附）。
  工具：选择/直线/矩形/圆/折线/自由笔。编辑：**框选多选（拖动空白）/ Ctrl 多选 / Ctrl+A**、
  **删除（Del）**、**撤销/重做（Ctrl+Z/Y）**、复制粘贴（Ctrl+C/V/D）、箭头微调（Shift=10×）、
  **层序（置顶/置底/上移/下移）**、对齐/分布、W/H 数值、**双击文字重编**。
  可 **加文字**（**字体可选**、轮廓/单线、字号/字距/行距）、**加图片**（中心线/potrace，
  可选多阈值合并）、**导入 SVG**、**导入 Word/Excel**（.docx/.xlsx，自适应床面）。
  文字转路径用 fontTools（系统中文字体）；单线数据随附
  `data/chinese_hershey_heiti.json`（LingDong，OFL）；图片追踪需
  `bin/potrace.exe`（随附，GPL，来源见其 README）。
- 当前仅 Windows（COM 口 / CP210x）。

详见 `PRD.md`（v0.2）与 `src/megapro/gui/`。


## Layout

- `src/megapro/cli/` — CLI entrypoint (`--list-ports`, `--port`, `--baud`,
  `probe`/`jog`/`home`/`stream`/`estop`)
- `src/megapro/gui/` — 上位机 GUI（controller 纯逻辑 / worker QThread / main_window）
- `src/megapro/transport/` — Marlin USB-serial transport
- `src/megapro/dialect/` — Marlin dialect helpers
- `src/megapro/safety/` — blocked-code guard
- `profiles/mega-pro-marlin.yaml` — machine profile stub
- `examples/` — sample G-code inputs
- `logs/` — run logs and probe transcripts (git-ignored)

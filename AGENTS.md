# AGENTS.md — Mega Pro Pen Plotter (M0/M1a)

Drive an Anycubic Mega Pro as a pen plotter / paper cutter over its **stock
Marlin USB serial** — no firmware flash, no board mods. Laser is sealed,
heaters/spindle are hard-blocked in software. Read `REPORT.md` (Chinese,
full handoff with evidence) before touching transport or calibration.

Doc hierarchy: this file + `REPORT.md` are current and authoritative;
`README.md` lags (still says COM6, lists `M303` as guard-blocked — see below),
so trust code + this file, not README, when they disagree.

## Running / testing

Package is NOT pip-installed; `src/` is on the path only via `tests/conftest.py`
and the `scripts/*.py` header. From repo root:

```sh
PYTHONPATH=src python -m megapro --port COM7 probe   # (README shows plain `python -m megapro`; that fails without PYTHONPATH)
python -m pytest -q                                  # 33 tests, all green as of 2026-09-06
```

- Only dependency: `pyserial==3.5` (`requirements-m0.txt`). Stdlib only otherwise
  (Python 3.14) — **no PyYAML**: the profile is parsed/written by the hand-rolled
  flat-YAML parser in `cli/main.py` (`_load_profile`/`_save_profile`), so keep
  `profiles/mega-pro-marlin.yaml` in flat `key: value` shape (top-level, no
  indentation for scalars; the `raw: |` block is handled specially).
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
  coords + human/physical confirmation.

## Architecture / layer rules

- `src/megapro/cli/main.py` — argparse entry (`python -m megapro`);
  subcommands: `probe`, `jog`, `home`, `stream`, `move`, `estop`, `console`.
  Exit codes: **0** ok, **2** usage/guard/config, **3** link lost.
- `src/megapro/transport/marlin_serial.py` — all serial I/O. Nothing else
  touches `pyserial` directly (estop's raw M112 send is the CLI's one exception).
- `src/megapro/safety/guard.py` — `check()` vets **every** line before TX.
- `src/megapro/dialect/marlin.py` — G-code header/footer snippets.
- `src/megapro/toolchain/svg_to_gcode.py` — SVG → polylines → pen G-code
  (nearest-neighbor travel sort, stdlib XML only). Emits **Z0 = pen down,
  Z1 = pen up** in absolute coords — this Z convention is NOT mapped to the
  profile's calibrated `pen_down_z: 24` / `safe_z: 40`; the toolchain never
  reads the profile. Don't stream its output at calibrated height until a
  profile→Z mapping exists (M2 work).
- `src/megapro/preview/to_svg.py` — toolpath preview (blue pen-down, red dashed travel).
- `src/megapro/gui/` — **上位机（PySide6, 中文）**：
  - `controller.py`（纯逻辑：状态机 gating / M114 解析 / jog·home·pen·park 序列构造，
    无 Qt 可单测）、`worker.py`（常驻 QThread 串口消费者，req 信号跨线程，
    唯一碰 pyserial 的 Qt 对象）、`main_window.py`（薄 Qt 绑定，**QTab 双页**：
    Tab0 控制/作业、Tab1 排版/制作）、`calibration.py`（Z 触纸标定向导，
    写 `pen_down_z`/`cut_touch_z`）、入口 `python -m megapro.gui`。
  - 文件作业：`job.py` 用**机器绝对 Z** 生成 G-code（不用 toolchain emit 的 Z0/Z1）；
    写字落笔=pen_down_z，裁刀下压=cut_touch_z−深度；越界按材料/210 拦截。
  - **M3 排版**：`layout/`（model 纯逻辑 mm + export_svg 拍平导出 + canvas/items Qt）、
    `text_to_svg.py`（fontTools 轮廓字 + 单线 data/chinese_hershey_heiti.json）、
    `image_to_svg.py`（Pillow 阈值 + bin/potrace.exe 转线条）。导出 SVG 为工件坐标
    mm，`_on_layout_export` 写临时文件 → 作业流水线。
  - 安全语义与 CLI 一致（负 Z 需 allow_z；XY 平移按笔态抬 Z；pen_down 用 profile）。
  - 测试：`test_gui_controller/job/text/image/layout.py`（纯逻辑）、
    `test_gui_worker.py`（MarlinSim）、`test_gui_window.py` + `test_gui_layout_window.py`
    （独立 QApplication, offscreen）。
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
  (PID autotune, heats heater) is NOT blocked by `guard.py` — only README
  claims it is. Don't add guard-block list edits to make M303 pass/fail tests;
  the source of truth is `guard.py:BLOCKED_PREFIXES`.
- Z motion needs `--allow-z`; negative-Z / Z-homing moves additionally need
  `pen_down_z` present in the profile. XY travel auto Z-hops +5 mm unless
  `--draw`. `home` lifts +10 mm before G28. Z never below 0.
- After any serial open the board **resets** (DTR edge): coordinates zero,
  soft-limit clamps silently return `ok` without motion. Always home (G28)
  and use absolute coords after reset. `open_link()` already handles
  dtr/rts-low + `DTR_WAIT` 2.5 s + `settle_link()` cold-boot drain (~16 s) —
  keep that order if you touch transport.
- `G28` can take ~60 s, a single long move ~30 s (`HOME_TIMEOUT`/`MOVE_TIMEOUT`);
  don't shorten them.
- Multi-command calibration/sequences must use the `console` subcommand (one
  persistent session = no per-command resets). Each separate CLI invocation
  re-opens the port and resets the board.

## Current state / next steps (2026-09-06)

Calibrated (live 2026-09-07): pen touches paper at absolute **Z17**
(`pen_down_z: 17.0`, from safe Z30 down 13); safe point **X0 Y50 Z30**.
The pen is **friction-fit, no spring** (older notes saying "spring pen clip /
pen_down_z 24" are stale — the mount was re-done). Pen height was stable
across both trial runs. Roadmap: M1b drag-knife + bounds checks, M2
calibration workflows. Next physical steps per `REPORT.md` §9: re-aim camera,
then square dry-run → write → drag-knife.

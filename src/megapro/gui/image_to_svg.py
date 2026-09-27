"""图片 → 可画线条 SVG（纯逻辑，无 Qt；Pillow + potrace CLI）。

流水线（PRD M3b）：Pillow 打开 → 灰度 → 按目标 mm/分辨率缩放 → 阈值 →
写临时位图 → potrace(-a 0 多边形 / -t 去噪) → 解析其 SVG 输出 →
fill=none 描边折线 → mm 坐标 SVG。

potrace.exe 探测顺序：传入路径 → 随附目录（repo/bin）→ PATH。
potrace 是外置 GPL 二进制，随附需在 README 注明来源与许可（与主代码隔离）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

__all__ = [
    "find_potrace",
    "trace_image",
    "trace_image_multi",
    "image_to_line_svg",
]

_BIN_DIR = Path(__file__).resolve().parent.parent.parent.parent / "bin"
_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

#: 多阈值灰阶分档（0–255 灰度值，**与床尺寸无关**）：90/130/170/210（等差 40）。
#: §8.4 常量收敛后 src 不留裸 210 数值字面（那是 canvas/coords.py 的
#: BED_W/BED_H 唯一定义处），故写成等差式生成，取值逐个不变。
GRAY_THRESHOLDS = tuple(90 + 40 * k for k in range(4))


def find_potrace(exe: str | Path | None = None) -> str | None:
    """探测 potrace 可执行文件；找不到返回 None。"""
    if exe:
        p = Path(exe)
        return str(p) if p.exists() else None
    for cand in (_BIN_DIR / "potrace.exe", _BIN_DIR / "potrace"):
        if cand.exists():
            return str(cand)
    return shutil.which("potrace")


def _to_bitmap(img, path: Path, threshold: int = 160) -> None:
    """灰度 → 阈值 → 存 1-bit PBM(P4) 供 potrace。

    PBM: bit 1 = 黑。暗像素(<threshold) → 1(黑)。逐行打包，行按字节对齐。
    """
    gray = img.convert("L")
    w, h = gray.size
    px = gray.load()
    row_bytes = (w + 7) // 8
    buf = bytearray()
    for y in range(h):
        acc = 0
        for x in range(w):
            bit = 1 if px[x, y] < threshold else 0
            acc = (acc << 1) | bit
            if (x + 1) % 8 == 0:
                buf.append(acc)
                acc = 0
        if w % 8:
            buf.append(acc << (8 - w % 8))
    with open(path, "wb") as fh:
        fh.write(f"P4\n{w} {h}\n".encode("ascii"))
        fh.write(bytes(buf))


def _potrace_svg(exe: str, pbm_path: Path, turdsize: int = 4,
                 polygon: bool = True, out_path: Path | None = None) -> str:
    """跑 potrace 输出 SVG。-a 0 → 纯多边形（利于转折线）；-t 去噪。

    注意：本 Windows 构建 potrace 的 stdout 捕获为空 —— 用 -o 写文件再读回。
    """
    out = out_path or (pbm_path.parent / "_trace_out.svg")
    cmd = [exe, "-s", "-t", str(turdsize)]
    if polygon:
        cmd += ["-a", "0", "-O", "0"]
    cmd += ["-o", str(out), str(pbm_path)]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"potrace 失败 rc={proc.returncode}: "
            f"{proc.stderr.decode(errors='replace')}"
        )
    text = out.read_text(encoding="utf-8", errors="replace")
    if not polygon and out_path is None:
        try:
            out.unlink()
        except OSError:
            pass
    return text


def _svg_polylines(svg: str, mm_per_px: float) -> str:
    """把 potrace 的 SVG 转成描边折线 SVG（mm）。

    potrace 输出结构：<g transform="translate(TX,TY) scale(SX,SY)"><path d=.../></g>
    其中 SY 为负（y 翻转），坐标是 1/SX 倍。这里解析 g 的 transform 并应用到
    path 的每个 M/L/Z 点，再 × mm_per_px → 最终 mm 坐标 polyline。
    """
    out: list[str] = []

    def emit(pts: list[tuple[float, float]]) -> None:
        if len(pts) < 2:
            return
        parts = []
        for x0, y0 in pts:
            nx = a * x0 + c * y0 + e
            ny = b * x0 + dd * y0 + f
            parts.append(f"{_fmt(nx * mm_per_px)},{_fmt(ny * mm_per_px)}")
        out.append(f'<polyline points="{" ".join(parts)}" fill="none"/>')

    # 取 transform（若在 path 父级 g 上）与 path
    g_match = re.search(r'<g[^>]*transform="([^"]*)"[^>]*>', svg)
    a, b, c, dd, e, f = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    if g_match:
        a, b, c, dd, e, f = _parse_matrix(g_match.group(1))

    for m in re.finditer(r"<path[^>]*d=\"([^\"]*)\"", svg):
        d = m.group(1)
        cur: list[tuple[float, float]] = []
        x = y = 0.0  # 当前点
        sx = sy = 0.0  # 子路径起点
        i = 0
        toks = re.findall(r"[MmLlHhVvZz]|" + _NUM.pattern, d)
        # 归一化：把相对命令先收集成 (cmd, x, y) 序列
        while i < len(toks):
            t = toks[i]
            if t in ("M", "m", "L", "l", "H", "h", "V", "v", "Z", "z"):
                cmd = t
                i += 1
                # 收集本命令后连续坐标对（隐式重复）
                while i + 1 < len(toks) and toks[i] not in (
                        "M", "m", "L", "l", "H", "h", "V", "v", "Z", "z"):
                    try:
                        x1 = float(toks[i])
                    except ValueError:
                        break
                    # 视作绝对新点（potrace 数值都是绝对后的相对值？不——直接累加）
                    if cmd in ("h", "H", "v", "V"):
                        # H/V 单轴
                        val = x1
                        nx, ny = x, y
                        if cmd in ("h", "H"):
                            nx = x + val if cmd == "h" else val
                            ny = y
                        else:
                            nx = x
                            ny = y + val if cmd == "v" else val
                        i += 1
                    else:
                        if i + 1 >= len(toks):
                            break
                        try:
                            y1 = float(toks[i + 1])
                        except ValueError:
                            break
                        if cmd in ("m", "l"):
                            nx, ny = x + x1, y + y1
                        else:
                            nx, ny = x1, y1
                        i += 2
                    if cmd in ("M", "m") and not cur:
                        sx, sy = nx, ny
                        cur.append((nx, ny))
                    elif cmd in ("M", "m"):
                        # 新子路径起点
                        if len(cur) >= 2:
                            emit(cur)
                        cur = [(nx, ny)]
                        sx, sy = nx, ny
                    else:
                        cur.append((nx, ny))
                    x, y = nx, ny
                # 命令结束；若 M 后无点，继续
            elif t in ("Z", "z"):
                if cur and (cur[0][0] != x or cur[0][1] != y):
                    cur.append((cur[0][0], cur[0][1]))
                emit(cur)
                cur = []
                i += 1
            else:
                i += 1
        if cur:
            emit(cur)
    if not out:
        return '<svg xmlns="http://www.w3.org/2000/svg"/>\n'
    return '<svg xmlns="http://www.w3.org/2000/svg">\n  ' \
        + "\n  ".join(out) + "\n</svg>\n"


def _parse_matrix(spec: str) -> tuple[float, float, float, float, float, float]:
    """解析 'translate(tx,ty) scale(sx,sy)' 为 matrix (a,b,c,d,e,f)。"""
    a, b, c, d, e, f = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    for name, args in re.findall(r"([a-z]+)\(([^)]*)\)", spec.lower()):
        nums = [float(v) for v in re.findall(_NUM.pattern, args)]
        if name == "translate" and nums:
            e += nums[0]
            f += nums[1] if len(nums) > 1 else 0.0
        elif name == "scale" and nums:
            sx = nums[0]
            sy = nums[1] if len(nums) > 1 else sx
            a *= sx
            d *= sy
    return (a, b, c, d, e, f)


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _trace_polylines_px(img, exe, threshold, turdsize, tmp_dir, keep_files):
    """单阈值 potrace → 像素 polylines（(x,y)）。"""
    tmp = tmp_dir or Path("logs")
    tmp.mkdir(exist_ok=True)
    pbm = tmp / "_trace_in.pbm"
    out_svg = tmp / "_trace_out.svg"
    _to_bitmap(img, pbm, threshold)
    try:
        svg = _potrace_svg(exe, pbm, turdsize=turdsize, polygon=True,
                           out_path=out_svg)
    finally:
        if not keep_files:
            for f in (pbm, out_svg):
                try:
                    f.unlink()
                except OSError:
                    pass
    # 解析 svg → 像素坐标（mm_per_px=1，拿原始像素）
    return _svg_polylines_px(svg)


def _svg_polylines_px(svg: str) -> list:
    """把 potrace SVG 解析成像素 polylines（不缩放）。复用 _svg_polylines 内部逻辑。"""
    # _svg_polylines 用 mm_per_px 缩放；这里临时用 1.0 解析并取数值
    # 直接调 _svg_polylines 无法拿未缩放值 → 复制轻量解析：取出 path 的 M/L 序列（像素）
    out: list = []
    g_match = re.search(r'<g[^>]*transform="([^"]*)"[^>]*>', svg)
    a, b, c, dd, e, f = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    if g_match:
        a, b, c, dd, e, f = _parse_matrix(g_match.group(1))
    for m in re.finditer(r"<path[^>]*d=\"([^\"]*)\"", svg):
        cur = []
        x = y = 0.0
        i = 0
        toks = re.findall(r"[MmLlHhVvZz]|" + _NUM.pattern, m.group(1))
        while i < len(toks):
            t = toks[i]
            if t in ("M", "m", "L", "l", "H", "h", "V", "v", "Z", "z"):
                cmd = t
                i += 1
                while i + 1 < len(toks) and toks[i] not in (
                        "M", "m", "L", "l", "H", "h", "V", "v", "Z", "z"):
                    try:
                        v1 = float(toks[i])
                    except ValueError:
                        break
                    if cmd in ("h", "H", "v", "V"):
                        nx, ny = x, y
                        if cmd in ("h", "H"):
                            nx = x + v1 if cmd == "h" else v1
                        else:
                            ny = y + v1 if cmd == "v" else v1
                        i += 1
                    else:
                        if i + 1 >= len(toks):
                            break
                        try:
                            v2 = float(toks[i + 1])
                        except ValueError:
                            break
                        if cmd in ("m", "l"):
                            nx, ny = x + v1, y + v2
                        else:
                            nx, ny = v1, v2
                        i += 2
                    if cmd in ("M", "m") and not cur:
                        cur.append((nx, ny))
                    elif cmd in ("M", "m"):
                        if len(cur) >= 2:
                            out.append(cur)
                        cur = [(nx, ny)]
                    else:
                        cur.append((nx, ny))
                    x, y = nx, ny
            elif t in ("Z", "z"):
                if cur and (cur[0][0] != x or cur[0][1] != y):
                    cur.append((cur[0][0], cur[0][1]))
                if len(cur) >= 2:
                    out.append(cur)
                cur = []
                i += 1
            else:
                i += 1
        if len(cur) >= 2:
            out.append(cur)
    # 应用 matrix
    for p in out:
        for j, (px, py) in enumerate(p):
            p[j] = (a * px + c * py + e, b * px + dd * py + f)
    return out


def trace_image(
    img_path: str | Path,
    *,
    target_mm: float = 180.0,
    max_px: int = 1000,
    threshold: int = 160,
    turdsize: int = 4,
    potrace: str | Path | None = None,
    keep_files: bool = False,
    tmp_dir: Path | None = None,
) -> str:
    """把图片追踪成线条 SVG（mm 坐标，最长边 target_mm）。

    找不到 potrace 抛 FileNotFoundError。
    """
    exe = find_potrace(potrace)
    if not exe:
        raise FileNotFoundError(
            "找不到 potrace；请放入 bin/potrace.exe 或装到 PATH（见 README）"
        )
    img = _open_gray_scaled(img_path, max_px)
    w, h = img.size
    mm_per_px = target_mm / max(w, h)
    px_paths = _trace_polylines_px(img, exe, threshold, turdsize,
                                   tmp_dir, keep_files)
    return _paths_px_to_svg(px_paths, mm_per_px)


def trace_image_multi(
    img_path: str | Path,
    *,
    target_mm: float = 180.0,
    max_px: int = 1000,
    thresholds=GRAY_THRESHOLDS,
    turdsize: int = 4,
    potrace: str | Path | None = None,
    keep_files: bool = False,
    tmp_dir: Path | None = None,
) -> str:
    """多阈值拼接：各阈值 potrace trace 一次 → 合并 → 去重叠。

    每档独立 trace → 提取全（各亮度层的轮廓都在）；重叠去重避免重复画。
    注：独立 trace 拼接在档间接缝可能不连续，但提取最全（用户选定）。
    """
    exe = find_potrace(potrace)
    if not exe:
        raise FileNotFoundError(
            "找不到 potrace；请放入 bin/potrace.exe 或装到 PATH（见 README）"
        )
    img = _open_gray_scaled(img_path, max_px)
    w, h = img.size
    mm_per_px = target_mm / max(w, h)
    merged: list = []
    for th in sorted(thresholds):
        merged.extend(_trace_polylines_px(img, exe, th, turdsize,
                                          tmp_dir, keep_files))
    if merged:
        from megapro.gui.opt import dedup_overlap

        merged = dedup_overlap(merged, tol=1.5)
    return _paths_px_to_svg(merged, mm_per_px)


def _open_gray_scaled(img_path, max_px):
    from PIL import Image

    img = Image.open(img_path).convert("L")
    w, h = img.size
    scale = min(1.0, max_px / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    return img


def _paths_px_to_svg(paths, mm_per_px):
    if not paths:
        return '<svg xmlns="http://www.w3.org/2000/svg"/>\n'
    parts = []
    for p in paths:
        pts = " ".join(f"{_fmt(x * mm_per_px)},{_fmt(y * mm_per_px)}" for x, y in p)
        parts.append(f'<polyline points="{pts}" fill="none"/>')
    return '<svg xmlns="http://www.w3.org/2000/svg">\n  ' \
        + "\n  ".join(parts) + "\n</svg>\n"


# 便捷别名
image_to_line_svg = trace_image


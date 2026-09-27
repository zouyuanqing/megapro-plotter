"""文字 → 可画路径 SVG（纯逻辑，无 Qt）。

两种模式（PRD M3a）：
- 轮廓空心字（默认）：fontTools 读字体 → 每字形轮廓转闭合折线。中英通吃，
  读出来是「空心/描边」效果；小字（<~8mm）会糊，UI 需提示。
- 单线手写体：读随附 LingDong chinese-hershey 数据（data/chinese_hershey_heiti.json，
  若存在）→ 单线折线；缺字回退轮廓字。

坐标约定：
- 输出为**工件坐标** SVG（mm，y 向下与 toolchain 一致；本模块直接把字形 y 翻到
  SVG 坐标，行首基线在 y=0 上方 → 调用方决定整体摆放）。
- 每个字形轮廓是独立闭合折线（笔抬落笔跳转由下游处理）。

字体：Windows CJK（msyh.ttc 等）仅限本地使用（MS 许可）；分发应配 Noto CJK。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from megapro.gui.canvas.coords import flip_y_scalar

__all__ = [
    "find_cjk_font",
    "list_fonts",
    "text_outline_svg",
    "load_singleline_data",
    "text_singleline_svg",
]

#: 单线字体数据路径（随附，可选；不存在则 text_singleline_svg 回退轮廓）
_SINGLELINE_DATA = Path(__file__).resolve().parent.parent.parent.parent / "data" \
    / "chinese_hershey_heiti.json"

_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/msyh.ttc", 0),  # 微软雅黑
    ("C:/Windows/Fonts/simhei.ttf", None),  # 黑体
    ("C:/Windows/Fonts/simsun.ttc", 0),  # 宋体
    ("C:/Windows/Fonts/msyhbd.ttc", 0),
]
# 数字 → 上标/符号等直接 ASCII fallback 不处理；仅用于报错提示


def find_cjk_font() -> str | None:
    """返回第一个可用的系统中文字体路径；找不到返回 None。"""
    for path, _ in _FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


#: 系统字体目录（Windows）
_FONT_DIRS = [Path("C:/Windows/Fonts")]
_FONT_EXTS = (".ttf", ".ttc", ".otf")


def list_fonts(limit: int = 200) -> list[tuple[str, str]]:
    """列出可选字体 (显示名, 文件路径)。优先常见中文字体在前。

    扫描系统字体目录（Windows）。返回 (family_name, path)。TTC 用 face 0。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    # 常见中文字体优先
    priority = ["msyh", "msyhbd", "simhei", "simsun", "simkai", "simfang",
                "deng", "Noto", "SourceHan"]
    files: list[Path] = []
    for d in _FONT_DIRS:
        if d.exists():
            files.extend(sorted(d.glob("*")))
    files = [f for f in files if f.suffix.lower() in _FONT_EXTS]
    # 排序：priority 关键词优先
    def rank(f: Path) -> int:
        n = f.stem.lower()
        for i, kw in enumerate(priority):
            if kw.lower() in n:
                return i
        return len(priority)
    files.sort(key=rank)
    for f in files[:limit]:
        if str(f) in seen:
            continue
        seen.add(str(f))
        out.append((f.stem, str(f)))
    if not out:
        for path, _ in _FONT_CANDIDATES:
            if Path(path).exists():
                out.append((Path(path).stem, path))
    return out


def _load_font(path: str):
    from fontTools.ttLib import TTFont

    # .ttc 需 fontNumber；单 .ttf 不需
    num = None
    for cand_path, cand_num in _FONT_CANDIDATES:
        if Path(path).resolve() == Path(cand_path).resolve():
            num = cand_num
            break
    return TTFont(path, fontNumber=num) if num is not None else TTFont(path)


def _svg_esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text_outline_svg(
    text: str,
    *,
    font_path: str | None = None,
    size_mm: float = 10.0,
    line_gap_mm: float = 2.0,
    char_gap_mm: float = 0.0,
) -> str:
    """把 *text* 转成轮廓空心字 SVG（mm，y 向下）。

    返回含若干 <path> 的 <svg> 字符串。多行用 \n 分隔（行高 = size+line_gap）。
    坐标约定：基线放在 y=0，字形向上伸展到 y<0 —— 调用方需把整体平移到
    目标 y（如用 translate）。字形缺失画方框占位并计入步进。
    """
    from fontTools.pens.svgPathPen import SVGPathPen

    path = font_path or find_cjk_font()
    if not path:
        raise FileNotFoundError("找不到中文字体（msyh/simhei/simsun）；请指定 font_path")
    font = _load_font(path)
    paths: list[str] = []
    try:
        cmap = font.getBestCmap()
        gs = font.getGlyphSet()
        upem = font["head"].unitsPerEm
        scale = size_mm / upem
        line_h = size_mm + line_gap_mm
        # 每行一个 glyph 流；字形基线 y=0，行号 → y 下移（SVG y 下，行 n 基线 = n*line_h）
        # 但字要向上长（基线上方），故字形实际落在 y≈ -ascender..+descender
        ascender = font["hhea"].ascent / upem * size_mm
        descender = font["hhea"].descent / upem * size_mm
        # 让整段文字向下移到 y>=0：总上移量 = ascender（首行顶在 y=0）
        baseline0 = ascender  # 首行基线 svg y（在字形 bbox 之下？见下）
        # 字形经 matrix: y' = -scale*y + svg_h，svg_h 需≥字形最高点(ascender mm)
        # 取 svg_h = ascender（首行），则字顶 y'=0，字身向下到 ascender+descender 区域
        y_adv = ascender  # 字形变换里的 svg_h 基准（=首行基线在翻转后位置）
        for line_no, line in enumerate(text.split("\n")):
            svg_h = y_adv + line_no * line_h  # 本行字形变换的 y' 基准
            cx = 0.0
            for ch in line:
                gname = cmap.get(ord(ch))
                advance = 0.0
                if gname is not None and gname in gs:
                    glyph = gs[gname]
                    # glyf 用 bounds 判空；CFF 无 bounds 属性 → 尝试 draw
                    if _glyph_empty(glyph):
                        try:
                            advance = glyph.width / upem * size_mm
                        except AttributeError:
                            advance = size_mm
                        cx += advance + char_gap_mm
                        continue
                    pen = SVGPathPen(gs)
                    glyph.draw(pen)
                    d = pen.getCommands()
                    if d:
                        paths.append(_wrap_glyph(d, cx, scale, svg_h))
                    try:
                        advance = glyph.width / upem * size_mm
                    except AttributeError:
                        advance = size_mm
                else:
                    advance = size_mm  # 缺字占位
                    _missing_box(paths, cx, svg_h, size_mm)
                cx += advance + char_gap_mm
        body = "\n  ".join(paths)
        return f'<svg xmlns="http://www.w3.org/2000/svg">\n  {body}\n</svg>\n'
    finally:
        try:
            font.close()
        except Exception:
            pass


def _glyph_empty(glyph) -> bool:
    """粗略判字形是否为空（空格等无轮廓字形）。glyf 看 bounds；其他画一遍判空。"""
    try:
        return glyph.bounds is None
    except AttributeError:
        return False


def _wrap_glyph(d: str, dx: float, scale: float, svg_h: float) -> str:
    """把 glyph path d 用 <path transform=matrix> 包成 mm 坐标 path。

    matrix: x' = scale*x + dx ; y' = svg_h - scale*y （y 翻转 + 缩放到 mm）
    svg_h = 本行字形 y' 基准（把字形最高点放到 y≈svg_h 之上方小值区）。
    """
    mat = f"matrix({_n(scale)} 0 0 {_n(-scale)} {_n(dx)} {_n(svg_h)})"
    return f'<path transform="{mat}" d="{_svg_esc(d)}"/>'


def _missing_box(paths: list[str], cx: float, top: float, size_mm: float) -> None:
    # 缺字：画一个方框占位（可画，提示该字不在字体中）
    s = size_mm
    d = f"M{cx:.3f} {top - s:.3f}H{cx + s:.3f}V{top:.3f}H{cx:.3f}Z"
    paths.append(f'<path d="{d}"/>')


def _n(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------
# 单线手写体（LingDong chinese-hershey）
# --------------------------------------------------------------------------

def load_singleline_data(path: str | Path | None = None) -> dict:
    """读单线字体数据 JSON：{codepoint_str: [[[x,y],...], ...]}（0-1 归一化）。"""
    p = Path(path) if path else _SINGLELINE_DATA
    if not p.exists():
        raise FileNotFoundError(f"单线字体数据不存在：{p}")
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def text_singleline_svg(
    text: str,
    *,
    data: dict | None = None,
    data_path: str | Path | None = None,
    size_mm: float = 10.0,
    char_gap_mm: float = 0.0,
    fallback_font: str | None = None,
) -> str:
    """单线手写体文字 → SVG。缺字（数据未含）回退轮廓字(fallback_font)。"""
    data = data if data is not None else load_singleline_data(data_path)
    paths: list[str] = []
    cx = 0.0
    for ch in text:
        key = f"U+{ord(ch):04X}"
        strokes = data.get(key)
        if strokes is None:
            # 回退：用轮廓字画这个字（保持步进一致较复杂——简单做法：方框+报错）
            # v1：缺字画方框并标记（单线数据未含）
            _missing_box(paths, cx, 0.0, size_mm)
            cx += size_mm + char_gap_mm
            continue
        for stroke in strokes:
            pts = " ".join(
                f"{_n((x + cx / size_mm))},{_n(y)}" for x, y in stroke
            )
            # 数据 0-1 归一化 → mm：乘 size；y 翻 = 格式编码（归一化 y-up →
            # SVG y-down），经 coords.flip_y_scalar 唯一翻转实现（阶段 2 迁调，
            # 数学等价：flip_y_scalar(y, 1.0) == 1.0 - y）
            pts2 = " ".join(
                f"{_n((x * size_mm) + cx)},{_n(flip_y_scalar(y, 1.0) * size_mm)}"
                for x, y in stroke
            )
            paths.append(f'<polyline points="{pts2}" fill="none"/>')
        cx += size_mm + char_gap_mm
    body = "\n  ".join(paths)
    return f'<svg xmlns="http://www.w3.org/2000/svg">\n  {body}\n</svg>\n'

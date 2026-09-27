"""coords 坐标权威单测（纯逻辑，零 Qt，不进事件循环）——蓝图 §8.1。

覆盖：svg_ydown ↔ paper 对合、machine ≡ paper 恒等、place_at_anchor 纯平移、
grid_steps Heckbert 阶梯、translate_paths 纯平移、翻转数学单一来源静态扫描。
"""

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

from megapro.gui.canvas.coords import (
    BED_H,
    BED_W,
    anchor_point,
    bbox_of,
    flip_y_scalar,
    grid_steps,
    machine_from_paper,
    paper_from_machine,
    paper_from_svg_ydown,
    paper_to_svg_ydown,
    place_at_anchor,
    snap,
    translate_paths,
)

REPO = Path(__file__).resolve().parent.parent


# --- SVG y-down ↔ 纸面 y-up（对合） ------------------------------------------

def test_paper_svg_ydown_involution():
    paths = [[(0.0, 0.0), (0.0, 10.0), (20.0, 90.0)]]
    svg = paper_to_svg_ydown(paths)
    # y_svg = bed_h − y_paper（x 不变）
    assert svg[0] == [(0.0, 210.0), (0.0, 200.0), (20.0, 120.0)]
    # 对合：双向各翻两次还原
    assert paper_from_svg_ydown(svg) == paths
    assert paper_to_svg_ydown(paper_from_svg_ydown(paths)) == paths
    # 不改入参；自定义 bed_h 同式
    assert paths[0] == [(0.0, 0.0), (0.0, 10.0), (20.0, 90.0)]
    assert paper_to_svg_ydown([[(3.0, 4.0)]], bed_h=100.0) == [[(3.0, 96.0)]]
    # 唯一翻转实现自反
    assert flip_y_scalar(flip_y_scalar(7.5, 210.0), 210.0) == 7.5


# --- 纸面 ≡ 机器（恒等） ------------------------------------------------------

def test_machine_is_paper_identity():
    for p in [(0.0, 0.0), (12.5, -3.25), (210.0, 210.0), (0.5, 209.5)]:
        assert machine_from_paper(p) is p
        assert paper_from_machine(p) is p
        assert machine_from_paper(p) == p


# --- 内容归位（放置，纯平移） -------------------------------------------------

def test_place_at_anchor():
    paths = [[(10.0, 20.0), (30.0, 50.0)]]
    bb = bbox_of(paths)
    assert bb == (10.0, 20.0, 30.0, 50.0)
    # 九宫格锚点（y-up：b=下 t=上）
    assert anchor_point(bb, "bl") == (10.0, 20.0)
    assert anchor_point(bb, "bc") == (20.0, 20.0)
    assert anchor_point(bb, "br") == (30.0, 20.0)
    assert anchor_point(bb, "ml") == (10.0, 35.0)
    assert anchor_point(bb, "mc") == (20.0, 35.0)
    assert anchor_point(bb, "mr") == (30.0, 35.0)
    assert anchor_point(bb, "tl") == (10.0, 50.0)
    assert anchor_point(bb, "tc") == (20.0, 50.0)
    assert anchor_point(bb, "tr") == (30.0, 50.0)
    with pytest.raises(ValueError):
        anchor_point(bb, "xx")
    # 默认 bl → 目标默认工件原点
    out = place_at_anchor(paths, "bl", (0.0, 0.0))
    assert bbox_of(out) == (0.0, 0.0, 20.0, 30.0)
    # 锚点落在 target 上
    out2 = place_at_anchor(paths, "tc", (5.0, 5.0))
    assert anchor_point(bbox_of(out2), "tc") == (5.0, 5.0)
    # 纯平移：相对几何/段序保持（组级一次平移）
    group = [[(0.0, 0.0), (2.0, 0.0)], [(4.0, 3.0), (6.0, 3.0)]]
    placed = place_at_anchor(group, "bl", (1.0, 1.0))
    assert placed[0] == [(1.0, 1.0), (3.0, 1.0)]
    assert placed[1] == [(5.0, 4.0), (7.0, 4.0)]
    # 空输入返回拷贝
    assert place_at_anchor([], "bl", (0.0, 0.0)) == []


# --- Heckbert 网格步距 --------------------------------------------------------

def test_grid_steps_heckbert():
    # major = 满足 major*ppm ≥ target_px(40) 的最小 1/2/5×10^k；minor = major/5
    assert grid_steps(1.0) == (50.0, 10.0)
    assert grid_steps(2.0) == (20.0, 4.0)
    assert grid_steps(12.0) == (5.0, 1.0)
    assert grid_steps(0.02) == (2000.0, 400.0)
    assert grid_steps(1.5) == (50.0, 10.0)
    assert grid_steps(4.0) == (10.0, 2.0)  # need=10 恰在阶梯上
    with pytest.raises(ValueError):
        grid_steps(0.0)
    with pytest.raises(ValueError):
        grid_steps(1.0, target_px=0.0)


def test_snap_scalar_grid():
    assert snap(3.7, 1.0) == 4.0
    assert snap(3.2, 1.0) == 3.0
    assert snap(2.5, 0.5) == 2.5
    with pytest.raises(ValueError):
        snap(1.0, 0.0)


# --- 平移（工件原点） ---------------------------------------------------------

def test_translate_paths_pure_shift():
    paths = [[(0.0, 0.0), (20.0, 0.0)], [(5.0, 5.0)]]
    out = translate_paths(paths, 10.0, 50.0)
    assert out == [[(10.0, 50.0), (30.0, 50.0)], [(15.0, 55.0)]]
    # 纯平移：段数/段内点数/顺序/方向全部保持
    assert len(out) == len(paths)
    assert [len(p) for p in out] == [len(p) for p in paths]
    # 原列表不变
    assert paths[0][0] == (0.0, 0.0)
    zero = translate_paths(paths, 0.0, 0.0)
    assert zero == paths and zero is not paths
    # controller 保留 re-export（现有 import 不破，且是同一实现）
    from megapro.gui.controller import translate_paths as from_controller
    from megapro.gui.canvas.coords import translate_paths as from_coords

    assert from_controller is from_coords


# --- 翻转数学单一来源（静态扫描，§8.1） ---------------------------------------

# 禁止 pattern（§8.1 基础上补实证盲区，独立评审 medium #2 与 low #2；阶段 3
# 迁移债复核确认覆盖以下三形）：
#   ① 表达位**小写**翻转 `bed_h - y` / `_bed_h - …`（旧 job.py:161 形与
#      layout/undo.py:96,104 的 `…._bed_h - new[1]` 形）；
#   ② 右操作数泛化 `\w+\[1\]`（undo.py:104 实为 `_bed_h - old[1]`）；
#   ③ 右操作数吃数字后缀 `y[0-9]*`（`y2 = bed_h - y1` 变量后缀形，原 `y\b` 不吃）；
#   ④ float 字面 `y = 210.0 - y`（浮点形，原 `210\s*-` 不认 `.0`）；
#   ⑤ 负号后允许空格 `(x, - y)`（负号空格形，原 `\(x,\s*-y\)` 不认）。
# 仍不得误伤 doc_import.py:53 的 `BED_H - 2 * margin`（尺寸算术，非翻转）。
_FLIP_PAT = re.compile(
    r"(?:y'|_y)\s*=\s*(?:BED_H|bed_h|span)\s*-"
    r"|(?:BED_H|bed_h|_bed_h|span)\s*-\s*(?:(?:py|ny|mmv|y)[0-9]*\b|v\.y\(\)|\w+\[1\])"
    r"|(?:210(?:\.0)?|1\.0)\s*-\s*y[0-9]*\b"
    r"|ysum\s*-\s*y[0-9]*\b"
    r"|\(x,\s*-\s*y[0-9]*\b\)"
)

#: 唯一允许的翻转实现本体：coords.flip_y_scalar 的函数体（`span - y`）。
#: 扫描时按 AST 定位排除该函数体；其他任何文件/位置的同形表达式仍算命中。
_AUTHORITY_FN = "flip_y_scalar"

# 扫描范围 = **src/megapro 全树**（§2.1「任何文件」；评审 low #2 补齐
# preview/toolchain/cli/worker/opt/presets/calibration/edge_to_svg/image_to_svg
# 等此前漏扫文件）。阶段 2 迁移债表 (_LEGACY_STAGE2) 已按其自身规则
# 「阶段 2 清零删表」删除。
_SCAN_FILES = sorted(
    p.relative_to(REPO).as_posix()
    for p in (REPO / "src/megapro").rglob("*.py")
)
#: 符号级扫描另覆盖 tests/：C1 债务形态（``paper_to_svg_ydown as flip_y``）
#: 就出生在 tests，且不含任何被禁翻转 pattern（pattern 扫描拦不住）。
#: 翻转写法 pattern 的口径仍是 src 全树（tests/ 不在 pattern 口径）。
_TEST_FILES = sorted(
    p.relative_to(REPO).as_posix()
    for p in (REPO / "tests").rglob("*.py")
)
#: 阶段 3 起翻转写法**零容忍**：白名单清空（main_window 的旧 (210−y) 已随
#: _draw_preview 删除；调用 coords.flip_y_scalar/paper_to/from_svg_ydown 是
#: 格式编解码边界，不算命中）。
_FLIP_WHITELIST: dict = {}

_STRIP_TYPES = {tokenize.STRING, tokenize.COMMENT}
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
    _tok = getattr(tokenize, _name, None)  # Python 3.12+ 才有 f-string 专用 token
    if _tok is not None:
        _STRIP_TYPES.add(_tok)


def _code_only(text: str) -> str:
    """用 tokenize 剥离 STRING/COMMENT/f-string 字面（保留换行，其余补空格）。

    Python 3.12+ 的 f-string 字面段是 FSTRING_MIDDLE（不是 STRING）——必须一并
    剥离，否则 f"210 - y" 里的翻转字样会漏扫（评审 medium #2 实证）。f-string
    的 ``{…}`` 表达式段是普通 token、**保留**（那里是真实算术，该抓）。
    """
    starts: list[int] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        starts.append(pos)
        pos += len(line)
    out = list(text)
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in _STRIP_TYPES:
            (r1, c1), (r2, c2) = tok.start, tok.end
            o1, o2 = starts[r1 - 1] + c1, starts[r2 - 1] + c2
            for i in range(o1, o2):
                if out[i] != "\n":
                    out[i] = " "
    return "".join(out)


def _authority_span(text: str):
    """coords.flip_y_scalar 的函数体行号 range（唯一允许的翻转实现本体）。"""
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == _AUTHORITY_FN:
            return range(node.lineno, node.end_lineno + 1)
    return None


def _flip_hits(rel: str) -> list[tuple[int, str]]:
    """一个源文件的翻转命中 [(行号, 匹配串)]（剥字面/注释、豁免权威实现本体）。"""
    text = (REPO / rel).read_text(encoding="utf-8")
    code = _code_only(text)
    span = _authority_span(text) if rel.endswith("coords.py") else None
    hits = []
    for m in _FLIP_PAT.finditer(code):
        line = code.count("\n", 0, m.start()) + 1
        if span is not None and line in span:
            continue  # flip_y_scalar 本体 = 唯一实现，不算「手写翻转」
        hits.append((line, m.group(0)))
    return hits


def test_flip_pattern_strength():
    """pattern/剥离器自检：旧翻转形必须命中、非翻转/字面不得误伤。"""
    def hit(src):
        return bool(_FLIP_PAT.search(_code_only(src)))

    assert hit("out = [(x, bed_h - y) for x, y in p]")  # 旧 job.py:161 形
    assert hit("q = self._bed_h - y")  # 小写下标形（阶段 1 评审盲区）
    assert hit("y2 = bed_h - y1")  # 数字后缀（评审 low #2）
    assert hit("y = 210.0 - y")  # float 字面（评审 low #2）
    assert hit("pts2 = (x, - y)")  # 负号后空格（评审 low #2）
    assert hit("gi.setPos(x, self.page._bed_h - new[1])")  # undo.py:96 形
    assert hit("gi.setPos(x, self.page._bed_h - old[1])")  # undo.py:104 形
    assert hit("pts = [(x, -y) for x, y in p]")  # items.py:56 形
    assert hit("yy = (1.0 - y) * size_mm")  # text_to_svg.py:253 形
    assert hit("y2 = BED_H - py")
    assert hit("s = 210 - y")
    assert hit("prev_y = span - y")  # 权威实现的复制粘贴（赋值形）
    # 不得误伤：尺寸算术 / 普通减法 / 字面与注释 / f-string 字面段
    assert not hit("n = BED_H - 2 * margin")  # doc_import.py:53
    assert not hit("x1 = origin_x + max_x - margin - pen_radius")
    assert not hit("s = '210 - y'  # 210 - y")
    assert not hit('s = f"210 - y"')
    assert hit('s = f"{210 - y}"')  # f-string 表达式段是真实算术，该抓


def test_flip_scan_covers_src_tree():
    """扫描范围 = src/megapro 全树（无漏扫子包/文件，迁移债②的范围条款）。"""
    all_py = sorted(
        p.relative_to(REPO).as_posix()
        for p in (REPO / "src/megapro").rglob("*.py")
    )
    assert _SCAN_FILES == all_py
    for must in ("src/megapro/gui/canvas/coords.py",
                 "src/megapro/gui/canvas/snap.py",
                 "src/megapro/gui/layout/model.py",
                 "src/megapro/gui/layout/export_svg.py",
                 "src/megapro/gui/main_window.py",
                 "src/megapro/gui/job.py",
                 "src/megapro/toolchain/svg_to_gcode.py",
                 "src/megapro/preview/to_svg.py",
                 "src/megapro/safety/guard.py"):
        assert must in _SCAN_FILES, f"漏扫: {must}"


def test_flip_math_single_source():
    """翻转数学单一来源：全树扫描命中 = ∅（阶段 3 起零容忍，白名单已清空）。

    调用 coords.flip_y_scalar / paper_to_svg_ydown / paper_from_svg_ydown 不算
    命中（SVG 互换层格式编解码边界）；唯一实现本体按 AST 豁免。

    **零命中口径**：src/megapro **全树、符号级**（tests/ 的翻转写法不在
    pattern 口径）——本用例可执行扫描 = 剥注释/字符串后的翻转写法 pattern
    （``_FLIP_PAT``）全树零命中；**符号层由独立用例自动钉死**
    （:func:`test_flip_symbol_level_zero_hit`，tokenize NAME 级扫 src+tests：
    独立 ``flip_y``/``already_paper`` 符号零命中、``flip_y_scalar`` 是唯一
    实现本体不算，C1 别名形态 ``paper_to_svg_ydown as flip_y`` /
    ``flip_y = paper_to_svg_ydown`` 由它拦）。``tempfile`` 字面不在本口径
    （它不是翻转写法）：排版导出的临时文件**往返本体**已删（main_window
    无 tempfile）；仍存活的两处均有出处 —— ``layout/doc_import.py`` 顶部
    import（既有未使用 import，文件属改动清单外）与
    ``layout_page._svg_to_paths`` 的字符串→临时文件解析桥（§2.2 missing #6
    要求保名保留）。
    """
    hits: dict[str, list] = {}
    for rel in _SCAN_FILES:
        found = _flip_hits(rel)
        if found:
            hits[rel] = found
    assert not _FLIP_WHITELIST, "白名单应为空（阶段 3 零容忍）"
    assert not hits, f"禁止的翻转写法: {hits}"


def test_flip_symbol_level_zero_hit():
    """符号级零命中（§8.1 零命中口径的符号层，自动钉死，非手工 grep）。

    tokenize NAME 级扫描 src/megapro **与 tests/**：独立 ``flip_y`` /
    ``already_paper`` 符号一律零命中。NAME token 天然不含注释/字符串
    （文档、注释里提到这些名字不误伤）。钉死的是 pattern 扫描拦不住的
    C1/C2 债务形态 —— ``from … import paper_to_svg_ydown as flip_y`` 别名、
    ``flip_y = paper_to_svg_ydown`` 赋值别名等不含任何被禁翻转写法，重犯时
    ``_FLIP_PAT`` 扫描仍绿，只能靠符号层拦。``flip_y_scalar`` 是唯一翻转
    实现本体（NAME 整词为 ``flip_y_scalar``，不等于 ``flip_y``），不算命中。
    """
    banned = {"flip_y", "already_paper"}
    hits: dict[str, list] = {}
    for rel in _SCAN_FILES + _TEST_FILES:
        text = (REPO / rel).read_text(encoding="utf-8")
        found = [
            (tok.start[0], tok.string)
            for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type == tokenize.NAME and tok.string in banned
        ]
        if found:
            hits[rel] = found
    assert not hits, f"禁止的翻转符号（含 C1 别名形态）: {hits}"


def test_bed_constants_single_source():
    """BED_W/BED_H=210.0 的数值字面只在 coords.py 出现一处（全 src 扫描）。

    阶段 5 收敛完成：§8.4 白名单已清空（job.py 默认参 / main_window 材料表 /
    presets.py / preview 默认参全部改引 BED_W/BED_H；灰阶阈值等非床尺寸的
    210 同数异义项改为等差式生成，取值不变）—— coords 之外一律零命中。
    """
    pat = re.compile(r"\b210(?:\.0)?\b")
    owned = [rel for rel in _SCAN_FILES if not rel.endswith("coords.py")]
    for rel in owned:
        text = _code_only((REPO / rel).read_text(encoding="utf-8"))
        found = pat.findall(text)
        assert not found, f"{rel} 出现 210 数值字面 {found}（应引用 BED_W/BED_H）"
    coords_text = _code_only((REPO / "src/megapro/gui/canvas/coords.py")
                             .read_text(encoding="utf-8"))
    assert len(pat.findall(coords_text)) == 1  # 唯一定义处
    assert BED_W == 210.0 and BED_H == 210.0


def test_bed_210_literal_convergence():
    """§8.4 常量收敛扫描（阶段 5 验收）：tokenize 剥注释/字符串后，``\\b210(\\.0)?\\b``
    数值字面**仅允许** ``canvas/coords.py`` 的 ``BED_W/BED_H`` 定义处。

    与 :func:`test_bed_constants_single_source` 同一把尺，但按「允许行」精确
    到定义行（不是整文件豁免）；白名单为空（不允许任何其它文件/行）。
    """
    pat = re.compile(r"\b210(?:\.0)?\b")
    allowed_rel = "src/megapro/gui/canvas/coords.py"
    hits: dict[str, list[int]] = {}
    for rel in _SCAN_FILES:
        code = _code_only((REPO / rel).read_text(encoding="utf-8"))
        lines = sorted({code.count("\n", 0, m.start()) + 1
                        for m in pat.finditer(code)})
        if lines:
            hits[rel] = lines
    assert set(hits) <= {allowed_rel}, f"210 数值字面越界: {hits}"
    # 唯一允许处 = coords.py 的 BED_W/BED_H 定义行（`BED_W = BED_H = 210.0`）
    src = (REPO / allowed_rel).read_text(encoding="utf-8")
    def_lines = [i for i, ln in enumerate(src.splitlines(), 1)
                 if "BED_W" in ln and "BED_H" in ln and "=" in ln]
    assert hits.get(allowed_rel) == def_lines[:1], (
        f"210 数值字面应恰在 BED_W/BED_H 定义行 {def_lines[:1]}，实际 {hits}")

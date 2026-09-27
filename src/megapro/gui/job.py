"""Job 编译器 —— 把 SVG/文件转成可直接发给机器的 G-code（纯逻辑，无 Qt）。

发射只在本模块：只读复用 toolchain 的 parse_svg + nearest_neighbor_sort 取
polylines，然后用**机器绝对 Z** 生成——空移/抬笔在 safe_z、落笔到
pen_down_z（写字）或 cut_down_z（裁刀下压）。Z 是绝对机器坐标（safe_z 高 /
落刀低），杜绝 Z0 顶床。（旧 toolchain 的 Z0/Z1 双轨发射器已随阶段 5 退役，
svg_to_gcode 只留纯数据侧。）

本机 Z 语义（profile 标定）：绝对 Z 越大越高；safe_z≈30 抬笔安全高度，
pen_down_z≈17 笔尖触纸，cut_down_z（裁刀）按材料下压。所有坐标视作
工件坐标（若设了 G92 工件原点，则由调用方按偏移平移后再交给 check_bounds）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from megapro.gui.canvas.coords import (
    BED_H,
    BED_W,
    place_at_anchor,
    translate_paths,
)
from megapro.gui.gcode_parse import Segment, parse_lines

__all__ = [
    "polylines_for_svg",
    "check_bounds",
    "check_bounds_v2",
    "BoundsViolation",
    "BoundsReport",
    "JobBoundsError",
    "gcode_for_drawing",
    "gcode_for_cutting",
    "cut_z_for_depth",
    "Placement",
    "OptParams",
    "MotionParams",
    "ZMap",
    "Material",
    "JobSpec",
    "CompiledJob",
    "compile_job",
]

_DEFAULT_XY_FEED = 1200.0
_DEFAULT_Z_FEED = 300.0


def polylines_for_svg(path, *, sort: bool = True) -> list[list[tuple[float, float]]]:
    """读 SVG → 多段线（复用 toolchain，纯数据）。

    sort=True（默认，保旧契约）做最近邻排序；sort=False 保持文件原序
    （JobSpec 构建一律用原序，NN 只在 compile_job 内按 opt.sort 执行）。
    """
    from megapro.toolchain.svg_to_gcode import nearest_neighbor_sort, parse_svg

    paths = parse_svg(path)
    return nearest_neighbor_sort(paths) if sort else paths


def check_bounds(
    paths: list[list[tuple[float, float]]],
    *,
    max_x: float = BED_W,
    max_y: float = BED_H,
    margin: float = 0.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    pen_radius: float = 0.0,
) -> list[str]:
    """越界预检：返回超出 [origin, origin+max-margin] 的路径描述（空=全过）。

    坐标按工件坐标；若设了工件原点偏移，调用方应把路径先平移（或传 origin）。
    margin 为安全边距；pen_radius 为笔尖半径 → 判定区再内缩（笔心距边界留半径，
    防笔尖出界/笔画压边）。
    """
    x0, y0 = origin_x, origin_y
    # 笔径只内缩**远端**上界（max 边）：原点 0 是物理归位端，笔心到 0 即可
    # （纸从 0 铺起，靠 0 边的半笔尖超出纸缘无妨）；远端超程才需留半径防出界。
    x1, y1 = origin_x + max_x - margin - pen_radius, origin_y + max_y - margin - pen_radius
    bad: list[str] = []
    for i, p in enumerate(paths):
        if not p:
            continue
        for x, y in p:
            if x < x0 or x > x1 or y < y0 or y > y1:
                bad.append(f"路径#{i} 越界: ({x:g},{y:g}) 超出 [{x0:g},{x1:g}]×[{y0:g},{y1:g}]")
                break
    return bad


def gcode_for_drawing(
    paths: list[list[tuple[float, float]]],
    *,
    pen_down_z: float,
    safe_z: float,
    feed_xy: float = _DEFAULT_XY_FEED,
    feed_z: float = _DEFAULT_Z_FEED,
    travel_lift_mm: float = 5.0,
) -> list[str]:
    """写字：每段 = 空移起点 → 落笔 pen_down_z → 沿路径画 → 抬离再跳段。

    抬笔策略：首段前抬到 safe_z（安全高度）一次；之后**段间只抬离纸面
    travel_lift_mm**（绝对 = pen_down_z + travel_lift_mm，封顶 safe_z）——
    默认 5mm 就够跳段，不必每段回 safe_z(30)，省时省磨损。
    Z 全为绝对机器坐标（safe_z ≥ pen_down_z；相等=空跑不下压）。
    cur 初值 None：**首段必发定位 G0**（哪怕起点恰是 (0,0)），杜绝「假设
    起点已在 (0,0)」漏发定位 → 落笔后拖线（根因 #5）。
    """
    if safe_z < pen_down_z:
        raise ValueError(
            f"safe_z({safe_z}) 不能低于 pen_down_z({pen_down_z})（相等=空跑）")
    if pen_down_z < 0:
        raise ValueError(f"pen_down_z 不能为负: {pen_down_z}（Z 永不为负）")
    if travel_lift_mm < 0:
        raise ValueError(f"travel_lift_mm 不能为负: {travel_lift_mm}")
    lift_z = min(safe_z, pen_down_z + travel_lift_mm)
    if lift_z < 0:
        raise ValueError(
            f"抬笔 Z 不能为负: lift_z={lift_z}（输出所有 Z ≥ 0）")
    lines = ["G90", "G21"]
    lines.append(f"G0 Z{_fmt(safe_z)} F{_fmt(feed_z)}")  # 首段前抬到安全高度
    cur: tuple[float, float] | None = None
    for p in paths:
        if not p:
            continue
        if cur is None or p[0] != cur:
            lines.append(f"G0 X{_fmt(p[0][0])} Y{_fmt(p[0][1])} F{_fmt(feed_xy)}")
        lines.append(f"G1 Z{_fmt(pen_down_z)} F{_fmt(feed_z)}")  # 落笔
        for x, y in p:
            lines.append(f"G1 X{_fmt(x)} Y{_fmt(y)} F{_fmt(feed_xy)}")
        lines.append(f"G0 Z{_fmt(lift_z)} F{_fmt(feed_z)}")  # 段间抬离（低抬）
        cur = p[-1]
    lines.append("M400")  # 排空：等所有移动完成
    return lines


def gcode_for_cutting(
    paths: list[list[tuple[float, float]]],
    *,
    cut_down_z: float,
    safe_z: float,
    feed_xy: float = _DEFAULT_XY_FEED,
    feed_z: float = _DEFAULT_Z_FEED,
    passes: int = 1,
    travel_lift_mm: float = 5.0,
) -> list[str]:
    """裁纸：闭合轮廓整圈下压切。

    每段：空移到起点（首段前抬 safe_z）→ 下压 cut_down_z → 沿整圈 G1 →
    抬离 travel_lift_mm（绝对 = cut_down_z + travel_lift_mm，封顶 safe_z）再跳段。
    默认单遍（passes=1）；多遍 = 每遍重新下压沿圈（依赖机器重复性，慎用）。
    cur 初值 None：**首段必发定位 G0**，下压发生在定位到 p[0] 之后（根因 #5：
    否则 p[0]==(0,0) 时会在错误 XY 先下压误切）。
    """
    if safe_z < cut_down_z:
        raise ValueError(
            f"safe_z({safe_z}) 不能低于 cut_down_z({cut_down_z})（相等=空跑）")
    if cut_down_z < 0:
        raise ValueError(f"cut_down_z 不能为负: {cut_down_z}（Z 永不为负）")
    if travel_lift_mm < 0:
        raise ValueError(f"travel_lift_mm 不能为负: {travel_lift_mm}")
    lift_z = min(safe_z, cut_down_z + travel_lift_mm)
    if lift_z < 0:
        raise ValueError(
            f"抬笔 Z 不能为负: lift_z={lift_z}（输出所有 Z ≥ 0）")
    lines = ["G90", "G21"]
    lines.append(f"G0 Z{_fmt(safe_z)} F{_fmt(feed_z)}")
    cur: tuple[float, float] | None = None
    for p in paths:
        if not p:
            continue
        if cur is None or p[0] != cur:
            lines.append(f"G0 X{_fmt(p[0][0])} Y{_fmt(p[0][1])} F{_fmt(feed_xy)}")
        for _ in range(passes):
            lines.append(f"G1 Z{_fmt(cut_down_z)} F{_fmt(feed_z)}")  # 下压
            for x, y in p:
                lines.append(f"G1 X{_fmt(x)} Y{_fmt(y)} F{_fmt(feed_xy)}")
            lines.append(f"G0 Z{_fmt(lift_z)} F{_fmt(feed_z)}")  # 段间抬离（低抬）
        cur = p[-1]
    lines.append("M400")
    return lines


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def cut_z_for_depth(touch_z: float, depth: float) -> float:
    """裁刀下压的绝对 Z = 触纸 Z − 下压深度。

    P1b 语义（用户决策）：刀尖触纸的绝对 Z（cut_touch_z，由标定向导测得）
    减去相对纸面下压深度 depth → 得到下压到的绝对机器 Z。
    算术面断言（与 profile 具体值无关）：depth ≥ 0 且结果 ≥ 0，否则 ValueError
    —— touch_z < depth 会产负 Z，与「Z 永不为负」不变量冲突，编译期拒绝。
    """
    if depth < 0:
        raise ValueError(f"下压深度不能为负: {depth}")
    down = touch_z - depth
    if down < 0:
        raise ValueError(
            f"下压后 Z 为负（touch_z={touch_z}, depth={depth}）：Z 永不为负")
    return down


# ---------------------------------------------------------------------------
# JobSpec / compile_job —— 编译流水线（阶段 1，纯逻辑零 Qt，蓝图 §2.2/§3.1）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Placement:
    """内容放置：preserve=直用（版面坐标即工件坐标）；anchor=九宫格归位。

    载入判定（``main_window._placement_for_meta``）：SVG 根元素 width/height
    **均**声明 210×210（两维都等于 BED_W/BED_H，缺一不可）才 preserve，
    否则 anchor@bl→(0,0)。
    """

    mode: Literal["anchor", "preserve"] = "anchor"
    anchor: str = "bl"
    target: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True)
class OptParams:
    """轨迹优化：dedup=重叠去重（容差=笔尖半径）；sort=NN 排序（False=源序直通）。"""

    dedup: bool = False
    sort: bool = False
    pen_diameter_mm: float = 0.5


@dataclass(frozen=True)
class MotionParams:
    """运动参数。travel_lift_mm 属本类（不属于 ZMap），域 ≥ 0（负抬离量会
    发出负 Z，compile/emitter 双双拒）；lift_z 由 compile 计算。"""

    feed_xy: float = _DEFAULT_XY_FEED
    feed_z: float = _DEFAULT_Z_FEED
    travel_lift_mm: float = 5.0
    passes: int = 1


@dataclass(frozen=True)
class ZMap:
    """触纸 Z → 机器绝对 Z 映射（§2.1）：抬笔=safe_z；落笔/下压=down_z。

    写字落笔 down_z = pen_down_z；裁刀下压 down_z = cut_z_for_depth(
    cut_touch_z, depth)；空跑 down_z := safe_z（由 compile_job 构造）。
    """

    safe_z: float
    down_z: float


@dataclass(frozen=True)
class Material:
    """材料：纸基准角铺在机器 (0,0)（与工件原点无关）；margin=安全边距。"""

    w: float
    h: float
    margin: float = 0.0


@dataclass(frozen=True)
class JobSpec:
    """一次作业的完整参数（内存唯一源，§3.1）。

    ``paths_paper`` = 工件 mm y-up、**未平移**、**保持源顺序**；``zmap`` 为
    None 表示 Z 未标定（编译产物 runnable=False，禁执行）。
    """

    paths_paper: list
    source_name: str = ""
    tool: Literal["pen", "knife"] = "pen"
    placement: Placement = Placement()
    opt: OptParams = OptParams()
    motion: MotionParams = MotionParams()
    zmap: ZMap | None = None
    material: Material = Material(BED_W, BED_H)
    work_origin: tuple[float, float] | None = None


@dataclass(frozen=True)
class CompiledJob:
    """编译产物：``lines`` = 将发送字节流（唯一真源）；``segments`` = 同一份
    lines 的 parse_lines 回读（预览吃它）；``bounds`` = 同源越界报告。
    """

    lines: tuple[str, ...]
    segments: tuple[Segment, ...]
    bounds: BoundsReport
    meta: dict

    @property
    def runnable(self) -> bool:
        """可执行 = 越界全过 且 Z 标定齐（执行按钮绑此属性）。"""
        return self.bounds.ok and bool(self.meta.get("calibrated", True))


@dataclass(frozen=True)
class BoundsViolation:
    """一个越界点及其触犯的框名（行程 / 可用区 / 笔径）。"""

    path_idx: int
    point: tuple[float, float]
    frame_name: str


@dataclass(frozen=True)
class BoundsReport:
    """越界预检报告：violations 空 = 全过；judge = 判定区（预览画框用）。"""

    violations: tuple[BoundsViolation, ...]
    judge: tuple[float, float, float, float]

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def messages(self) -> list[str]:
        return [
            f"路径#{v.path_idx} 越界({v.frame_name}): "
            f"({v.point[0]:g},{v.point[1]:g})"
            for v in self.violations
        ]


class JobBoundsError(Exception):
    """strict=True 且越界：携带 report（预览画违规点）。"""

    def __init__(self, report: BoundsReport):
        super().__init__("；".join(report.messages) or "图形越界")
        self.report = report


def check_bounds_v2(
    paths: list[list[tuple[float, float]]],
    *,
    travel: tuple[float, float],
    material: tuple[float, float],
    margin: float = 0.0,
    pen_radius: float = 0.0,
) -> BoundsReport:
    """越界预检 v2：全在**机器坐标**判定（work_origin 平移之后调用）。

    - 行程框 = 机器 (0,0,travel)；材料框 = 机器 (0,0,material)（纸角铺在
      机器 (0,0)，与工件原点无关，§5）。
    - 可用区远端 = ``min(travel, material) − margin``（逐维先取 min 再减
      边距；近端恒 0）；判定区 = 可用区再内缩 pen_radius（只缩远端）。
      **A4 例**：材料 210×297、行程 210×210、margin=m、pen_radius=r →
      可用区远端 (210−m, 210−m)（297 一维被行程 min 成 210），
      判定区 (0, 0, 210−m−r, 210−m−r)。
    - pen_radius 只内缩**远端**（check_bounds 语义保持：0 是物理归位端，
      笔心到 0 即可）。
    - 每路径记第一个越界点；触犯顺序命名：行程 → 可用区 → 笔径。
    """
    tw, th = travel
    mw, mh = material
    if tw <= 0 or th <= 0:
        raise ValueError(f"行程须为正: {travel}")
    if mw <= 0 or mh <= 0:
        raise ValueError(f"材料须为正: {material}")
    if margin < 0 or pen_radius < 0:
        raise ValueError(f"margin/pen_radius 不能为负: {margin}, {pen_radius}")
    ux1, uy1 = min(tw, mw) - margin, min(th, mh) - margin  # 可用区远端
    jx1, jy1 = ux1 - pen_radius, uy1 - pen_radius  # 判定区（pen_radius 只缩远端）
    bad: list[BoundsViolation] = []
    for i, p in enumerate(paths):
        if not p:
            continue
        for x, y in p:
            if not (0.0 <= x <= tw and 0.0 <= y <= th):
                bad.append(BoundsViolation(i, (x, y), "行程"))
            elif not (0.0 <= x <= ux1 and 0.0 <= y <= uy1):
                bad.append(BoundsViolation(i, (x, y), "可用区"))
            elif not (0.0 <= x <= jx1 and 0.0 <= y <= jy1):
                bad.append(BoundsViolation(i, (x, y), "笔径"))
            else:
                continue
            break
    return BoundsReport(tuple(bad), (0.0, 0.0, jx1, jy1))


def compile_job(
    spec: JobSpec, *, strict: bool = True, dry_run: bool = False
) -> CompiledJob:
    """编译流水线（§3.1）：``opt.dedup`` →（``opt.sort`` 为真才 NN）→
    placement → ``translate_paths(+work_origin)`` → ``check_bounds_v2`` →
    Z 断言 → ``gcode_for_*`` → ``parse_lines`` 回读。

    - work_origin 在编译期并入 lines（纯平移，不发 G92）；改原点 = 重跑本流水线。
    - Z 断言：real 模式 ``safe_z > down_z ≥ 0``（严格）；``dry_run=True`` 用
      ``ZMap(safe_z, down_z=safe_z)``（断言 ``down == safe``，emitter 允许
      相等 = 不下压空跑）；裁刀下压 ``cut_down_z ≥ 0``（= zmap.down_z；
      touch_z < depth 由 cut_z_for_depth 拒绝）。**输出所有 Z ≥ 0** —— 含
      派生 ``lift_z = min(safe_z, down_z + travel_lift_mm)`` 与
      ``travel_lift_mm ≥ 0`` 域校验（guard 对 allow_z+lift_configured 放行
      负 Z，不能当后备，负抬离量在这里拒）。
    - strict=True 越界抛 :class:`JobBoundsError`；strict=False 返回**同一个**
      CompiledJob（report 在 ``.bounds``，执行按钮绑 ``.runnable``）。
    - zmap=None（未标定）：几何照常编译进 ``meta["paths"]``/``.bounds``，
      lines/segments 为空、``runnable=False``（未标定禁执行）。
    """
    if spec.tool not in ("pen", "knife"):
        raise ValueError(f"未知工具: {spec.tool!r}")

    # 1) 去重 / 顺序优化（工件坐标做；sort=False 源序直通）
    if spec.opt.dedup or spec.opt.sort:
        from megapro.gui.opt import dedup_and_optimize

        paths = dedup_and_optimize(
            [list(p) for p in spec.paths_paper],
            dedup=spec.opt.dedup,
            tol=spec.opt.pen_diameter_mm / 2.0,
            optimize=spec.opt.sort,
        )
    else:
        paths = [list(p) for p in spec.paths_paper]

    # 2) 放置
    if spec.placement.mode == "preserve":
        pass
    elif spec.placement.mode == "anchor":
        paths = place_at_anchor(paths, spec.placement.anchor, spec.placement.target)
    else:
        raise ValueError(f"未知放置模式: {spec.placement.mode!r}")

    # 3) 工件原点 = 编译期纯平移
    ox, oy = spec.work_origin if spec.work_origin is not None else (0.0, 0.0)
    paths = translate_paths(paths, ox, oy)

    # 4) 越界预检（机器坐标）
    report = check_bounds_v2(
        paths,
        travel=(BED_W, BED_H),
        material=(spec.material.w, spec.material.h),
        margin=spec.material.margin,
        pen_radius=spec.opt.pen_diameter_mm / 2.0,
    )
    if strict and not report.ok:
        raise JobBoundsError(report)

    # 5) Z 断言 → 6) 发射 → 7) parse_lines 回读
    lines: tuple[str, ...] = ()
    segments: tuple[Segment, ...] = ()
    zm: ZMap | None = None
    lift_z: float | None = None
    if spec.zmap is not None:
        if dry_run:
            zm = ZMap(safe_z=spec.zmap.safe_z, down_z=spec.zmap.safe_z)
            if zm.down_z != zm.safe_z:
                raise ValueError("空跑要求 down_z == safe_z")
            if zm.safe_z < 0:
                raise ValueError(f"safe_z 不能为负: {zm.safe_z}")
        else:
            zm = spec.zmap
            if not zm.safe_z > zm.down_z >= 0:
                raise ValueError(
                    f"real 模式要求 safe_z > down_z ≥ 0"
                    f"（safe_z={zm.safe_z}, down_z={zm.down_z}）")
        if spec.motion.travel_lift_mm < 0:
            raise ValueError(
                f"travel_lift_mm 不能为负: {spec.motion.travel_lift_mm}")
        lift_z = min(zm.safe_z, zm.down_z + spec.motion.travel_lift_mm)
        if lift_z < 0:
            raise ValueError(
                f"抬笔 Z 不能为负: lift_z={lift_z}（输出所有 Z ≥ 0）")
        common = dict(
            feed_xy=spec.motion.feed_xy,
            feed_z=spec.motion.feed_z,
            travel_lift_mm=spec.motion.travel_lift_mm,
        )
        if spec.tool == "pen":
            emitted = gcode_for_drawing(
                paths, pen_down_z=zm.down_z, safe_z=zm.safe_z, **common)
        else:
            emitted = gcode_for_cutting(
                paths, cut_down_z=zm.down_z, safe_z=zm.safe_z,
                passes=max(1, int(spec.motion.passes)), **common)
        lines = tuple(emitted)
        segments = tuple(parse_lines(
            lines, z_down=zm.down_z, z_safe=zm.safe_z, tool=spec.tool))

    meta = {
        "calibrated": spec.zmap is not None,
        "dry_run": dry_run,
        "tool": spec.tool,
        "source_name": spec.source_name,
        "work_origin": (ox, oy),
        "zmap": zm,
        "lift_z": lift_z,
        "paths": paths,
    }
    return CompiledJob(lines, segments, report, meta)

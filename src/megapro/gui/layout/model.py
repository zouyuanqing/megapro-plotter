"""排版文档模型（纯逻辑，无 Qt）。

图元 = 一个「折线集合」+ 变换（位置/缩放/旋转）；文档 = 图元列表 + 床尺寸。
paint 与导出同源于此模型 —— QGraphicsScene 只做展示；导出时把变换拍平进
绝对坐标。

坐标契约（docs/preview-layout-blueprint.md §2.1/§2.2，阶段 2 改写）：
- ``Item.paths`` = **本地纸面 y-up mm**（消灭旧「y 可上可下」歧义）；
  入库归一：内容 bbox 左下 = 本地 (0,0)（:func:`normalize_local`，**只在
  创建/导入入口调用**，model/export 内部不自动跑 —— 直接构造 Item 的用例
  不受影响）。
- ``pos`` = 本地 (0,0) 的页面位置；旋转/缩放支点 = 本地 (0,0)。
- ``transformed_paths`` 数学**不动**（test_gui_layout.py 的 transform golden
  保持）：``rx = x1·cosθ − y1·sinθ + px``，即 y-up 下 +θ = 纸面逆时针。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from megapro.gui.canvas.coords import BED_H, BED_W

__all__ = [
    "BED_W",
    "BED_H",
    "Item",
    "Document",
    "Polyline",
    "normalize_local",
    "flatten_visible",
]

Polyline = list[tuple[float, float]]


@dataclass
class Item:
    """一个排版图元：若干折线（本地纸面 y-up mm）+ 变换。

    约定：paths 坐标为「本地」纸面 y-up 坐标（创建/导入入口经
    :func:`normalize_local` 归一：内容 bbox 左下 = 本地 (0,0)）；
    pos = 本地 (0,0) 的页面(工件)位置；scale = 整体缩放；angle_deg =
    旋转（绕 pos，y-up 下逆时针为正）。
    导出时：本地 → 缩放 → 旋转(绕 pos) → 平移 pos → 页面坐标。
    """

    paths: list[list[tuple[float, float]]] = field(default_factory=list)
    pos: tuple[float, float] = (0.0, 0.0)  # 页面/工件 mm
    scale: float = 1.0
    angle_deg: float = 0.0
    name: str = "item"
    z: float = 0.0  # 层序（大=上）
    locked: bool = False  # 锁定（不可选中/移动）
    visible: bool = True  # 隐藏则导出/显示都跳过
    text_spec: dict | None = None  # 文字源信息（可重编）：见 text_to_svg

    def bbox(self) -> tuple[float, float, float, float]:
        """本地 bbox (x0,y0,x1,y1)（未含变换）。空 → (0,0,0,0)。"""
        xs = [x for p in self.paths for x, _ in p]
        ys = [y for p in self.paths for _, y in p]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def transformed_paths(self) -> list[list[tuple[float, float]]]:
        """把变换(缩放+旋转+平移)拍平进坐标，返回页面/工件坐标的折线。"""
        px, py = self.pos
        s = self.scale
        rad = math.radians(self.angle_deg)
        cos, sin = math.cos(rad), math.sin(rad)
        out: list[list[tuple[float, float]]] = []
        for p in self.paths:
            pts = []
            for x, y in p:
                # 本地(可能带缩放前) → 缩放 → 旋转(绕 pos) → 平移
                x1, y1 = x * s, y * s
                rx = x1 * cos - y1 * sin + px
                ry = x1 * sin + y1 * cos + py
                pts.append((rx, ry))
            out.append(pts)
        return out

    def page_bbox(self) -> tuple[float, float, float, float]:
        """变换后的页面 bbox。"""
        xs = [x for p in self.transformed_paths() for x, _ in p]
        ys = [y for p in self.transformed_paths() for _, y in p]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class Document:
    """排版文档：图元列表 + 床尺寸。原点 (0,0) = 工件原点（床左下）。"""

    items: list[Item] = field(default_factory=list)
    bed_w: float = BED_W
    bed_h: float = BED_H

    def add(self, item: Item) -> None:
        self.items.append(item)

    def remove(self, item: Item) -> None:
        try:
            self.items.remove(item)
        except ValueError:
            pass

    def sorted_items(self) -> list[Item]:
        """按 z 升序（小 z 先画=在下层）。"""
        return sorted(self.items, key=lambda it: it.z)

    def items_visible(self) -> list[Item]:
        """可见图元，按 z 升序。"""
        return [it for it in self.sorted_items() if it.visible]

    def top_z(self) -> float:
        return max((it.z for it in self.items), default=0.0)

    def bottom_z(self) -> float:
        return min((it.z for it in self.items), default=0.0)


def normalize_local(item: Item) -> tuple[float, float]:
    """入库归一：paths 平移使内容 bbox 左下 = 本地 (0,0)，pos 补偿。

    **页面几何严格不变**（纯局部重锚）：``pos' = pos + R(θ)S(s)·(x0, y0)``。
    **只在创建/导入入口调用**（layout_page 的绘制/组导入路径）；model/export
    内部不自动跑 —— 直接构造 Item 的用例不受影响（§2.2）。

    返回平移量 delta=(x0, y0)（= 旧局部坐标系原点在新局部坐标系的位置）。
    """
    x0, y0, _x1, _y1 = item.bbox()
    if x0 == 0.0 and y0 == 0.0:
        return (0.0, 0.0)
    item.paths = [[(x - x0, y - y0) for x, y in p] for p in item.paths]
    s = item.scale
    rad = math.radians(item.angle_deg)
    cos, sin = math.cos(rad), math.sin(rad)
    ox, oy = x0 * s, y0 * s
    item.pos = (
        item.pos[0] + ox * cos - oy * sin,
        item.pos[1] + ox * sin + oy * cos,
    )
    return (x0, y0)


def flatten_visible(doc: Document) -> list[Polyline]:
    """z 升序拍平全部可见图元为绝对页面坐标折线（跳过 <2 点折线）。"""
    out: list[Polyline] = []
    for it in doc.items_visible():
        for p in it.transformed_paths():
            if len(p) >= 2:
                out.append(p)
    return out

"""排版文档模型（纯逻辑，无 Qt）。

图元 = 一个「折线集合」（工件坐标，mm）+ 变换（位置/缩放/旋转）。
文档 = 图元列表 + 床 210×210。paint 与导出同源于此模型 ——
QGraphicsScene 只做展示；导出时把变换拍平进绝对坐标。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "BED_W",
    "BED_H",
    "Item",
    "Document",
]

BED_W = 210.0
BED_H = 210.0


@dataclass
class Item:
    """一个排版图元：若干折线（本地 mm，原点在 item 左下/内容 bbox 原点）+ 变换。

    约定：paths 坐标为「本地」坐标（内容自身，y 可上可下由生成方定）；
    pos = item 在页面(工件)坐标的放置点（对齐到本地 (0,0)）；
    scale = 整体缩放；angle_deg = 旋转（绕 pos，逆时针，用于屏幕）。
    导出时：本地 → 平移 pos → 缩放 → 旋转 → 页面坐标。
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
        import math

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
    """排版文档：图元列表 + 床尺寸。原点 (0,0) = 工件原点。"""

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

"""排版页与机器预览页共享的画布核心。

阶段 1 只含零 Qt 坐标权威 :mod:`megapro.gui.canvas.coords`；
``view_transform``/``paper_scene``/``paper_view``/``rulers``/``items``/
``handles``/``snap``/``undo_cmds`` 在阶段 2 落地（Qt 薄封装）。
"""

from __future__ import annotations

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

__all__ = [
    "BED_W",
    "BED_H",
    "flip_y_scalar",
    "paper_to_svg_ydown",
    "paper_from_svg_ydown",
    "machine_from_paper",
    "paper_from_machine",
    "translate_paths",
    "bbox_of",
    "anchor_point",
    "place_at_anchor",
    "grid_steps",
    "snap",
]

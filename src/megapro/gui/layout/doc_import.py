"""Word/Excel 导入 → 排版图元（纯逻辑 + 一个 Qt 对话框）。

- .docx：python-docx 读段落/表格 → 文字轮廓/单线 + 表格边框线。
- .xlsx：openpyxl 读单元格/合并/列宽/行高 → 表格网格线 + 单元格文字。
- 自适应床面：整体缩放到 fit 210×210（保持比例、居中，:func:`_fit_to_bed`）。
- **入库走组导入契约**（阶段 2，§2.2/§2.3 #7）：生成方保持 y-down 排版
  （段落/表格行序 = y-down 图像序），入库时整组 ``paper_from_svg_ydown`` →
  组级一次 ``place_at_anchor``（保 ``_fit_to_bed`` 后的相对布局）→ 逐 Item
  ``normalize_local`` —— 严禁逐 Item 归位（段落/表格会塌到原点）。Word/Excel
  在纸面视图/导出里因此**正立**（第一段/第一行在上）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6 import QtWidgets

from megapro.gui.layout.model import BED_H, BED_W, Item, normalize_local
from megapro.gui.layout.layout_page import _svg_to_paths, _group_paths_to_paper
from megapro.gui.text_to_svg import (
    find_cjk_font, text_outline_svg, text_singleline_svg,
)

__all__ = ["extract_docx", "extract_xlsx", "wrap_group", "DocImportDialog"]

_DATA_DIR = Path(__file__).resolve().parents[4] / "data"
_MM_PER_PT = 25.4 / 72.0  # 磅 → mm
_DEFAULT_COL_MM = 25.0
_DEFAULT_ROW_MM = 8.0
_MARGIN_MM = 5.0


def _text_paths(text: str, *, mode: str, size_mm: float, font_path=None) -> list:
    if not text.strip():
        return []
    if mode == "single":
        data = _DATA_DIR / "chinese_hershey_heiti.json"
        svg = text_singleline_svg(text, data_path=data, size_mm=size_mm,
                                  fallback_font=font_path or find_cjk_font())
    else:
        svg = text_outline_svg(text, font_path=font_path, size_mm=size_mm)
    return _svg_to_paths(svg)


def _fit_to_bed(paths: list, margin: float = _MARGIN_MM) -> tuple[list, tuple]:
    """整体缩放到 fit 床面（保持比例、居中）。返回 (缩放后 paths, 平移量)。"""
    xs = [x for p in paths for x, y in p]
    ys = [y for p in paths for x, y in p]
    if not xs:
        return paths, (0.0, 0.0)
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if w <= 0 and h <= 0:
        return paths, (0.0, 0.0)
    avail_w = BED_W - 2 * margin
    avail_h = BED_H - 2 * margin
    s = min(avail_w / w if w > 0 else 1e9, avail_h / h if h > 0 else 1e9, 1.0)
    # 缩放并平移使居中
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    out = []
    for p in paths:
        out.append([((x - cx) * s + BED_W / 2, (y - cy) * s + BED_H / 2)
                    for x, y in p])
    return out, (0.0, 0.0)


def extract_docx(path: str | Path, *, mode: str = "outline",
                 size_mm: float = 5.0, font_path=None) -> list[Item]:
    """读 .docx → 排版图元（段落文字 + 表格）。"""
    import docx

    doc = docx.Document(str(path))
    items: list[Item] = []
    # 段落文字：逐段生成，纵向排布
    y = 0.0
    line_h = size_mm * 1.6
    for para in doc.paragraphs:
        text = para.text
        if not text.strip():
            y += line_h * 0.6
            continue
        paths = _text_paths(text, mode=mode, size_mm=size_mm,
                            font_path=font_path)
        if not paths:
            continue
        # 平移到当前 y
        for p in paths:
            for i, (x, yy) in enumerate(p):
                p[i] = (x, yy + y)
        items.append(Item(paths=paths, pos=(0.0, 0.0),
                          name=f"段落:{text[:10]}", z=len(items)))
        y += line_h
    # 表格
    for ti, table in enumerate(doc.tables):
        items.extend(_table_to_items(
            [[cell.text for cell in row.cells] for row in table.rows],
            mode=mode, size_mm=size_mm, font_path=font_path,
            name=f"表{ti+1}"))
    return _finish(items)


def extract_xlsx(path: str | Path, *, mode: str = "outline",
                 size_mm: float = 5.0, font_path=None,
                 sheet: str | None = None) -> list[Item]:
    """读 .xlsx → 排版图元（表格网格 + 单元格文字）。"""
    import openpyxl

    wb = openpyxl.load_workbook(str(path), data_only=True)
    ws = wb[sheet] if sheet else wb.active
    grid = []
    for row in ws.iter_rows(values_only=True):
        grid.append(["" if v is None else str(v) for v in row])
    items = _table_to_items(grid, mode=mode, size_mm=size_mm,
                            font_path=font_path, name="表格")
    return _finish(items)


def _table_to_items(grid: list[list[str]], *, mode: str, size_mm: float,
                    font_path, name: str) -> list[Item]:
    """把二维单元格转成：网格线 + 每格文字。"""
    if not grid:
        return []
    ncol = max(len(r) for r in grid)
    col_w = _DEFAULT_COL_MM
    row_h = _DEFAULT_ROW_MM
    # 网格线
    lines = []
    total_w = ncol * col_w
    total_h = len(grid) * row_h
    for c in range(ncol + 1):
        x = c * col_w
        lines.append([(x, 0.0), (x, total_h)])
    for r in range(len(grid) + 1):
        y = r * row_h
        lines.append([(0.0, y), (total_w, y)])
    items = [Item(paths=lines, pos=(0.0, 0.0), name=f"{name}:网格", z=0)]
    # 单元格文字
    for r, row in enumerate(grid):
        for c, val in enumerate(row):
            if not val.strip():
                continue
            paths = _text_paths(val, mode=mode, size_mm=size_mm,
                                font_path=font_path)
            if not paths:
                continue
            # 缩放到单元格内并放到格子中
            xs = [x for p in paths for x, y in p]
            ys = [y for p in paths for x, y in p]
            tw = (max(xs) - min(xs)) or 1
            th = (max(ys) - min(ys)) or 1
            s = min((col_w * 0.85) / tw, (row_h * 0.8) / th, 1.0)
            ox = c * col_w + (col_w - tw * s) / 2 - min(xs) * s
            oy = r * row_h + (row_h - th * s) / 2 - min(ys) * s
            sp = [[(x * s + ox, y * s + oy) for x, y in p] for p in paths]
            items.append(Item(paths=sp, pos=(0.0, 0.0),
                              name=f"格:{val[:8]}", z=1))
    return items


def _finish(items: list[Item]) -> list[Item]:
    """fit 到床面后按**组导入契约**入库（页面几何保持、行序正立）。

    ① 整组 ``paper_from_svg_ydown``（y-down → 纸面 y-up）；
    ② 组级**一次** ``place_at_anchor``（mc → 床中心：保持 _fit_to_bed 的
       居中语义；严禁逐 Item 归位）；
    ③ 逐 Item ``normalize_local``（局部重锚，页面几何不变）。
    """
    all_paths = [p for it in items for p in it.paths]
    fitted, _ = _fit_to_bed(all_paths)
    i = 0
    for it in items:
        n = len(it.paths)
        it.paths = fitted[i:i + n]
        i += n
    groups = _group_paths_to_paper(
        [it.paths for it in items], anchor="mc",
        target=(BED_W / 2, BED_H / 2))
    for it, paths in zip(items, groups):
        it.paths = paths
        normalize_local(it)
    return items


def wrap_group(items, *, name: str = "组") -> Item:
    """恒等容器包装：把一组 Item 原样收进一个容器（FR-07 / D4）。

    容器恒为**恒等变换**（``paths=[]``、``pos=(0,0)``、``scale=1.0``、
    ``angle_deg=0.0``），``children`` = 原 Item 集（同一批对象，列表拷贝）；
    子项数据零改写 —— 由 FR-01 的「父恒等 ∘ 子」合成数学保证
    ``flatten_visible([容器])`` 与 ``flatten_visible(平铺列表)`` 逐点等价
    （含折线顺序、z 序口径）。

    **组变换不走容器**（v1.2 裁决：扁平场景下容器变换无渲染通路，组移动/缩放/
    旋转 = 叶子集合的一条命令，见 FR-04/FR-05）；``z`` 取 0 且**不参与拍平
    排序**（FR-02）。

    本函数**不改** :func:`_finish` / ``extract_docx`` / ``extract_xlsx`` 的平铺
    返回契约（冻结用例 ``test_gui_doc_import.py`` 依赖顶层名字查找与
    ``len(items) >= 3``）；调用侧接线（``layout_page._add_doc``）随 M2/T7b。
    """
    return Item(paths=[], pos=(0.0, 0.0), scale=1.0, angle_deg=0.0,
                name=name, z=0.0, children=list(items))


class DocImportDialog(QtWidgets.QDialog):
    """Word/Excel 导入对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入 Word / Excel")
        self._items: list[Item] = []
        form = QtWidgets.QFormLayout(self)
        row = QtWidgets.QHBoxLayout()
        self._path = QtWidgets.QLineEdit()
        row.addWidget(self._path)
        btn = QtWidgets.QPushButton("选择…")
        btn.clicked.connect(self._pick)
        row.addWidget(btn)
        form.addRow("文件:", row)
        self._mode = QtWidgets.QComboBox()
        self._mode.addItem("轮廓空心字", "outline")
        self._mode.addItem("单线手写体", "single")
        form.addRow("文字模式:", self._mode)
        self._size = QtWidgets.QDoubleSpinBox()
        self._size.setRange(1, 30)
        self._size.setValue(5.0)
        self._size.setSuffix(" mm")
        form.addRow("字号:", self._size)
        self._status = QtWidgets.QLabel("")
        form.addRow(self._status)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                        | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _pick(self) -> None:
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 Word/Excel", "",
            "Word/Excel (*.docx *.xlsx);;所有文件 (*)")
        if p:
            self._path.setText(p)

    def _on_ok(self) -> None:
        path = self._path.text().strip()
        if not path:
            QtWidgets.QMessageBox.warning(self, "未选文件", "请选择 .docx 或 .xlsx")
            return
        try:
            if path.lower().endswith(".docx"):
                self._items = extract_docx(
                    path, mode=self._mode.currentData(),
                    size_mm=self._size.value())
            elif path.lower().endswith(".xlsx"):
                self._items = extract_xlsx(
                    path, mode=self._mode.currentData(),
                    size_mm=self._size.value())
            else:
                QtWidgets.QMessageBox.warning(
                    self, "不支持", "仅支持 .docx / .xlsx（旧格式请另存为新格式）")
                return
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "导入失败", str(exc))
            return
        if not self._items:
            QtWidgets.QMessageBox.warning(self, "空", "没有可导入的内容")
            return
        self.accept()

    def items(self) -> list[Item]:
        return self._items

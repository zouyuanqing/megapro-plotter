"""A5 回归：递归深度守卫漏一处（``descendants``/``group_items`` 抛 RecursionError）。

背景（A5 实测）：:mod:`model` 的模块 docstring 声称「**所有**递归遍历
（:func:`iter_items` / :func:`iter_flattens` / :meth:`Item.bbox` /
:meth:`Item.transformed_paths`）共享 :data:`MAX_TREE_DEPTH` 上限，越限抛
:class:`ValueError`」—— **这句话是假的**：清单里没有 :meth:`Item.descendants`，
它与 :meth:`Page.group_items` 的成环自含拒绝都直接递归、成环时抛
:class:`RecursionError`（不可诊断），且在**合法无环深树**上也只能存活到
998 层，而其它遍历 65 层就抛了。

实跑（a↔b 成环，16 个入口）：``flatten_visible`` / ``iter_items`` /
``iter_flattens`` / ``iter_ancestors`` / ``iter_leaves`` / ``iter_units`` /
``bbox`` / ``transformed_paths`` / ``top_z`` / ``bottom_z`` /
``sorted_items`` / ``items_visible`` / ``unit_page_bbox`` /
``document_to_svg`` **全部**抛可诊断的 ``ValueError``；唯独
``a.descendants()`` 与 ``doc.group_items([a, b])`` 抛 ``RecursionError``，
栈底帧正是 ``model.py`` 里 ``descendants`` 自身的递归。

**产品路径不可达**：src/ 里构造 ``children`` 的地方只有 ``doc_import.py``
的 ``wrap_group``（``paths=[]``）与 ``layout_page.py`` 的 JSON 反序列化
（JSON 语法无法表达环），且实测无环树上 ``doc.group_items([G, a])`` 确实返回
``None``、拒绝生效。所以这是**契约陈述失真**，不是危险 bug —— 本文件钉的是
「所有递归遍历都抛可诊断 ValueError」这句话变成真的。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from megapro.gui.layout.model import (  # noqa: E402
    MAX_TREE_DEPTH, Document, Item, _own_geometry, iter_ancestors,
    iter_flattens, iter_items, iter_leaves, iter_units, flatten_visible,
    unit_paths,
)
from megapro.gui.layout.export_svg import document_to_svg  # noqa: E402


def _cycle():
    """a ↔ b 成环（产品路径不可达，此处直接构造以钉守卫）。"""
    a = Item(name="a")
    b = Item(name="b")
    a.children.append(b)
    b.children.append(a)
    return a, b


def _deep_chain(n):
    """n 层无环链，返回根。"""
    root = Item(name="n0")
    cur = root
    for i in range(1, n):
        nx = Item(name=f"n{i}")
        cur.children.append(nx)
        cur = nx
    return root


# --- 成环：必须是可诊断的 ValueError，不是 RecursionError -------------------

def test_descendants_on_cycle_raises_value_error():
    """成环时 ``descendants()`` 抛 ``ValueError``（旧行为：``RecursionError``）。"""
    a, b = _cycle()
    with pytest.raises(ValueError) as ei:
        a.descendants()
    assert "深度" in str(ei.value), f"错误信息应可诊断，实际：{ei.value}"


def test_group_items_on_cycle_raises_value_error():
    """成环时 ``group_items`` 抛 ``ValueError``（旧行为：``RecursionError``）。

    成环自含拒绝内部要遍历每个成员的子树，成员自身已在环上 ⇒ 必须靠深度守卫
    拦下。
    """
    a, b = _cycle()
    doc = Document(items=[a])
    with pytest.raises(ValueError):
        doc.group_items([a, b])


@pytest.mark.parametrize("entry", [
    # —— 直接递归的 6 个（源码里自带下探）——
    "iter_items", "iter_ancestors", "iter_flattens",
    "Item.bbox", "Item.transformed_paths", "Item.descendants",
    # —— 派生入口：不自递归，但调用链必过上面 6 个，同样必须可诊断 ——
    "iter_leaves", "iter_units", "iter_units_invisible",
    "_own_geometry", "flatten_visible", "Item.page_bbox",
    "Item.unit_page_bbox", "unit_paths",
    "doc.top_z", "doc.bottom_z", "doc.sorted_items", "doc.items_visible",
    "doc.contains", "doc.contains_anywhere", "doc.page_of",
    "doc.group_items", "doc.remove", "document_to_svg",
])
def test_every_recursive_entry_raises_value_error_on_cycle(entry):
    """成环树上**每个**递归入口都抛 ValueError —— 清单不得再漏项。

    清单分两组：**直接递归的 6 个**与**派生入口**（不自递归但必然过这 6 个）。
    派生那组才是容易漏的 —— 上一轮 A5 的清单只列了 15 个，
    ``page_bbox``/``unit_paths``/``_own_geometry``/``contains_anywhere``/
    ``page_of`` 都没列，而它们在成环树上**确实**会被调用到。

    ⚠ 这份清单是**手写枚举**：新增一个递归遍历时它不会自己知道（那正是
    ``test_no_recursive_function_is_left_unguarded`` 用 AST 兜底的原因）。
    """
    a, b = _cycle()
    probe = Item(name="probe", paths=[[(0.0, 0.0), (1.0, 0.0)]])
    # 只把 a 放顶层：b 只能经 a 的子树抵达，成环子树才真正在遍历路径上
    doc = Document(items=[a, probe])
    _absent = Item(name="absent")  # 不在文档里，迫使 remove 走完整棵树
    call = {
        "iter_items": lambda: list(iter_items(doc.items)),
        "iter_ancestors": lambda: list(iter_ancestors(doc.items)),
        "iter_flattens": lambda: list(iter_flattens(doc.items)),
        "Item.bbox": lambda: a.bbox(),
        "Item.transformed_paths": lambda: a.transformed_paths(),
        "Item.descendants": lambda: a.descendants(),
        "iter_leaves": lambda: list(iter_leaves(doc.items)),
        "iter_units": lambda: list(iter_units(doc.items)),
        "iter_units_invisible": lambda: list(
            iter_units(doc.items, visible_only=False)),
        "_own_geometry": lambda: list(_own_geometry(doc.items)),
        "flatten_visible": lambda: flatten_visible(doc),
        "Item.page_bbox": lambda: a.page_bbox(),
        "Item.unit_page_bbox": lambda: a.unit_page_bbox(()),
        "unit_paths": lambda: unit_paths(a, ()),
        "doc.top_z": lambda: doc.top_z(),
        "doc.bottom_z": lambda: doc.bottom_z(),
        "doc.sorted_items": lambda: doc.sorted_items(),
        "doc.items_visible": lambda: doc.items_visible(),
        "doc.contains": lambda: doc.contains(probe),
        "doc.contains_anywhere": lambda: doc.contains_anywhere(probe),
        "doc.page_of": lambda: doc.page_of(probe),
        "doc.group_items": lambda: doc.group_items([a, probe]),
        # ``remove`` 用**一个不在文档里**的目标：``Page.remove`` 找到目标就
        # 立即 return（删顶层 a 或删 a 的直接子项 b 都会**短路不抛** ——
        # 那是正确行为，不是漏网）。只有目标根本不在文档、必须把整棵树走完
        # 时才会撞上环，那才是守卫要守的路径。
        "doc.remove": lambda: doc.remove(_absent),
        "document_to_svg": lambda: document_to_svg(doc),
    }[entry]
    with pytest.raises(ValueError) as ei:
        call()
    assert "深度" in str(ei.value), f"{entry} 的错误信息不可诊断：{ei.value}"


# --- 合法无环深树：与其它遍历同一上限 ---------------------------------------

def test_descendants_respects_max_tree_depth_on_deep_legal_tree():
    """合法无环深树上 ``descendants()`` 不得再靠 Python 自身递归上限存活。

    旧行为下它能活到 998 层（守卫型入口 ~65 层就抛），即守卫形同虚设。
    """
    root = _deep_chain(MAX_TREE_DEPTH + 5)
    with pytest.raises(ValueError):
        root.descendants()
    # 上限之内则正常返回，长度 = 层数
    ok = _deep_chain(MAX_TREE_DEPTH)
    assert len(ok.descendants()) == MAX_TREE_DEPTH


def test_bbox_and_descendants_agree_on_the_depth_limit():
    """``descendants()`` 与 ``bbox()`` 在同深度的合法树上判定必须一致。"""
    n = MAX_TREE_DEPTH + 5
    for build in (lambda: _deep_chain(n).bbox(),
                  lambda: len(_deep_chain(n).descendants())):
        with pytest.raises(ValueError):
            build()


def test_exact_depth_boundary_is_pinned_per_entry():
    """钉住**逐入口的精确边界** —— 共享守卫谓词 ≠ 共享同一个容差。

    上面两条只断「64 层过、69 层抛」，对 off-by-one 完全失明；而 A5 给
    ``descendants`` 写的 docstring 一度声称「其它遍历 65 层就抛」，实测是半对：
    :func:`iter_items` 家族（``yield from iter_items(it.children, _depth+1)``
    对**空 children 也建**生成器）确实 65 项就抛，而 ``bbox`` /
    ``transformed_paths`` / ``descendants`` 只在确有 children 时下探，
    65 项放行、66 项才抛。差一层是既有实现形状、本轮**未**统一，此处按现状
    钉死；谁将来把 :func:`iter_items` 改成只在有 children 时下探，本用例即红，
    那时 docstring 的数字也要跟着改。
    """
    def raises(fn, n) -> bool:
        try:
            fn(_deep_chain(n))
        except ValueError:
            return True
        return False

    n_ok, n_bad = MAX_TREE_DEPTH + 1, MAX_TREE_DEPTH + 2   # 65 / 66
    # iter_items 家族：早一层 —— 64 项放行、65 项就抛
    assert not raises(lambda r: list(iter_items([r])), MAX_TREE_DEPTH), \
        "iter_items 应在 65 项才抛（64 项放行）"
    assert raises(lambda r: list(iter_items([r])), MAX_TREE_DEPTH + 1)
    # bbox / transformed_paths / descendants：晚一层 —— 65 项放行、66 项才抛
    for name, fn in (("bbox", lambda r: r.bbox()),
                     ("transformed_paths", lambda r: r.transformed_paths()),
                     ("descendants", lambda r: r.descendants())):
        assert not raises(fn, n_ok), f"{name} 应在 66 项才抛（65 项放行）"
        assert raises(fn, n_bad), f"{name} 应在 66 项抛"


# --- 未改动的既有契约：无环树上自含拒绝仍生效 -------------------------------

def test_group_items_still_rejects_self_containing_selection_on_legal_tree():
    """深度守卫不得改变无环树上的自含拒绝语义（仍返回 None，不抛）。"""
    g = Item(name="G")
    kid = Item(paths=[[(0.0, 0.0), (1.0, 0.0)]], name="a")
    g.children.append(kid)
    doc = Document(items=[g])
    assert doc.group_items([g, kid], name="X") is None
    assert [c.name for c in g.children] == ["a"], "拒绝不得有副作用"


# --- 容差：守卫谓词共享，但「早一层/晚一层」是实测事实 -----------------------

def test_depth_tolerance_is_one_layer_apart():
    """``iter_items`` 比其余入口**早一层**抛（模块 docstring:154 的声明）。

    差异来源：``iter_items`` 对**空** children 也建生成器并先 ``_check_depth``；
    其余入口只在**确有** children 时才带 ``_depth+1`` 下探。差一层不影响
    正确性 —— 两者都远低于 CPython 998 层上限，谁也不会先炸成
    ``RecursionError``。但既然写进了 docstring 就要钉住，否则「共享守卫」
    会被误读成「共享同一个阈值」。
    """
    def _first_raise(call, lo=1, hi=400):
        for n in range(lo, hi):
            try:
                call(_deep_chain(n))
            except ValueError:
                return n
            except RecursionError:  # pragma: no cover - 守卫失效才会到这里
                pytest.fail(f"第 {n} 层抛了 RecursionError（守卫失效）")
        return None  # pragma: no cover

    early = _first_raise(lambda r: list(iter_items([r])))
    late = _first_raise(lambda r: r.descendants())
    assert early == MAX_TREE_DEPTH + 1, f"iter_items 应在 {MAX_TREE_DEPTH + 1} 层抛，实测 {early}"
    assert late == MAX_TREE_DEPTH + 2, f"descendants 应在 {MAX_TREE_DEPTH + 2} 层抛，实测 {late}"
    assert late - early == 1, "两者应恰好差一层"


# --- 结构化兜底：AST 判「有没有递归函数漏守卫」 -----------------------------

def test_no_recursive_function_is_left_unguarded():
    """**结构化兜底**：model.py 里凡参与递归的函数都必须（直接或间接）调 ``_check_depth``。

    上一份入口清单是**手写枚举**：今天覆盖 25 个入口，但新增一个递归遍历时
    **没有任何东西会失败** —— 清单不会自己知道有新入口。这正是「断言了会变的
    清单、没断言会出错的事」。本用例改为直接读源码 AST，把契约
    「所有递归遍历共享 ``MAX_TREE_DEPTH`` 守卫」变成机器可判的**结构性质**：

    - AST 取出 model.py 全部函数，建调用图（``self.x()``/``obj.x()``/裸 ``x()``
      一律归一到末段名）；
    - 「参与递归」= 自递归 **或** 位于长度 >1 的强连通分量（互递归）；
    - 「已守卫」= 函数体内出现 ``_check_depth(...)``，**或**它调用的某个同名
      函数已守卫（传递闭包 ⇒ ``iter_leaves`` 经 ``iter_flattens`` 算已守卫）；
    - 断言：参与递归 ⇒ 已守卫。

    新增递归遍历却漏守卫 ⇒ 本用例**立即**失败并点名该函数。
    """
    import ast
    import pathlib

    import megapro.gui.layout.model as model_mod

    src = pathlib.Path(model_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    funcs: dict[str, ast.FunctionDef] = {}

    def _collect(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.setdefault(child.name, child)
            _collect(child)

    _collect(tree)
    assert funcs, "AST 没解析出任何函数 —— 判据失灵，本用例形同虚设"

    def _callee_names(fn) -> set[str]:
        """函数体内调用到的「本模块已知函数」末段名。"""
        out: set[str] = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)  # self.x() / it.x() / Page.x() 一律归一
        return out & set(funcs)

    def _calls_check_depth(fn) -> bool:
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if ((isinstance(f, ast.Name) and f.id == "_check_depth") or
                    (isinstance(f, ast.Attribute) and f.attr == "_check_depth")):
                return True
        return False

    # --- 强连通分量（迭代版 Tarjan，避免测试自身深递归）---
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    scc_of: dict[str, int] = {}
    stack: list[str] = []
    counter = [0]
    scc_count = [0]

    for root in sorted(funcs):
        if root in index:
            continue
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack[root] = True
        work = [(root, iter(sorted(_callee_names(funcs[root]))))]
        while work:
            node, it = work[-1]
            advanced = False
            for succ in it:
                if succ not in index:
                    index[succ] = low[succ] = counter[0]
                    counter[0] += 1
                    stack.append(succ)
                    on_stack[succ] = True
                    work.append((succ, iter(sorted(_callee_names(funcs[succ])))))
                    advanced = True
                    break
                if on_stack.get(succ):
                    low[node] = min(low[node], index[succ])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc_of[w] = scc_count[0]
                    if w == node:
                        break
                scc_count[0] += 1

    scc_size: dict[int, int] = {}
    for s in scc_of.values():
        scc_size[s] = scc_size.get(s, 0) + 1

    def _is_cycle_member(name: str) -> bool:
        """自递归，或位于长度 >1 的强连通分量（互递归）。"""
        return (name in _callee_names(funcs[name]) or
                scc_size.get(scc_of.get(name, -1), 0) > 1)

    memo: dict[str, bool] = {}

    def _guarded(name: str) -> bool:
        if name in memo:
            return memo[name]
        memo[name] = False  # 环上先占位，防无限递归
        res = _calls_check_depth(funcs[name]) or any(
            _guarded(c) for c in _callee_names(funcs[name]))
        memo[name] = res
        return res

    reach: dict[str, bool] = {}

    def _in_recursion(name: str) -> bool:
        """本函数**能到达**某个递归点 ⇒ 它自己也是一条递归遍历入口。

        必须取**传递闭包**，不能只看自己是不是环成员：``iter_flattens`` 自身
        不递归（它把递归委托给内嵌的 ``_walk``），若只判「环成员」就会漏掉
        它 —— 而它恰恰是 :func:`iter_leaves`/:func:`iter_units`/
        :func:`_own_geometry` 的守卫来源。同理 ``iter_leaves`` 自己也不递归。
        """
        if name in reach:
            return reach[name]
        reach[name] = False  # 占位
        res = _is_cycle_member(name) or any(
            _in_recursion(c) for c in _callee_names(funcs[name]))
        reach[name] = res
        return res

    unguarded = [n for n in sorted(funcs)
                 if _in_recursion(n) and not _guarded(n)]
    assert not unguarded, (
        "这些函数参与递归却没有（直接或间接）调用 _check_depth —— 成环时会抛"
        f"不可诊断的 RecursionError：{unguarded}")

    # 反向自检：判据本身必须认得真正的递归点，否则上面等于什么都没检。
    # 5 个**自递归**函数（源码里直接写 `for ch in self.children` /
    # `yield from 本函数`）。
    for must in ("iter_items", "iter_ancestors", "bbox", "transformed_paths",
                 "descendants"):
        assert _is_cycle_member(must), (
            f"AST 判据失灵：{must} 竟未被判为自递归")
        assert _guarded(must), (
            f"AST 判据失灵：{must} 竟未被判为已守卫")
    # ``iter_flattens`` 把递归委托给**内嵌**的 ``_walk`` ⇒ 它自身不是环成员，
    # 只能靠传递闭包计入。把它错归为「自递归」正是本用例要防的判据退化。
    assert not _is_cycle_member("iter_flattens"), (
        "iter_flattens 竟被判为自递归（它其实委托给内嵌的 _walk）")
    assert _is_cycle_member("_walk") and _guarded("_walk"), (
        "内嵌的 _walk 才是 iter_flattens 的真递归点，判据没认出它")
    for delegated in ("iter_flattens", "iter_leaves", "iter_units",
                      "_own_geometry"):
        assert _in_recursion(delegated) and _guarded(delegated), (
            f"AST 判据失灵：{delegated} 委托递归却未被计入")

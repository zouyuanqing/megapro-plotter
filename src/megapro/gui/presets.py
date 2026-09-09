"""配置预设（纯逻辑，无 Qt）：命名 json + 最近预设 + 同步 yaml profile。

预设保存当前作业/机器配置：工具、Z 标定（pen_down_z/safe_z/cut_touch_z）、
工件原点偏移、速度、跳段抬升、笔尖直径、材料、优化开关。
- 存 `presets/<name>.json`；`presets/last.json` 记最近一次保存/加载的预设名。
- 保存时同步把 Z 标定键写回 profile yaml（与标定向导一致）。
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "PRESETS_DIR",
    "DEFAULT_CONFIG",
    "collect_config",
    "apply_config",
    "save_preset",
    "load_preset",
    "list_presets",
    "delete_preset",
    "save_last",
    "load_last",
    "sync_profile_z",
    "preset_z_problems",
]

PRESETS_DIR = Path("presets")  # 相对仓库根（与 profiles/ 并列）
PROFILE_PATH = Path("profiles") / "mega-pro-marlin.yaml"

DEFAULT_CONFIG: dict = {
    "tool": "pen",
    "pen_down_z": None,
    "cut_touch_z": None,
    "safe_z": 30.0,
    "safe_x": 0.0,
    "safe_y": 50.0,
    "origin_x": 0.0,
    "origin_y": 0.0,
    "has_origin": False,
    "feed_xy": 1200.0,
    "feed_z": 300.0,
    "travel_lift_mm": 5.0,
    "pen_diameter_mm": 0.5,
    "material": "A4",
    "material_w": 210.0,
    "material_h": 297.0,
    "material_margin": 5.0,
    "cut_depth_mm": 0.5,
    "opt_dedup": False,
    "opt_sort": True,
}


def _dir(path=None) -> Path:
    p = Path(path) if path else PRESETS_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def collect_config(mw) -> dict:
    """从 MainWindow 收集当前配置。mw 提供各状态/控件。"""
    return {
        "tool": mw._tool,
        "pen_down_z": mw._pen_down_z,
        "cut_touch_z": mw._cut_touch_z,
        "safe_z": mw._safe_z,
        "safe_x": mw._safe_x,
        "safe_y": mw._safe_y,
        "origin_x": mw._work_origin[0] if mw._work_origin else 0.0,
        "origin_y": mw._work_origin[1] if mw._work_origin else 0.0,
        "has_origin": mw._work_origin is not None,
        "feed_xy": mw._feed_xy,
        "feed_z": mw._feed_z,
        "travel_lift_mm": mw._travel_lift_mm,
        "pen_diameter_mm": mw._pen_diameter_mm,
        "material": mw._material,
        "material_w": mw._material_w,
        "material_h": mw._material_h,
        "material_margin": mw._material_margin,
        "cut_depth_mm": mw._cut_depth_mm(),
        "opt_dedup": mw._opt_dedup,
        "opt_sort": mw._opt_sort,
    }


def apply_config(mw, cfg: dict) -> None:
    """把 cfg 应用到 MainWindow 状态/控件。"""
    mw._tool = cfg.get("tool", "pen")
    idx = mw.tool_combo.findData(mw._tool)
    if idx >= 0:
        mw.tool_combo.setCurrentIndex(idx)
    mw._pen_down_z = cfg.get("pen_down_z")
    mw._cut_touch_z = cfg.get("cut_touch_z")
    mw._safe_z = float(cfg.get("safe_z", 30.0))
    mw._safe_x = float(cfg.get("safe_x", 0.0))
    mw._safe_y = float(cfg.get("safe_y", 50.0))
    if cfg.get("has_origin"):
        mw._work_origin = (float(cfg.get("origin_x", 0.0)),
                           float(cfg.get("origin_y", 0.0)))
        mw.origin_label.setText(
            f"工件原点：机器({mw._work_origin[0]:.2f},{mw._work_origin[1]:.2f})")
    else:
        mw._work_origin = None
        mw.origin_label.setText("工件原点：机器(0,0)")
    mw._feed_xy = float(cfg.get("feed_xy", 1200.0))
    mw._feed_z = float(cfg.get("feed_z", 300.0))
    mw._travel_lift_mm = float(cfg.get("travel_lift_mm", 5.0))
    mw._pen_diameter_mm = float(cfg.get("pen_diameter_mm", 0.5))
    mw._material = cfg.get("material", "A4")
    mw._material_w = float(cfg.get("material_w", 210.0))
    mw._material_h = float(cfg.get("material_h", 297.0))
    mw._material_margin = float(cfg.get("material_margin", 5.0))
    mw._opt_dedup = bool(cfg.get("opt_dedup", False))
    mw._opt_sort = bool(cfg.get("opt_sort", True))
    # 同步控件
    for attr, spin in (("_feed_xy", "feed_xy_spin"), ("_feed_z", "feed_z_spin"),
                       ("_travel_lift_mm", "lift_spin")):
        getattr(mw, spin).setValue(getattr(mw, attr))
    mw.pen_diameter_spin.setValue(mw._pen_diameter_mm)
    mw.dedup_cb.setChecked(mw._opt_dedup)
    mw.sort_cb.setChecked(mw._opt_sort)
    # 材料下拉
    for i in range(mw.material_combo.count()):
        if mw.material_combo.itemData(i)[0] == mw._material:
            mw.material_combo.setCurrentIndex(i)
            break
    mw.margin_spin.setValue(mw._material_margin)
    if hasattr(mw, "cut_depth_spin") and "cut_depth_mm" in cfg:
        mw.cut_depth_spin.setValue(float(cfg.get("cut_depth_mm", 0.5)))
    mw._refresh_tool_info()
    mw._on_clear_job()
    mw._append_console(f"已加载预设：{cfg.get('_name','')} "
                       f"({mw._tool}, Z标定{'✓' if mw._pen_down_z else '✗'})")


def preset_z_problems(cfg: dict) -> list[str]:
    """校验预设的 Z 标定是否可疑（<5mm 会撞床）。返回问题描述列表（空=安全）。

    真机实测 pen_down_z≈17 / cut_touch_z≈17.5；<5 明显是错误/早期值。
    """
    out = []
    for key, label in (("pen_down_z", "笔触纸Z"), ("cut_touch_z", "刀触纸Z")):
        v = cfg.get(key)
        if v is not None:
            try:
                if float(v) < 5.0:
                    out.append(f"{label}={v}")
            except (TypeError, ValueError):
                out.append(f"{label}={v!r}(非数)")
    return out


def save_preset(name: str, cfg: dict, *, presets_dir=None, profile_path=None) -> Path:
    """保存命名预设 json；同步 Z 键回 profile。返回文件路径。"""
    safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "preset"
    d = _dir(presets_dir)
    data = dict(cfg)
    data["_name"] = name
    path = d / f"{safe}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    # 同步 profile Z 键（若提供了路径或默认存在）
    try:
        sync_profile_z(cfg, profile_path=profile_path)
    except FileNotFoundError:
        pass
    return path


def load_preset(name: str, *, presets_dir=None) -> dict:
    """读命名预设。name 可为文件名（含/不含 .json）。"""
    d = _dir(presets_dir)
    p = d / (name if name.endswith(".json") else f"{name}.json")
    if not p.exists():
        raise FileNotFoundError(f"预设不存在：{name}")
    return json.loads(p.read_text(encoding="utf-8"))


def list_presets(*, presets_dir=None) -> list[str]:
    """列出所有预设名（不含 last.json）。"""
    d = _dir(presets_dir)
    return sorted(p.stem for p in d.glob("*.json") if p.stem != "last")


def delete_preset(name: str, *, presets_dir=None) -> None:
    d = _dir(presets_dir)
    p = d / (name if name.endswith(".json") else f"{name}.json")
    if p.exists():
        p.unlink()


def save_last(name: str, *, presets_dir=None) -> None:
    d = _dir(presets_dir)
    (d / "last.json").write_text(
        json.dumps({"last": name}, ensure_ascii=False), encoding="utf-8")


def load_last(*, presets_dir=None) -> str | None:
    d = _dir(presets_dir)
    p = d / "last.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("last")
    except (json.JSONDecodeError, OSError):
        return None


def sync_profile_z(cfg: dict, *, profile_path=None) -> None:
    """把 cfg 的 Z 标定键写回 profile yaml（pen_down_z/safe_z/cut_touch_z）。"""
    from megapro.cli.main import _load_profile, _save_profile

    pp = Path(profile_path) if profile_path else PROFILE_PATH
    data = _load_profile(pp) or {}
    for key in ("pen_down_z", "safe_z", "safe_x", "safe_y", "cut_touch_z"):
        if key in cfg and cfg[key] is not None:
            data[key] = float(cfg[key])
    _save_profile(data, pp)

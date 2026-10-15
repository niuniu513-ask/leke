# -*- coding: utf-8 -*-
"""config.ini 的读取与回写。

回写采用「文本行替换」而不是 configparser.write()，
目的是完整保留用户配置文件里的注释、空行和原有顺序。
"""
from __future__ import annotations

import configparser
import os
import shutil
import sys

CONFIG_NAME = "config.ini"
TEMPLATE_NAME = "config_template.ini"


def app_dir() -> str:
    """程序工作目录：打包后为 exe 所在目录，源码运行时为项目根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir() -> str:
    """只读资源目录：打包后为 PyInstaller 解包目录。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", app_dir())
    return app_dir()


def config_path() -> str:
    return os.path.join(app_dir(), CONFIG_NAME)


def ensure_config() -> str:
    """确保 config.ini 存在（不存在则从模板复制）。返回其路径。"""
    path = config_path()
    if not os.path.exists(path):
        for base in (app_dir(), resource_dir()):
            tpl = os.path.join(base, TEMPLATE_NAME)
            if os.path.exists(tpl):
                shutil.copyfile(tpl, path)
                break
    return path


def _parser(path: str | None = None) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser(interpolation=None)
    cp.read(path or config_path(), encoding="utf8")
    return cp


def read(path: str | None = None) -> configparser.RawConfigParser:
    return _parser(path)


def get(section: str, key: str, fallback: str = "") -> str:
    cp = _parser()
    try:
        return cp.get(section, key)
    except Exception:
        return fallback


def get_bool(section: str, key: str, fallback: bool = False) -> bool:
    v = get(section, key, "").strip().lower()
    if not v:
        return fallback
    return v in {"1", "true", "yes", "y", "on"}


def get_int(section: str, key: str, fallback: int = 0) -> int:
    try:
        return int(float(get(section, key, "").strip()))
    except Exception:
        return fallback


def get_float(section: str, key: str, fallback: float = 0.0) -> float:
    try:
        return float(get(section, key, "").strip())
    except Exception:
        return fallback


def _find_section_bounds(lines: list[str], section: str) -> tuple[int, int]:
    """返回 [section_header_index, section_end_index) ；找不到小节返回 (-1, -1)。"""
    head = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if head >= 0:
                return head, i
            if s[1:-1].strip().lower() == section.lower():
                head = i
    if head >= 0:
        return head, len(lines)
    return -1, -1


def update_values(updates: dict[tuple[str, str], str], path: str | None = None) -> list[str]:
    """按 (section, key) -> 新值 批量更新 config.ini。

    - 只替换「该小节内已存在的、未被注释的」key 行，注释与排版原样保留。
    - 小节内不存在该 key 时，追加到小节末尾。
    - 小节不存在时，在文件末尾新建。

    返回被改动的描述列表。
    """
    path = path or config_path()
    ensure_config()
    with open(path, "r", encoding="utf8") as f:
        lines = f.read().splitlines()

    # 按小节分组处理，避免行号在插入后错位
    by_section: dict[str, dict[str, str]] = {}
    for (sec, key), val in updates.items():
        by_section.setdefault(sec, {})[key] = val

    changed: list[str] = []
    # 从后往前处理小节，防止插入导致已处理位置失效
    sections_order = list(by_section.keys())
    positions = []
    for sec in sections_order:
        positions.append((_find_section_bounds(lines, sec), sec))
    positions.sort(key=lambda x: x[0][0], reverse=True)

    for (start, end), sec in positions:
        if start < 0:
            # 新建小节
            lines.append("")
            lines.append(f"[{sec}]")
            for k, v in by_section[sec].items():
                lines.append(f"{k} = {v}")
                changed.append(f"[{sec}] {k} = {v} (新增)")
            continue

        pending = dict(by_section[sec])
        for i in range(start + 1, end):
            s = lines[i].strip()
            if not s or s.startswith(";") or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            k = s.split("=", 1)[0].strip()
            for want in list(pending):
                if want.lower() == k.lower():
                    old = lines[i].split("=", 1)[1].strip()
                    new = pending.pop(want)
                    if old != str(new):
                        lines[i] = f"{k} = {new}"
                        changed.append(f"[{sec}] {k}: {old or '(空)'} → {new}")
                    break
        if pending:
            insert_at = end
            # 插到小节末尾之前（跳过尾部空行）
            while insert_at > start + 1 and not lines[insert_at - 1].strip():
                insert_at -= 1
            new_lines = [f"{k} = {v}" for k, v in pending.items()]
            lines[insert_at:insert_at] = new_lines
            for k, v in pending.items():
                changed.append(f"[{sec}] {k} = {v} (新增)")

    with open(path, "w", encoding="utf8") as f:
        f.write("\n".join(lines) + "\n")
    return changed


def write_course_list(course_ids: list[str], path: str | None = None) -> None:
    update_values({("common", "course_list"): ",".join(course_ids)}, path)


# ------------------------------------------------------------------ 界面偏好
# 这些是界面特有的开关，不属于 config.ini 的配置项，单独存一个 json，避免污染它
UI_FILE = "gui_settings.json"

_UI_DEFAULTS = {
    "skip_finished": True,     # 只处理未完成的任务点
    "verbose_log": False,      # 日志区显示 DEBUG 级
    "auto_scroll": True,
    "geometry": "",
}


def ui_path() -> str:
    return os.path.join(app_dir(), UI_FILE)


def ui_load() -> dict:
    data = dict(_UI_DEFAULTS)
    try:
        import json
        with open(ui_path(), "r", encoding="utf8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update(loaded)
            from gui_app.credential_protection import unprotect
            if isinstance(data.get('kc_token'), str):
                data['kc_token'] = unprotect(data['kc_token'])
    except Exception:
        pass
    return data


def ui_save(patch: dict) -> None:
    data = ui_load()
    data.update(patch or {})
    try:
        import json
        import tempfile
        from gui_app.credential_protection import protect
        if isinstance(data.get('kc_token'), str):
            data['kc_token'] = protect(data['kc_token'])
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=os.path.dirname(ui_path()),
                                         prefix='.settings-', delete=False) as f:
            pending = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.replace(pending, ui_path())
        finally:
            if os.path.exists(pending):
                os.unlink(pending)
    except Exception:
        pass


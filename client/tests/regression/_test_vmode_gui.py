# -*- coding: utf-8 -*-
"""设置页「视频学习模式」：UI 状态 + 配置回写 + 回读 验证。

会临时改写 config.ini 的 video_mode 字段，跑完自动还原。

跑法：.venv\\Scripts\\python.exe tests/regression/_test_vmode_gui.py
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys

os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

from gui_app import store                       # noqa: E402
from gui_app.main_window import MainWindow      # noqa: E402

CFG = store.config_path()
CFG_BAK = os.path.join(ROOT, "_bak_config_vmode.ini")
UIF = store.ui_path()
UIF_BAK = os.path.join(ROOT, "_bak_ui_vmode.json")

FAILS: list[str] = []
PASSES = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {name}" + (f"  {extra}" if extra else ""))
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}" + (f"  {extra}" if extra else ""))


def sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else "-"


def main() -> int:
    app = QApplication.instance() or QApplication([])
    shutil.copy(CFG, CFG_BAK)
    if os.path.exists(UIF):
        shutil.copy(UIF, UIF_BAK)
    before = sha(CFG)

    try:
        win = MainWindow()
        sp = win.settings_page

        print("\n[1] 控件存在 + 默认态")
        check("有 cb_vmode 下拉框", hasattr(sp, "cb_vmode"))
        check("有 lb_vmode 提示标签", hasattr(sp, "lb_vmode"))
        check("两个模式选项", sp.cb_vmode.count() == 2, f"{sp.cb_vmode.count()} 个")
        check("选项值与后端一致",
              sp.cb_vmode.itemData(0) == "fast" and sp.cb_vmode.itemData(1) == "timed")
        sp.cb_vmode.setCurrentIndex(sp.cb_vmode.findData("fast"))
        check("fast 提示里点明「不计学习时长」",
              "学习时长" in sp.lb_vmode.text(), sp.lb_vmode.text()[:40] + "…")
        print("  fast 提示:", sp.lb_vmode.text())

        print("\n[2] 切到 timed，提示要变")
        sp.cb_vmode.setCurrentIndex(sp.cb_vmode.findData("timed"))
        app.processEvents()
        t = sp.lb_vmode.text()
        check("timed 提示变化", "1 秒真实时间" in t, t[:44] + "…")
        check("timed 提示提到从头重播", "重播" in t)
        print("  timed 提示:", t)

        print("\n[3] current_common() 必须带上 video_mode")
        sp.cb_vmode.setCurrentIndex(sp.cb_vmode.findData("timed"))
        c = sp.current_common()
        check("current_common 有 video_mode", c.get("video_mode") == "timed",
              str(c.get("video_mode")))

        print("\n[4] 保存回写 config.ini")
        sp.save()
        raw = store.get("common", "video_mode", "")
        check("config.ini 里 video_mode = timed", raw.strip() == "timed", f"实际 {raw!r}")
        check("原注释未被破坏", "[common]" in open(CFG, encoding="utf8").read())

        print("\n[5] 改回 fast 再存")
        sp.cb_vmode.setCurrentIndex(sp.cb_vmode.findData("fast"))
        sp.save()
        check("config.ini 里 video_mode = fast",
              store.get("common", "video_mode", "").strip() == "fast")

        print("\n[6] 重新构造页面，应当回读 config.ini（而不是默认值）")
        sp.cb_vmode.setCurrentIndex(sp.cb_vmode.findData("timed"))
        sp.save()
        win2 = MainWindow()
        sp2 = win2.settings_page
        check("新建页面回读到 timed",
              sp2.cb_vmode.currentData() == "timed",
              str(sp2.cb_vmode.currentData()))
        check("提示也同步成 timed 文案", "重播" in sp2.lb_vmode.text())

        print("\n[7] 模拟老配置（文件里没有 video_mode）")
        with open(CFG, encoding="utf8") as f:
            txt = f.read()
        lines = [ln for ln in txt.splitlines() if not ln.strip().startswith("video_mode")]
        with open(CFG, "w", encoding="utf8") as f:
            f.write("\n".join(lines) + "\n")
        check("已移除 video_mode 行", "video_mode" not in store.get("common", "video_mode", ""))
        win3 = MainWindow()
        sp3 = win3.settings_page
        check("缺字段时回落 fast（不崩）", sp3.cb_vmode.currentData() == "fast",
              str(sp3.cb_vmode.currentData()))
        check("current_common 仍可用", sp3.current_common().get("video_mode") == "fast")

        print("\n[8] 保存时能把缺失的键补回文件")
        sp3.cb_vmode.setCurrentIndex(sp3.cb_vmode.findData("timed"))
        sp3.save()
        check("补充后写入成功", store.get("common", "video_mode", "").strip() == "timed")
    finally:
        shutil.copy(CFG_BAK, CFG)
        if os.path.exists(UIF_BAK):
            shutil.copy(UIF_BAK, UIF)
        os.remove(CFG_BAK)
        if os.path.exists(UIF_BAK):
            os.remove(UIF_BAK)
        after = sha(CFG)
        print(f"\nconfig.ini 已还原（sha256 {'一致' if after == before else '不一致!'}）")

    print("=" * 62)
    print(f"结果：{PASSES} 通过 / {len(FAILS)} 失败")
    for f in FAILS:
        print("  失败项:", f)
    print("=" * 62)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

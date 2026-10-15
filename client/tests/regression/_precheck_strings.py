# -*- coding: utf-8 -*-
"""改文案前的源码级预检：验包清单里的字符串，源码里还找得到吗？

为什么需要
----------
`_check_exe_consts.py` 只能扫**打包后的 exe**，一轮打包十几分钟。
UI 改文案（删小字、改按钮名）很容易漏掉 WANT_STRINGS 里的对应条目 ——
等到打包完才发现，改一次要重打一次包。本脚本用**同一份清单**先在源码上跑，
几秒钟出结果。

匹配口径与验包器对齐
--------------------
* 先把被拆行的字符串字面量接起来（`"a"` 换行 `"b"` → `"ab"`），
  否则源码里明明写着、只在行间断开，会被误报为缺失；
* 再按裸子串找。所以这里命中 ⇒ 编译进包后大概率也在。

判定
----
只要有一条缺失就非 0 退出。缺失通常意味着两种情况之一：
1. 文案真被删了 —— 那就该同步删/改 WANT_STRINGS 里的条目；
2. 只是改了措辞 —— 把 WANT_STRINGS 更新成新串。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "regression"))

from _check_exe_consts import WANT_MODULES, WANT_STRINGS  # noqa: E402

#: 不参与扫描的目录（测试自身、历史备份、构建产物、资源目录）
SKIP_DIRS = {"tests", "__pycache__", "_bak_v150", "resource", "assets",
             "node_modules", "知识库", ".venv", ".git"}


def collect_sources() -> str:
    chunks: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith("dist")]
        for name in filenames:
            if name.endswith(".py"):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    chunks.append(fh.read())
    return "\n".join(chunks)


def main() -> int:
    raw = collect_sources()
    flat = re.sub(r'"\s*\n\s*"', "", raw)      # 双引号拆行接续
    flat = re.sub(r"'\s*\n\s*'", "", flat)     # 单引号拆行接续

    missing = [s for s in WANT_STRINGS if s not in flat]
    if missing:
        print("[FAIL] 验包清单里这些字符串在源码里找不到了：")
        for s in missing:
            print("        ·", s)
        print("       → 确认是文案改了就同步 WANT_STRINGS，否则补回源码")
    else:
        print(f"[PASS] 验包清单 {len(WANT_STRINGS)} 条字符串全部在源码里")

    own = [m for m in WANT_MODULES if m.split(".")[0] in ("gui_app", "api")]
    gone = [m for m in own if not (
        os.path.exists(os.path.join(ROOT, *m.split(".")) + ".py")
        or os.path.exists(os.path.join(ROOT, *m.split("."), "__init__.py")))]
    if gone:
        print(f"[FAIL] 本地模块文件缺失：{gone}")
    else:
        print(f"[PASS] 本地模块 {len(own)} 条都在")
    return 1 if (missing or gone) else 0


if __name__ == "__main__":
    sys.exit(main())

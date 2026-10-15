# -*- coding: utf-8 -*-
"""平台多账号共享件自测：AccountStore（账号档案）+ AccountBar（账号条）。

这两个件是 U校园 / 雨课堂 多账号并行的共同底座，被两个页面同时依赖，
所以单独锁一份契约测试（不联网、不碰真实用户数据，全程在临时目录里跑）。

跑法：cd chaoxing-main; .venv/Scripts/python.exe tests/regression/_test_platform_accounts.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

from gui_app import store                                        # noqa: E402
from gui_app.platform_accounts import AccountStore, mask, new_id  # noqa: E402
from gui_app.widgets import AccountBar                            # noqa: E402

FAILS: list = []
PASSES = 0

YT_DEFAULTS = {"domain": "buu.yuketang.cn", "cookie": "", "user_id": "",
               "university_id": "", "realname": "", "learning_rate": 12}
UP_DEFAULTS = {"username": "", "password": "", "token_full": "",
               "ai_endpoint": "", "ai_key": "", "ai_model": ""}


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {name}" + (f"  {extra}" if extra else ""))
    else:
        FAILS.append(name + (f"  {extra}" if extra else ""))
        print(f"  [FAIL] {name}" + (f"  {extra}" if extra else ""))


def main() -> int:
    print("=" * 56)
    print("平台多账号共享件 验证")
    print("=" * 56)

    tmp = tempfile.mkdtemp(prefix="_test_platacc_")
    orig_app_dir = store.app_dir
    store.app_dir = lambda: tmp

    app = QApplication.instance() or QApplication(sys.argv)

    try:
        print("\n[1] 空档案：没有任何文件时也能用")
        s = AccountStore("yt_accounts.json", "yt.json", YT_DEFAULTS).load()
        check("空档案 accounts 为空", s.accounts == [])
        check("空档案 active 为空", s.active == "")
        check("空档案 display_name 不炸", s.display_name("") == "")
        check("无账号时 pairs 为空", s.pairs() == [])

        print("\n[2] 增 / 改 / 切 / 删 + 落盘")
        a1 = s.add()
        check("add 自动命名", a1["name"] == "账号 1", a1["name"])
        check("add 后成为当前账号", s.active == a1["id"])
        a2 = s.add("账号 2")
        check("第二个账号命名沿用传入值", a2["name"] == "账号 2")
        check("id 不重复", a1["id"] != a2["id"])
        s.update(a1["id"], cookie="csrftoken=x; sessionid=y", user_id="42")
        check("update 合并字段", s.get(a1["id"])["user_id"] == "42")
        s.mark_logged_in(a1["id"])
        check("mark_logged_in 写 last_login",
              bool(s.get(a1["id"])["last_login"]), s.get(a1["id"])["last_login"])
        s.set_active(a2["id"])
        check("set_active 生效", s.active == a2["id"])
        s.set_active("不存在")
        check("set_active 非法 id 不生效", s.active == a2["id"])

        again = AccountStore("yt_accounts.json", "yt.json", YT_DEFAULTS).load()
        check("重新 load 后账号还在（真的落盘了）", len(again.accounts) == 2)
        check("重新 load 后当前账号还在", again.active == a2["id"])
        check("重新 load 后字段不丢",
              again.get(a1["id"])["cookie"].endswith("sessionid=y"))
        check("pairs 顺序与 accounts 一致",
              [p[0] for p in again.pairs()] == [a1["id"], a2["id"]])

        print("\n[3] 显示名优先级：备注名 > 真实姓名 > 用户名 > 域名 > id")
        s.update(a1["id"], name="", realname="牛同学", username="180")
        check("备注名为空 → 用真实姓名", s.display_name(a1["id"]) == "牛同学")
        s.update(a1["id"], realname="")
        check("真实姓名空 → 用用户名", s.display_name(a1["id"]) == "180")
        s.update(a1["id"], username="")
        check("用户名空 → 用域名", s.display_name(a1["id"]) == "buu.yuketang.cn")
        s.update(a2["id"], name="我的小号")
        check("有备注名就用备注名", s.display_name(a2["id"]) == "我的小号")

        print("\n[4] 删除")
        s.remove(a1["id"])
        check("删除后只剩一个", len(s.accounts) == 1)
        check("删掉的账号 get 返回空 dict", s.get(a1["id"]) == {})
        check("当前账号被删后自动改选", s.active == a2["id"])
        s.remove(a2["id"])
        check("删空后 active 归零", s.active == "" and s.accounts == [])

        print("\n[5] 旧单账号文件自动迁移（升级不丢登录态）")
        legacy = os.path.join(tmp, "up.json")
        with open(legacy, "w", encoding="utf8") as f:
            json.dump({"username": "13800000000", "token_full": "TOKEN-1"}, f)
        up = AccountStore("up_accounts.json", "up.json", UP_DEFAULTS).load()
        check("迁移成一条账号", len(up.accounts) == 1)
        check("迁移后成为当前账号", up.active == up.accounts[0]["id"])
        check("迁移保留了用户名", up.get(up.active)["username"] == "13800000000")
        check("迁移保留了 token", up.get(up.active)["token_full"] == "TOKEN-1")
        check("迁移来源有记录", up.get(up.active).get("migrated_from") == "up.json")
        check("旧文件被改名成 .migrated（不再被重复读）",
              not os.path.exists(legacy) and os.path.exists(legacy + ".migrated"))
        up2 = AccountStore("up_accounts.json", "up.json", UP_DEFAULTS).load()
        check("第二次 load 不会重复迁移", len(up2.accounts) == 1)
        check("迁移结果已落盘", os.path.exists(os.path.join(tmp, "up_accounts.json")))

        print("\n[6] 工具函数")
        check("mask 打码不留中间内容", "secret" not in mask("aaaasecretbbbb"))
        check("mask 短串整体打码", mask("abc") == "******")
        check("new_id 前缀与长度", new_id().startswith("acc_") and len(new_id()) == 14)

        print("\n[7] AccountBar：账号条接线")
        bar = AccountBar()
        seen: list = []
        bar.sig_switch.connect(lambda a: seen.append(("switch", a)))
        bar.sig_add.connect(lambda: seen.append(("add", "")))
        bar.sig_remove.connect(lambda a: seen.append(("remove", a)))
        bar.set_accounts([("A1", "账号 1"), ("A2", "账号 2")], "A1")
        check("chip 数量 = 账号数", len(bar._buttons) == 2, str(len(bar._buttons)))
        check("current 用传入值", bar.current() == "A1")
        check("选中项 checked", bar._buttons["A1"].isChecked()
              and not bar._buttons["A2"].isChecked())
        check("多账号时删除按钮可用", bar.btn_remove.isEnabled())
        bar._buttons["A2"].click()
        check("点 chip 发 sig_switch", ("switch", "A2") in seen, str(seen))
        bar.btn_add.click()
        check("新增按钮发 sig_add", ("add", "") in seen)
        bar.btn_remove.click()
        check("删除按钮发 sig_remove 带当前 id", ("remove", "A1") in seen, str(seen))

        print("\n[8] AccountBar：运行态圆点 + 只有一个账号时不许删")
        bar.set_running({"A2"})
        check("运行中 chip 带圆点", bar._buttons["A2"].text().startswith("●"),
              bar._buttons["A2"].text())
        check("非运行 chip 不带圆点", not bar._buttons["A1"].text().startswith("●"))
        check("运行态属性已设置", bar._buttons["A2"].property("busy") == "true")
        bar.set_running(set())
        check("停止后圆点消失", not bar._buttons["A2"].text().startswith("●"))
        bar.set_accounts([("A1", "账号 1")], "A1")
        check("只剩一个账号时禁止删除（不能把账号删空）",
              not bar.btn_remove.isEnabled())
        bar.set_accounts([], "")
        check("空列表不炸且 current 为空", bar.current() == "" and len(bar._buttons) == 0)

        # v1.5.5：set_accounts 返回 {acc_id: chip}，并新增 chip()/running_ids() 查询口
        made = bar.set_accounts([("A1", "x"), ("A2", "y")], "ZZ")
        check("current 传了非法值时回落到第一个", bar.current() == "A1")
        check("set_accounts 返回 acc_id -> chip 映射",
              set(made.keys()) == {"A1", "A2"} and made["A1"] is bar.chip("A1"))
        check("chip() 按 id 取按钮", bar.chip("A2") is bar._buttons["A2"])
        check("chip() 取不到时返回 None", bar.chip("NOPE") is None)
        bar.set_running({"A1"})
        check("running_ids() 反映运行态", bar.running_ids() == {"A1"},
              str(bar.running_ids()))
        check("显示名原样存在 label 属性里（set_running 不再猜 tooltip）",
              bar.chip("A1").property("label") == "x")
        bar.set_accounts([("A1", "x"), ("A2", "y")], "A2")
        check("current 合法值时保持", bar.current() == "A2")

        print("\n[9] AccountBar：brand 属性可切（平台配色）")
        bar.setProperty("brand", "yuketang")
        check("brand 属性已设置", bar.property("brand") == "yuketang")
    finally:
        store.app_dir = orig_app_dir
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 56)
    print(f"结果：{PASSES} 通过 / {len(FAILS)} 失败")
    for f in FAILS:
        print("  失败:", f)
    print("=" * 56)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

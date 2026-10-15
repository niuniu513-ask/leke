# -*- coding: utf-8 -*-
"""多账号档案。

存放位置：程序目录下的 ``accounts.json``。

```json
{
  "version": 1,
  "active": "default",
  "accounts": [
    {"id": "default", "name": "", "username": "180****", "password": "…",
     "use_cookies": false, "enabled": true, "courses": ["123"], "realname": ""}
  ]
}
```

约定
----
* ``id == "default"`` 的账号是「从 config.ini 迁移过来的老账号」：
  账号密码仍然读写 ``config.ini``、cookie 仍然是 ``cookies.txt``，
  这样老用户升级后一切照旧，不需要重新填一遍。
* 其余账号的账号密码存在 ``accounts.json``，cookie 存在
  ``cookies_<id>.txt``。每个账号一份 cookie，互不覆盖 —— 这是能并行刷课的前提。
* 密码是明文（与 config.ini 一致）。程序不联网上传任何东西。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

from gui_app import store

PROFILE_FILE = "accounts.json"
DEFAULT_ID = "default"

#: 老账号的 cookie 文件名，必须与 api.config.GlobalConst.COOKIES_PATH 一致
LEGACY_COOKIE = "cookies.txt"


def profile_path() -> str:
    return os.path.join(store.app_dir(), PROFILE_FILE)


def cookie_path(acc_id: str) -> str:
    """账号的 cookie 文件路径。

    default 账号固定用 cookies.txt（兼容老用户 / 已有配置）；
    其他账号用 cookies_<id>.txt。
    """
    if not acc_id or acc_id == DEFAULT_ID:
        return os.path.join(store.app_dir(), LEGACY_COOKIE)
    safe = re.sub(r"[^0-9A-Za-z_-]", "", str(acc_id)) or "acc"
    return os.path.join(store.app_dir(), f"cookies_{safe}.txt")


def mask_user(username: str) -> str:
    """手机号脱敏：138****8888；非手机号原样返回（太长则中间省略）。"""
    u = str(username or "").strip()
    if not u:
        return "（未填写）"
    if re.fullmatch(r"\d{11}", u):
        return f"{u[:3]}****{u[-4:]}"
    if len(u) > 14:
        return u[:6] + "…" + u[-4:]
    return u


def new_id() -> str:
    return "acc" + uuid.uuid4().hex[:8]


class ProfileBook:
    """账号列表 + 当前选中账号。"""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.accounts: list[dict] = list(data.get("accounts") or [])
        self.active: str = str(data.get("active") or DEFAULT_ID)

    # ------------------------------------------------------------------ 读写
    @classmethod
    def load(cls) -> "ProfileBook":
        """读取 accounts.json；不存在或损坏时按「从 config.ini 迁移」处理。"""
        path = profile_path()
        data = None
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("accounts"), list):
                    data = loaded
            except Exception:
                data = None

        book = cls(data) if data else cls({})
        book.migrate_if_needed()
        return book

    def save(self) -> None:
        payload = {
            "version": 1,
            "active": self.active,
            "accounts": self.accounts,
        }
        path = profile_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass

    # ------------------------------------------------------------------ 迁移
    def migrate_if_needed(self) -> bool:
        """把 config.ini 里的老账号变成 default 档案（只做一次）。"""
        if any(a.get("id") == DEFAULT_ID for a in self.accounts):
            return False
        username = store.get("common", "username", "")
        password = store.get("common", "password", "")
        use_cookies = store.get_bool("common", "use_cookies", False)
        legacy_courses = [x.strip() for x in
                          store.get("common", "course_list", "").split(",") if x.strip()]
        self.accounts.insert(0, {
            "id": DEFAULT_ID,
            "name": "默认账号",
            "username": username,
            "password": password,
            "use_cookies": use_cookies,
            "enabled": True,
            "courses": legacy_courses,
            "realname": "",
            "last_login": "",
        })
        if not self.active:
            self.active = DEFAULT_ID
        self.save()
        return True

    # ------------------------------------------------------------------ 查询
    def get(self, acc_id: str) -> dict | None:
        for a in self.accounts:
            if a.get("id") == acc_id:
                return a
        return None

    def exists(self, acc_id: str) -> bool:
        return self.get(acc_id) is not None

    def current(self) -> dict | None:
        """当前选中的账号；没有就退回第一个。"""
        a = self.get(self.active)
        if a is None and self.accounts:
            a = self.accounts[0]
            self.active = str(a.get("id") or "")
        return a

    def display_name(self, acc: dict | str) -> str:
        """显示名：优先学习通用户名（登录后拿到），其次自定义备注，最后脱敏账号。"""
        if isinstance(acc, str):
            acc = self.get(acc) or {}
        real = str(acc.get("realname") or "").strip()
        if real:
            return real
        name = str(acc.get("name") or "").strip()
        if name:
            return name
        return mask_user(acc.get("username", ""))

    @staticmethod
    def is_loginable(acc: dict) -> bool:
        """有 cookie 文件，或者有账号密码 —— 才有得登。"""
        if acc.get("use_cookies"):
            return os.path.exists(cookie_path(str(acc.get("id") or "")))
        return bool(acc.get("username")) and bool(acc.get("password"))

    # ------------------------------------------------------------------ 增删改
    def add(self, name: str = "", username: str = "", password: str = "",
            use_cookies: bool = False) -> dict:
        acc = {
            "id": new_id(),
            "name": str(name or "").strip(),
            "username": str(username or "").strip(),
            "password": str(password or ""),
            "use_cookies": bool(use_cookies),
            "enabled": True,
            "courses": [],
            "realname": "",
            "last_login": "",
        }
        self.accounts.append(acc)
        self.save()
        return acc

    def update(self, acc_id: str, **fields) -> dict | None:
        acc = self.get(acc_id)
        if acc is None:
            return None
        # default 账号的账号密码同步回 config.ini，兼容老配置
        sync_keys = ("username", "password", "use_cookies")
        touch_config = (acc_id == DEFAULT_ID
                        and any(k in fields for k in sync_keys))
        for k, v in fields.items():
            if k in ("id",):
                continue
            acc[k] = v
        if touch_config:
            updates = {}
            if "username" in fields:
                updates[("common", "username")] = str(fields["username"] or "")
            if "password" in fields:
                updates[("common", "password")] = str(fields["password"] or "")
            if "use_cookies" in fields:
                updates[("common", "use_cookies")] = (
                    "true" if fields["use_cookies"] else "false")
            if updates:
                try:
                    store.update_values(updates)
                except Exception:
                    pass
        self.save()
        return acc

    def remove(self, acc_id: str, keep_cookie_file: bool = False) -> bool:
        """删除账号。同时删掉它自己的 cookie 文件。

        注意：default 账号的 cookies.txt 不删 —— 它属于老用户的既有配置，
        删掉账号后命令行模式还要用它。
        """
        if not self.exists(acc_id):
            return False
        self.accounts = [a for a in self.accounts if a.get("id") != acc_id]
        if not keep_cookie_file and acc_id != DEFAULT_ID:
            try:
                p = cookie_path(acc_id)
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        try:
            from api.base import SessionManager
            SessionManager.drop_session(acc_id)
        except Exception:
            pass
        if self.active == acc_id:
            self.active = str((self.accounts[0].get("id") if self.accounts else ""))
        self.save()
        return True

    # ------------------------------------------------------------------ 课程
    def courses(self, acc_id: str) -> list[str]:
        acc = self.get(acc_id) or {}
        return [str(x) for x in (acc.get("courses") or [])]

    def set_courses(self, acc_id: str, course_ids: list[str]) -> None:
        acc = self.get(acc_id)
        if acc is None:
            return
        acc["courses"] = [str(x) for x in (course_ids or [])]
        # 课程被取消勾选后，它的章节选择就成了孤儿，顺手清掉
        sel = acc.get("chapter_sel")
        if isinstance(sel, dict) and sel:
            keep = set(acc["courses"])
            for k in [k for k in sel if k not in keep]:
                sel.pop(k, None)
        if acc_id == DEFAULT_ID:
            # 老账号继续写回 config.ini，兼容老配置
            try:
                store.write_course_list(acc["courses"])
            except Exception:
                pass
        self.save()

    # ------------------------------------------------------------------ 章节选择
    # 存储形如 acc["chapter_sel"] = {"<courseId>": ["<pointId>", …]}；
    # 键不存在 / 值为空 = 该课程刷全部章节（默认行为，不往 json 里塞垃圾）。

    def chapters(self, acc_id: str, course_id: str) -> list[str] | None:
        """该账号在该课程勾选的章节 id；None = 未做过选择（刷全部）。"""
        acc = self.get(acc_id) or {}
        sel = acc.get("chapter_sel") or {}
        ids = sel.get(str(course_id))
        if not ids:
            return None
        return [str(x) for x in ids]

    def set_chapters(self, acc_id: str, course_id: str,
                     point_ids: list[str] | None) -> None:
        """保存章节选择；point_ids 为空/None 表示清除选择（恢复刷全部）。"""
        acc = self.get(acc_id)
        if acc is None:
            return
        sel = acc.get("chapter_sel")
        if not isinstance(sel, dict):
            sel = {}
            acc["chapter_sel"] = sel
        key = str(course_id)
        ids = [str(x) for x in (point_ids or []) if str(x).strip()]
        if ids:
            sel[key] = ids
        else:
            sel.pop(key, None)
        self.save()

    # ------------------------------------------------------------------ 状态
    def mark_logged_in(self, acc_id: str, realname: str = "") -> None:
        acc = self.get(acc_id)
        if acc is None:
            return
        acc["last_login"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if realname:
            acc["realname"] = realname
        self.save()

    def mark_logged_out(self, acc_id: str) -> None:
        acc = self.get(acc_id)
        if acc is None:
            return
        acc["realname"] = ""
        self.save()


# ---------------------------------------------------------------------- 快捷函数
def load() -> ProfileBook:
    return ProfileBook.load()


def account_payload(book: ProfileBook, acc_id: str) -> dict:
    """把一个账号整理成「运行期账号描述」，交给 worker 用。"""
    acc = book.get(acc_id) or {}
    return {
        "id": acc_id,
        "name": book.display_name(acc),
        "username": acc.get("username", ""),
        "password": acc.get("password", ""),
        "use_cookies": bool(acc.get("use_cookies", False)),
        "cookie_path": cookie_path(acc_id),
        "courses": book.courses(acc_id),
    }

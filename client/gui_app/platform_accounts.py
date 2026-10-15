# -*- coding: utf-8 -*-
"""平台账号档案（U校园 / 雨课堂共用）。

为什么需要它
------------
学习通那边早就是多账号了（`accounts.json` + `gui_app/profiles.py`），但
U校园和雨课堂最初只支持单账号，各自写一个 `unipus.json` / `yuketang.json`。
要支持"多账号并行"，得先把账号层从"一个 dict"升级成"一份带 active 的档案表"。

两个平台的账号结构不同（U校园是账密/token，雨课堂是域名/cookie），
所以这里只做**共性的那部分**：文件读写、id 生成、增删改查、当前账号、
以及**从旧的单账号文件自动迁移**。平台专属字段由调用方的 `defaults` 决定。

文件格式
--------
```json
{
  "version": 1,
  "active": "acc_1a2b3c",
  "accounts": [
    {"id": "acc_1a2b3c", "name": "账号 1", ...平台专属字段}
  ]
}
```

约定
----
* `id` 一旦生成不再变化（cookie 文件名 / 线程名都依赖它）。
* `name` 是给人看的备注名；没填就按"账号 N"自动编号。
* 老的单账号文件（`unipus.json` / `yuketang.json`）在档案为空时会被
  **迁移**成一条记录并改名成 `.migrated`，保证升级后原来的登录态还在。
* 明文存储，与 `config.ini` / `accounts.json` 一致；程序不上传任何东西。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

from gui_app import store

VERSION = 1


def new_id() -> str:
    """生成账号 id（短、可读、足够唯一）。"""
    return "acc_" + uuid.uuid4().hex[:10]


def mask(value: str, head: int = 5, tail: int = 4) -> str:
    """密钥/cookie 打码。太短就整体打码。"""
    text = str(value or "")
    if len(text) <= head + tail + 2:
        return "******"
    return text[:head] + "******" + text[-tail:]


def safe_suffix(acc_id: str) -> str:
    """把 id 变成能安全当文件名后缀的字符串。"""
    return re.sub(r"[^0-9A-Za-z_-]", "", str(acc_id)) or "acc"


class AccountStore:
    """一份平台账号档案。用法：`store = AccountStore("yuketang_accounts.json", "yuketang.json", DEFAULTS).load()`。"""

    def __init__(self, filename: str, legacy_filename: str = "",
                 defaults: dict | None = None, base_dir: str = ""):
        self.filename = str(filename)
        self.legacy_filename = str(legacy_filename or "")
        self.defaults = dict(defaults or {})
        #: 留空 = 运行时读 store.app_dir()（跟随 exe / 源码目录）。
        #: 显式传入则固定目录 —— 测试**应该**用这个，别去 monkeypatch gui_app.store，
        #: 否则任何构造页面的测试都会迁移开发机上的真实账号文件。
        self.base_dir = str(base_dir or "")
        self._accounts: list[dict] = []
        self._active: str = ""
        self._loaded = False

    # ------------------------------------------------------------------ 路径
    def root(self) -> str:
        return self.base_dir or store.app_dir()

    def path(self) -> str:
        return os.path.join(self.root(), self.filename)

    def legacy_path(self) -> str:
        return (os.path.join(self.root(), self.legacy_filename)
                if self.legacy_filename else "")

    # ------------------------------------------------------------------ 读写
    def load(self) -> "AccountStore":
        if self._loaded:
            return self
        self._loaded = True
        data = None
        try:
            with open(self.path(), "r", encoding="utf8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("accounts"), list):
                data = loaded
        except Exception:
            data = None
        if data is None:
            self._accounts, self._active = [], ""
            self.migrate_legacy()
            return self
        self._accounts = [self._normalize(a) for a in data.get("accounts") or []
                          if isinstance(a, dict)]
        self._active = str(data.get("active") or "")
        self._ensure_active()
        return self

    def save(self) -> None:
        payload = {
            "version": VERSION,
            "active": self._active,
            "accounts": self._accounts,
        }
        path = self.path()
        tmp = path + ".tmp"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    # ------------------------------------------------------------------ 迁移
    def migrate_legacy(self) -> bool:
        """把旧的单账号文件搬成档案里的第一条记录（只做一次）。"""
        if self._accounts or not self.legacy_filename:
            return False
        legacy = self.legacy_path()
        if not os.path.exists(legacy):
            return False
        raw = {}
        try:
            with open(legacy, "r", encoding="utf8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            raw = {}
        acc = self._normalize({"id": new_id(), "name": "默认账号"})
        for key in self.defaults:
            if key in raw:
                acc[key] = raw[key]
        acc["migrated_from"] = self.legacy_filename
        self._accounts = [acc]
        self._active = acc["id"]
        self.save()
        try:
            os.replace(legacy, legacy + ".migrated")
        except Exception:
            pass
        return True

    # ------------------------------------------------------------------ 规范化
    def _normalize(self, acc: dict) -> dict:
        out = dict(self.defaults)
        out.update({k: v for k, v in acc.items() if v is not None})
        out["id"] = str(out.get("id") or new_id())
        out["name"] = str(out.get("name") or "")
        out.setdefault("last_login", "")
        return out

    def _ensure_active(self) -> None:
        ids = [a["id"] for a in self._accounts]
        if self._active not in ids:
            self._active = ids[0] if ids else ""

    def _auto_name(self) -> str:
        used = {str(a.get("name") or "") for a in self._accounts}
        i = 1
        while f"账号 {i}" in used:
            i += 1
        return f"账号 {i}"

    # ------------------------------------------------------------------ 查询
    @property
    def accounts(self) -> list[dict]:
        return self._accounts

    @property
    def active(self) -> str:
        return self._active

    def set_active(self, acc_id: str) -> None:
        if any(a["id"] == acc_id for a in self._accounts):
            self._active = str(acc_id)
            self.save()

    def get(self, acc_id: str) -> dict:
        for a in self._accounts:
            if a["id"] == acc_id:
                return a
        return {}

    def display_name(self, acc_id: str) -> str:
        acc = self.get(acc_id) if isinstance(acc_id, str) else {}
        if not acc:
            return str(acc_id or "")
        # 优先级：备注名 > 真实姓名 > 用户名/域名 > id
        for key in ("name", "realname", "username", "domain"):
            value = str(acc.get(key) or "").strip()
            if value:
                return value
        return acc.get("id", "")

    def pairs(self) -> list[tuple]:
        """[(id, 显示名), …]，界面控件直接用。"""
        return [(a["id"], self.display_name(a["id"])) for a in self._accounts]

    # ------------------------------------------------------------------ 增删改
    def add(self, name: str = "") -> dict:
        acc = self._normalize({"id": new_id(), "name": str(name or "") or self._auto_name()})
        self._accounts.append(acc)
        self._active = acc["id"]
        self.save()
        return acc

    def update(self, acc_id: str, **fields) -> dict:
        acc = self.get(acc_id)
        if not acc:
            return {}
        acc.update(fields)
        acc["last_login"] = str(fields.get("last_login")
                                or acc.get("last_login") or "")
        self.save()
        return acc

    def mark_logged_in(self, acc_id: str) -> None:
        self.update(acc_id, last_login=time.strftime("%Y-%m-%d %H:%M:%S"))

    def remove(self, acc_id: str) -> None:
        self._accounts = [a for a in self._accounts if a["id"] != acc_id]
        self._ensure_active()
        self.save()

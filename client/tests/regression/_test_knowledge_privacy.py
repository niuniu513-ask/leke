# -*- coding: utf-8 -*-
"""旧 JSON 后端的离线兼容性验证。

1.6.9 生产默认后端已按用户要求改成 CloudCacheDAO。本脚本显式注入
旧 CacheDAO，仅验证历史文件读取与离线组件；云端行为由
tests/test_cloud_knowledge.py 和服务端 test_knowledge_api.py 验证。

为什么单独锁一份
----------------
「知识库会不会把题目传出去」是靠代码结构保证的（没有任何一处把 cache.json
发给远端），但这种保证**很容易被后来的一次顺手改动破坏**（比如加个"云端同步"）。
所以这里把契约写成可执行的断言：

1. 把 socket / requests / httpx 全部换成会抛异常的桩，然后跑完整的
   知识库读写与 TikuLocal 查询路径 —— 全程一次网络调用都不许发生；
2. 静态扫描：只有 api/answer.py 允许读写缓存；api/base.py（刷课路径）
   不许写缓存（随机猜的答案不能污染知识库）、不许碰 notification；
3. 缓存文件必须落在程序目录的「知识库」文件夹里，路径不得指向别处。

跑法：cd chaoxing-main; .venv\\Scripts\\python.exe tests/regression/_test_knowledge_privacy.py
"""
from __future__ import annotations

import glob
import inspect
import json
import os
import shutil
import socket
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

os.environ.pop("QT_QPA_PLATFORM", None)

from api import answer as A                      # noqa: E402
from api.answer import (                          # noqa: E402
    CacheDAO, TikuLocal, get_cache_dao, knowledge_size,
)

FAILS: list[str] = []
PASSES = 0
_ORIG_CWD = os.getcwd()


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print(f"  [PASS] {name}" + (f"  {extra}" if extra else ""))
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}" + (f"  {extra}" if extra else ""))


def reset_singleton() -> None:
    with A._cache_dao_singleton_lock:
        A._cache_dao_singleton = None
        A.CloudCacheDAO = CacheDAO  # 旧文件后端仍离线；生产默认值由云端测试验证。


# ---------------------------------------------------------------- 断网桩
NET_HITS: list[str] = []


class NetworkForbidden(RuntimeError):
    pass


def _boom_factory(label: str):
    def _boom(*_a, **_k):
        NET_HITS.append(label)
        raise NetworkForbidden(f"隐私契约被破坏：知识库路径尝试联网（{label}）")
    return _boom


_SAVED: list = []


def poison_network():
    """把能出网的入口全部换成会抛异常的桩。"""
    NET_HITS.clear()
    import requests
    import httpx

    targets = [
        (socket, "socket"), (socket, "create_connection"), (socket, "getaddrinfo"),
        (requests, "get"), (requests, "post"), (requests, "request"),
        (requests, "head"), (requests, "put"),
        (httpx, "get"), (httpx, "post"), (httpx, "request"), (httpx, "Client"),
    ]
    for obj, attr in targets:
        if hasattr(obj, attr):
            _SAVED.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, _boom_factory(f"{getattr(obj, '__name__', '?')}.{attr}"))


def restore_network():
    for obj, attr, original in reversed(_SAVED):
        setattr(obj, attr, original)
    _SAVED.clear()


CONF = {
    'provider': 'TikuLocal',
    'submit': 'false',
    'cover_rate': '0.8',
    'true_list': '正确,对',
    'false_list': '错误,错',
    'delay': '0',
}


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="kb_privacy_")
    try:
        work = os.path.join(tmp, "app")
        os.makedirs(work)
        os.chdir(work)
        reset_singleton()

        print("\n[1] 断网状态下，知识库读写照常工作")
        poison_network()
        try:
            dao = get_cache_dao()
            check("get_cache_dao() 可用", isinstance(dao, CacheDAO))
            check("缓存文件已建立", os.path.isfile(os.path.join("知识库", "cache.json")))
            dao.add_cache("隐私测试题1", "正确")
            dao.add_cache("隐私测试题2", "错误")
            check("写入后立即命中", dao.get_cache("隐私测试题1") == "正确")
            check("knowledge_size() 可读", knowledge_size() >= 2, str(knowledge_size()))
            with open(os.path.join("知识库", "cache.json"), encoding="utf8") as fp:
                on_disk = json.load(fp)
            check("两条都落到磁盘", len(on_disk) >= 2, str(len(on_disk)))
        finally:
            restore_network()
        check("全程零网络调用", NET_HITS == [], str(NET_HITS))

        print("\n[2] TikuLocal 纯离线：命中/未命中都不出网")
        poison_network()
        try:
            tl = TikuLocal(config_path="config.ini")
            tl.config_set(CONF)
            tl.init_tiku()
            check("TikuLocal 未被禁用", not tl.DISABLE)
            miss = tl.query({"title": "从没见过的题", "type": "judgement"})
            check("未命中返回 None（不联网求助）", miss is None, str(miss))
            hit = tl.query({"title": "隐私测试题2", "type": "judgement"})
            check("命中返回本地答案", hit == "错误", str(hit))
            batch = tl.query_all([
                {"title": "隐私测试题1", "type": "judgement"},
                {"title": "又一道没见过的题", "type": "judgement"},
            ])
            check("批量查询：命中给答案、未命中给 None",
                  batch == ["正确", None], str(batch))
            check("_query 恒返回 None（离线实现）",
                  tl._query({"title": "x", "type": "judgement"}) is None)
        finally:
            restore_network()
        check("TikuLocal 全程零网络调用", NET_HITS == [], str(NET_HITS))

        print("\n[3] 静态契约：只有 api/answer.py 允许碰缓存")
        py_files = [p for p in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True)
                    if ".venv" not in p and "__pycache__" not in p]
        cache_writers = []
        for path in py_files:
            try:
                src = open(path, encoding="utf8").read()
            except Exception:
                continue
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            if rel.startswith("tests/"):
                continue
            if "add_cache(" in src or "_read_cache(" in src or "get_cache_dao(" in src:
                cache_writers.append(rel)
        # api/unipus/answer_policy.py 也走 api.answer.get_cache_dao() 这个唯一入口
        # （U校园答题策略：命中缓存直接用、答完回落写回），不是自己开文件读写。
        check("缓存读写只出现在 api/answer.py（+ 三个只经 CacheDAO 的调用方）",
              sorted(cache_writers) == ["api/answer.py", "api/unipus/answer_policy.py",
                                        "gui_app/app.py", "gui_app/page_settings.py"],
              str(sorted(cache_writers)))

        base_src = open(os.path.join(ROOT, "api", "base.py"), encoding="utf8").read()
        check("api/base.py 不写知识库（随机猜的答案不污染缓存）",
              "add_cache" not in base_src)
        check("api/base.py 不引用 notification（GUI 刷课不发外部通知）",
              "notification" not in base_src.lower() and "Notification" not in base_src)

        dao_src = inspect.getsource(CacheDAO)
        check("CacheDAO 源码不含任何出网调用",
              not any(t in dao_src for t in
                      ("requests", "httpx", "urlopen", "openai", "socket", "urllib")),
              "命中： " + str([t for t in ("requests", "httpx", "urlopen", "openai", "socket", "urllib")
                               if t in dao_src]))

        answer_src = open(os.path.join(ROOT, "api", "answer.py"), encoding="utf8").read()
        check("api/answer.py 里没有上传缓存的动作（upload / files= / multipart）",
              not any(t in answer_src for t in ("upload", "files=", "multipart")))
        check("缓存文件名固定为 cache.json 且没有导出接口",
              CacheDAO.DEFAULT_CACHE_FILE == "cache.json"
              and not hasattr(CacheDAO, "export") and not hasattr(CacheDAO, "upload"))

        print("\n[4] 缓存位置固定在程序目录的「知识库」文件夹")
        dao2 = get_cache_dao()
        check("父目录名 = 知识库", dao2.cache_file.parent.name == "知识库",
              str(dao2.cache_file))
        check("路径是程序目录下的相对路径（不指向别处）",
              not dao2.cache_file.is_absolute(), str(dao2.cache_file))
        check("缓存在当前工作目录内",
              os.path.abspath(str(dao2.cache_file)).startswith(os.path.abspath(work)),
              os.path.abspath(str(dao2.cache_file)))

        print("\n[5] 旧缓存迁移也只在本机搬文件")
        os.chdir(tmp)
        reset_singleton()
        legacy = os.path.join(tmp, "cache.json")
        with open(legacy, "w", encoding="utf8") as fp:
            json.dump({"老题": "正确"}, fp, ensure_ascii=False)
        poison_network()
        try:
            d3 = get_cache_dao()
            check("旧 cache.json 被搬进知识库文件夹",
                  os.path.isfile(os.path.join("知识库", "cache.json")))
            check("迁移保留内容", d3.get_cache("老题") == "正确")
            check("旧文件已不在原处", not os.path.exists(legacy))
        finally:
            restore_network()
        check("迁移过程零网络调用", NET_HITS == [], str(NET_HITS))
    finally:
        os.chdir(_ORIG_CWD)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 56)
    print(f"结果：{PASSES} 通过 / {len(FAILS)} 失败")
    for f in FAILS:
        print("  失败:", f)
    print("=" * 56)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

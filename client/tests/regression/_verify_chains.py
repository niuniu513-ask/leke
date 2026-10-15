# -*- coding: utf-8 -*-
"""全链路真机连通性验证（源码级，不依赖 exe）。

逐项探测所有出网链路在「强制直连」下的真实状态。
跑法：cd chaoxing-main; .venv\\Scripts\\python.exe tests/regression/_verify_chains.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # tests/regression
_ROOT = os.path.dirname(os.path.dirname(_HERE))             # 项目根
sys.path.insert(0, _ROOT)

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS" if ok else "FAIL") + f"  {name}" + (f"  | {detail}" if detail else ""))
    sys.stdout.flush()


def main() -> int:
    # ---- 0. 源码级：selenium 全套可导入 ----
    try:
        from selenium.webdriver.edge.options import Options as _EdgeOpts
        from selenium.webdriver.edge.service import Service as _EdgeSvc
        check("selenium.webdriver.edge.options 可导入", True)
    except Exception as e:  # noqa: BLE001
        check("selenium.webdriver.edge.options 可导入", False, repr(e))

    # ---- 1. api/_net 直连出口 ----
    try:
        from api import _net
        r = _net.send("GET", "https://www.baidu.com", timeout=10)
        check("_net 直连出口（百度探测）", r.status_code == 200,
              f"status={r.status_code}")
    except Exception as e:  # noqa: BLE001
        check("_net 直连出口（百度探测）", False, repr(e))

    # ---- 2. 乐课服务端 ----
    try:
        from api import kc_client
        base = kc_client._server()
        r = _net.send("GET", base + "/api/v1/health", timeout=10)
        check("乐课服务端 health", r.status_code == 200,
              f"{base} status={r.status_code}")
    except Exception as e:  # noqa: BLE001
        check("乐课服务端 health", False, repr(e))

    # ---- 3. 题库 TikuAdapter（自建聚合，直连 + 入口 key）----
    try:
        from api.answer import ADAPTER_ENTRY_KEY
        r = _net.send(
            "POST",
            "https://kc-api.lifedays.site/tiku/adapter-service/search",
            json={"question": "测试连通性", "options": []},
            headers={"X-Leke-Key": ADAPTER_ENTRY_KEY},
            timeout=15,
        )
        body = getattr(r, "text", "")[:120]
        check("题库 TikuAdapter 直连", getattr(r, "status_code", 0) == 200,
              f"status={getattr(r, 'status_code', '?')} body={body!r}")
    except Exception as e:  # noqa: BLE001
        check("题库 TikuAdapter 直连", False, repr(e))

    # ---- 4. 超星（SessionManager 直连会话）----
    try:
        from api.base import SessionManager
        s = SessionManager.build_session()
        r = s.get("https://www.chaoxing.com/", timeout=15)
        check("超星站点连通（直连会话）", r.status_code == 200,
              f"status={r.status_code}")
    except Exception as e:  # noqa: BLE001
        check("超星站点连通（直连会话）", False, repr(e))

    # ---- 5. 雨课堂（yuketang client _send 直连）----
    try:
        from api.yuketang.client import _send
        r = _send("GET", "https://www.yuketang.cn/", timeout=15)
        check("雨课堂站点连通", r.status_code in (200, 302),
              f"status={getattr(r, 'status_code', '?')}")
    except Exception as e:  # noqa: BLE001
        check("雨课堂站点连通", False, repr(e))

    # ---- 6. 智慧树（client 构造时建立 trust_env=False 会话）----
    try:
        import requests as _rq
        s2 = _rq.Session()
        s2.trust_env = False
        r = s2.get("https://www.zhihuishu.com/", timeout=15)
        check("智慧树站点连通（trust_env=False）", r.status_code in (200, 302),
              f"status={r.status_code}")
    except Exception as e:  # noqa: BLE001
        check("智慧树站点连通（trust_env=False）", False, repr(e))

    # ---- 7. OpenAI SDK（未配代理时 trust_env=False 的 httpx client）----
    try:
        import httpx
        c = httpx.Client(trust_env=False)
        r = c.get("https://www.baidu.com", timeout=10)
        c.close()
        check("httpx trust_env=False 出网", r.status_code == 200,
              f"status={r.status_code}")
    except Exception as e:  # noqa: BLE001
        check("httpx trust_env=False 出网", False, repr(e))

    # ---- 汇总 ----
    fails = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n合计 {len(RESULTS)} 项，失败 {len(fails)} 项"
          + (f"：{', '.join(fails)}" if fails else ""))
    sys.stdout.flush()
    return 0 if not fails else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)

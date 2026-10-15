# -*- coding: utf-8 -*-
"""AI 答题链路真机测试：用用户真实 config.ini 跑生产 query_all。

验证：TikuAdapter（免费库）→ TikuGo → TikuLocal → AI(DeepSeek) 四级回退，
其中 AI 用真实 key 真实出答案。
跑法：cd chaoxing-main; .venv\\Scripts\\python.exe tests/regression/_test_ai_live.py
"""
from __future__ import annotations

import configparser
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _ROOT)

USER_CONFIG = r"D:\学习通\config.ini"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(("PASS" if ok else "FAIL") + f"  {name}" + (f"  | {detail}" if detail else ""))
    sys.stdout.flush()


def main() -> int:
    cp = configparser.ConfigParser()
    cp.read(USER_CONFIG, encoding="utf-8")
    conf = dict(cp["tiku"])

    from api.answer import Tiku

    tiku = Tiku.get_tiku_from_config(config=conf)
    check("题库链构建", tiku is not None and not tiku.DISABLE,
          type(tiku).__name__)
    # ⚠ get_tiku_from_config 不调 init_tiku（生产在 worker.py 补调），脚本必须自己补
    tiku.init_tiku()

    # 各 provider 的就绪状态（AI 是否被 _ready_check 放行）
    for p in getattr(tiku, "providers", []) or [tiku]:
        ready = p._ready_check()
        check(f"provider {p.__class__.__name__} 就绪", not ready, ready or "OK")

    # 真实搜题：第 1 题免费库应能命中；第 2 题大概率走 AI 兜底
    questions = [
        {"id": "q1", "title": "Excel中求和的函数是",
         "options": "A.AVERAGE\nB.SUM\nC.MAX\nD.COUNT", "type": "single"},
        {"id": "q2", "title": "泰勒公式中拉格朗日余项的一般形式是什么",
         "options": "A.f(n+1)(ξ)/(n+1)!(x-x0)^(n+1)\nB.f(n)(x)/n!\nC.f'(ξ)(x-x0)\nD.0",
         "type": "single"},
    ]
    answers = tiku.query_all(questions, query_delay=1.0)
    for i, (q, a) in enumerate(zip(questions, answers), 1):
        check(f"第 {i} 题拿到答案", bool(a), f"{q['title'][:18]}… -> {a!r}")

    # 单独调用配置的 AI，不能把免费题库的命中误报为 AI 成功。
    from api.answer import AI
    ai = next((p for p in getattr(tiku, "providers", []) or [tiku]
               if isinstance(p, AI) and not p.DISABLE), None)
    ai_answer = None
    if ai is not None:
        ai.set_work_feedback("连通性验证，请独立重新解答本题。")
        try:
            ai_answer = ai.query(dict(questions[1]))
        finally:
            ai.set_work_feedback(None)
    check("AI 独立查询返回答案", bool(ai_answer),
          repr(ai_answer) if ai_answer else "None（AI 未配置或查询失败）")
    return 0 if all(ok for _, ok in RESULTS) else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)

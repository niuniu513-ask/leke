# -*- coding: utf-8 -*-
"""旧文件后端兼容性（显式注入 CacheDAO，生产默认使用云端）验证：
知识库文件夹 / 迁移 / 单例 / 并发写 /
内存副本 / TikuLocal 离线 provider / TikuLocal,AI 组合沉淀闭环 / 共享题库实例 /
设置页预设常量。

跑法：.venv\\Scripts\\python.exe tests/regression/_test_knowledge.py
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/regression -> 项目根
sys.path.insert(0, ROOT)

os.environ.pop("QT_QPA_PLATFORM", None)

from api import answer as A          # noqa: E402
from api.answer import (             # noqa: E402
    CacheDAO, Tiku, TikuFallback, TikuLocal, PROVIDER_REGISTRY,
    get_cache_dao, knowledge_size, knowledge_dir,
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
    """重置 answer 模块的 CacheDAO 单例（cwd 切换后必须重置）。"""
    with A._cache_dao_singleton_lock:
        A._cache_dao_singleton = None
        A.CloudCacheDAO = CacheDAO  # 显式测试旧文件后端与迁移源兼容性，保留懒初始化。


class FakeAI(Tiku):
    """假 AI 题库：记录调用次数，永远答「正确」。"""

    def __init__(self, config_path=None):
        super().__init__(config_path)
        self.name = 'FakeAI'
        self.calls = 0

    def _init_tiku(self):
        pass

    def _query(self, q_info):
        self.calls += 1
        return '正确'

    def _query_all(self, q_list, query_delay=0.0):
        self.calls += len(q_list)
        return ['正确'] * len(q_list)


CONF = {
    'provider': 'TikuLocal,AI',
    'submit': 'false',
    'cover_rate': '0.8',
    'true_list': '正确,对',
    'false_list': '错误,错',
    'delay': '0',
}


def make_qs(prefix: str, n: int) -> list[dict]:
    return [{"title": f"{prefix}第{i}题（1.0分）", "type": "judgement"} for i in range(n)]


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="kb_test_")
    try:
        # ================================================= [1] 基础设施
        print("[1] 知识库文件夹与单例")
        cwd1 = os.path.join(tmp, "a")
        os.makedirs(cwd1)
        os.chdir(cwd1)
        reset_singleton()

        check("knowledge_dir() 名称正确", knowledge_dir().name == "知识库")
        dao = get_cache_dao()
        check("知识库文件夹被创建", os.path.isdir("知识库"))
        check("cache.json 被创建", dao.cache_file.is_file())
        dao2 = get_cache_dao()
        check("get_cache_dao() 单例幂等", dao is dao2)
        check("knowledge_size() 初始为 0", knowledge_size() == 0)

        # ================================================= [2] 旧缓存迁移
        print("[2] 旧根目录 cache.json 迁移")
        cwd2 = os.path.join(tmp, "b")
        os.makedirs(cwd2)
        os.chdir(cwd2)
        reset_singleton()
        legacy = {"老题1": "正确", "老题2": "错误"}
        with open("cache.json", "w", encoding="utf8") as f:
            json.dump(legacy, f, ensure_ascii=False)
        d2 = get_cache_dao()
        check("迁移后知识库/cache.json 存在", os.path.isfile(os.path.join("知识库", "cache.json")))
        check("旧条目1 保留", d2.get_cache("老题1") == "正确")
        check("旧条目2 保留", d2.get_cache("老题2") == "错误")
        check("根目录旧文件已搬走", not os.path.isfile("cache.json"))
        check("knowledge_size() = 2", knowledge_size() == 2)

        # ================================================= [3] 读写与内存副本
        print("[3] 读写与内存副本")
        d2.add_cache("新题A", "正确")
        check("add_cache 后立即 get 命中", d2.get_cache("新题A") == "正确")
        disk = json.load(open(os.path.join("知识库", "cache.json"), encoding="utf8"))
        check("磁盘与内存副本一致", d2._mem is not None and len(d2._mem) == len(disk) == 3)
        check("knowledge_size() 反映新增", knowledge_size() == 3)
        d2.add_cache("新题A", "错误")
        check("同名题覆盖更新", d2.get_cache("新题A") == "错误"
              and json.load(open(os.path.join("知识库", "cache.json"), encoding="utf8"))["新题A"] == "错误")
        # 恢复，方便后续用例计数
        d2.add_cache("新题A", "正确")

        # ================================================= [4] 多线程并发写
        print("[4] 并发写压力")
        N_T, N_Q = 10, 30

        def worker(tid: int):
            for i in range(N_Q):
                d2.add_cache(f"线程{tid}题{i}", "正确")

        ts = [threading.Thread(target=worker, args=(t,)) for t in range(N_T)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        total = knowledge_size()
        check(f"并发写无丢失（{N_T}线程×{N_Q}条）", total == 3 + N_T * N_Q, f"实际 {total}")
        disk2 = json.load(open(os.path.join("知识库", "cache.json"), encoding="utf8"))
        ok_all = all(f"线程{t}题{i}" in disk2 for t in range(N_T) for i in range(0, N_Q, 7))
        check("抽查各线程条目均在盘", ok_all)

        # ================================================= [5] TikuLocal
        print("[5] TikuLocal 离线 provider")
        reset_singleton()
        check("PROVIDER_REGISTRY 注册 TikuLocal", PROVIDER_REGISTRY.get("TikuLocal") is TikuLocal)
        tl = TikuLocal(config_path="config.ini")
        tl.config_set(CONF)
        tl.init_tiku()
        check("TikuLocal 不被禁用", not tl.DISABLE)
        check("旧配置名对应云端题库", tl.name == "云端共享题库")
        check("_query 恒返回 None（纯离线）", tl._query({"title": "x", "type": "judgement"}) is None)
        check("query() 未命中返回 None", tl.query({"title": "没见过的题", "type": "judgement"}) is None)
        get_cache_dao().add_cache("见过的题", "正确")
        check("query() 命中本地知识库", tl.query({"title": "见过的题", "type": "judgement"}) == "正确")

        # ================================================= [6] 组合与沉淀闭环
        print("[6] TikuLocal,AI 组合：本地优先 + AI 兜底 + 答案沉淀")
        fake = FakeAI()
        fake.config_set(CONF)                     # 否则会去读真实 config.ini（脏状态）
        tl2 = TikuLocal(config_path="config.ini")
        tl2.config_set(CONF)                      # 不设会被 _get_conf 判 DISABLE 剔除
        fb = TikuFallback([tl2, fake], config_path="config.ini")
        fb.config_set(CONF)
        fb.init_tiku()
        got_names = [p.__class__.__name__ for p in fb.providers]
        check("fallback 链保留 TikuLocal 与 FakeAI",
              got_names == ["TikuLocal", "FakeAI"], f"链={got_names}")
        qs = make_qs("沉淀", 3)
        r1 = fb.query_all(qs)
        check("首轮全部由 AI 补答", r1 == ["正确"] * 3)
        check("FakeAI 被调用 3 次", fake.calls == 3, f"calls={fake.calls}")
        disk3 = json.load(open(os.path.join("知识库", "cache.json"), encoding="utf8"))
        sunk = sum(1 for k in disk3 if "沉淀第" in k)
        check("AI 答案沉淀进本地知识库", sunk >= 3, f"沉淀 {sunk} 条")

        r2 = fb.query_all(make_qs("沉淀", 3))
        check("次轮全部本地命中（FakeAI 不再被调用）", fake.calls == 3, f"calls={fake.calls}")
        check("次轮结果仍正确", r2 == ["正确"] * 3)

        # 单题路径：基类 query() 在缓存命中时直接返回（上游行为：未命中走 query_all）
        get_cache_dao().add_cache("单题缓存题", "错误")
        r3 = fb.query({"title": "单题缓存题", "type": "judgement"})
        check("单题路径本地命中", r3 == "错误")

        # ================================================= [7] 配置解析与共享实例
        print("[7] get_tiku_from_config 与多账号共享")
        reset_singleton()
        conf_local = dict(CONF, provider="TikuLocal")
        conf_chain = dict(CONF, provider="TikuLocal,AI")
        t_single = Tiku.get_tiku_from_config(conf_local, config_path="config.ini")
        check("provider=TikuLocal 自动追加 AI", isinstance(t_single, TikuFallback)
              and [p.__class__.__name__ for p in t_single.providers] == ['TikuLocal', 'AI'])
        t_chain = Tiku.get_tiku_from_config(conf_chain, config_path="config.ini")
        check("provider=TikuLocal,AI 解析为 TikuFallback", isinstance(t_chain, TikuFallback))
        chain_names = [p.__class__.__name__ for p in t_chain.providers]
        check("链首是 TikuLocal", bool(chain_names) and chain_names[0] == "TikuLocal",
              f"链={chain_names}")

        from gui_app.worker import Runner
        runner = Runner.__new__(Runner)          # 跳过 QObject.__init__
        runner._shared_tiku = None
        runner._shared_tiku_fp = ""
        runner._tiku_lock = threading.Lock()
        s1 = runner._get_shared_tiku(conf_local)
        s2 = runner._get_shared_tiku(dict(conf_local))
        check("同配置两次获取同一实例", s1 is s2)
        s3 = runner._get_shared_tiku(conf_chain)
        check("配置变化自动重建（非同一实例）", s3 is not s1)
        check("重建后实例类型正确", isinstance(s3, TikuFallback))

        # ================================================= [8] 设置页常量
        print("[8] 设置页预设与卡片")
        from gui_app import page_settings as PS
        groups = getattr(PS, "PROVIDER_PRESET_GROUPS", None)
        check("PROVIDER_PRESET_GROUPS 存在且为 3 组", isinstance(groups, list) and len(groups) == 3)
        rec_vals = [v for _, items in groups for _, v in items] if groups else []
        check("推荐组含 TikuLocal,AI", "TikuLocal,AI" in rec_vals)
        check("推荐组含纯离线 TikuLocal", "TikuLocal" in rec_vals)
        all_items = [x for g in (groups or []) for x in g[1]]
        check("远端组含 TikuLike（不推荐标注）",
              any("TikuLike" in v and "不推荐" in lb for lb, v in all_items))
        check("旧 PROVIDER_PRESETS 已移除", not hasattr(PS, "PROVIDER_PRESETS"))
        src_kb = inspect.getsource(PS.SettingsPage)
        check("设置页含云端共享题库卡片", "card_kb" in src_kb and "云端共享题库" in src_kb)
        check("设置页含云端统计刷新", "_refresh_kb_stat" in src_kb and "knowledge_stats" in src_kb)
        check("设置页含旧题库导入", "导入旧本地题库" in src_kb)
        check("AI 一键启用默认写入 TikuLocal,AI", "TikuLocal,AI" in inspect.getsource(PS.SettingsPage.append_ai_provider))
        check("_refresh_groups 常显本地知识库卡片",
              "self.card_kb.setVisible(True)" in inspect.getsource(PS.SettingsPage._refresh_groups))

        # ================================================= [8b] provider 就绪守卫
        print("[8b] provider 就绪守卫：缺 token / 地址时不再白送题干")

        class _NoNet:
            """任何 HTTP 调用都直接炸 —— 守卫没拦住就会在这里暴露出来。"""

            def __getattr__(self, _name):
                def _boom(*_a, **_k):
                    raise AssertionError("守卫失效：仍然向服务端发了请求")
                return _boom

        _orig_requests = A.requests
        A.requests = _NoNet()
        try:
            def _mk(cls, **over):
                conf = dict(CONF)
                conf.update(over)
                inst = cls()
                inst.config_set(conf)
                inst.init_tiku()
                return inst

            yx = _mk(A.TikuYanxi)
            check("言溪 tokens 为空 → 直接停用（不发请求）", yx.DISABLE)
            check("言溪 未就绪原因给出可执行指引",
                  "参数设置" in yx._ready_check() and "token" in yx._ready_check())
            yx3 = A.TikuYanxi()
            yx3.config_set(dict(CONF, tokens="abc"))
            check("言溪 有 tokens 时 _ready_check 通过", yx3._ready_check() == "")

            lk = _mk(A.TikuLike)
            check("LIKE tokens 为空 → 直接停用", lk.DISABLE)

            ad = _mk(A.TikuAdapter)
            check("TikuAdapter url 为空 → 直接停用", ad.DISABLE)

            ai = _mk(A.AI)
            check("AI 缺 key → 直接停用", ai.DISABLE)
            ai2 = A.AI()
            ai2.config_set(dict(CONF, endpoint="https://x/v1", key="k", model="m"))
            check("AI 三件套齐备 → 就绪", ai2._ready_check() == "")

            sf = _mk(A.SiliconFlow)
            check("硅基流动 key 为空 → 直接停用", sf.DISABLE)

            tl = _mk(A.TikuLocal)
            check("TikuLocal 恒可用（纯离线）", not tl.DISABLE)

            check("基类提供 _ready_check 钩子（默认就绪）",
                  'return ""' in inspect.getsource(A.Tiku._ready_check))
            check("TikuFallback 会跳过 DISABLE 的 provider",
                  "provider.DISABLE" in inspect.getsource(A.TikuFallback._query_all))
        finally:
            A.requests = _orig_requests

        # ================================================= [8c] 题面归一化（cache_key）
        print("[8c] 题面归一化：剥题号/【题型】标签/分值，不裸剥数字开头")

        ck_cases = [
            ("1. 关于MySQL的说法", "关于MySQL的说法"),
            ("2、关于索引的说法", "关于索引的说法"),
            ("3）这是第三题", "这是第三题"),
            ("1【单选题】MySQL的默认端口是？", "MySQL的默认端口是？"),
            ("【多选题】题干？ ", "题干？"),
            ("题干（2.0分）", "题干"),
        ]
        check("cache_key 剥题号/标签/分值（正例）",
              all(A.cache_key(s) == w for s, w in ck_cases),
              f"实际={[A.cache_key(s) for s, _ in ck_cases]}")
        check("cache_key 不裸剥数字开头的题干（3NF / 1500米）",
              A.cache_key("3NF是指什么？") == "3NF是指什么？"
              and A.cache_key("1500米跑的规则是？") == "1500米跑的规则是？")
        check("cache_key 幂等",
              all(A.cache_key(A.cache_key(s)) == A.cache_key(s)
                  for s in [s for s, _ in ck_cases] + ["3NF是指什么？", ""]))
        # 回归保护：曾用 ^\d+ 裸剥，把【题型】标签/题号留在题面里发给上游题库
        # （实测 TikuAdapter 对『【单选题】Excel中求和的函数是』直接不命中）
        check("Tiku.query 用 cache_key 归一化题面（不再只剥数字）",
              "cache_key(q_info['title'])" in inspect.getsource(A.Tiku.query))
        check("Tiku.query_all 用 cache_key 归一化题面",
              "cache_key(q['title'])" in inspect.getsource(A.Tiku.query_all))

        # 端到端：带题号+【题型】标签的题面，经归一化后照样命中知识库
        reset_singleton()
        get_cache_dao().add_cache("Excel中求和的函数是", "SUM")
        _tl_e = TikuLocal(config_path="config.ini")
        _tl_e.config_set(CONF)
        _tl_e.init_tiku()
        check("带题号+【题型】标签的题面命中知识库",
              _tl_e.query({"title": "1【单选题】Excel中求和的函数是", "type": "single"}) == "SUM")
        check("纯题面同题同样命中",
              _tl_e.query({"title": "Excel中求和的函数是", "type": "single"}) == "SUM")

        # 设置页保存提示（不阻断保存，只提示「这样配跑起来无效」）
        warn_src = inspect.getsource(PS.SettingsPage._config_warnings)
        check("设置页有「配置隐患」提示方法", "token" in warn_src and "跳过" in warn_src)
        check("题库来源为空也会提示", "题库来源为空" in warn_src)

        # 开跑前预检（主窗口静态方法，与 _ready_check 同源规则）
        from gui_app.main_window import MainWindow as _MW
        check("预检：言溪缺 token 且无兜底 → 提示",
              bool(_MW._tiku_ready_warning({"provider": "TikuYanxi", "tokens": ""})))
        check("预检：有 TikuLocal 兜底 → 不提示",
              _MW._tiku_ready_warning({"provider": "TikuYanxi,TikuLocal", "tokens": ""}) == "")
        check("预检：完全没配题库 → 交给专门的询问",
              _MW._tiku_ready_warning({"provider": ""}) == "")
        check("预检：AI 缺密钥 → 提示",
              bool(_MW._tiku_ready_warning({"provider": "AI", "key": ""})))
        check("预检：AI 密钥齐备 → 不提示",
              _MW._tiku_ready_warning({"provider": "AI", "key": "sk-x"}) == "")

        # ================================================= [9] 环境清理检查
        print("[9] 清理")
        os.chdir(_ORIG_CWD)
        reset_singleton()
        check("恢复工作目录", os.getcwd() == _ORIG_CWD)

    finally:
        os.chdir(_ORIG_CWD)
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"通过 {PASSES} 项，失败 {len(FAILS)} 项")
    if FAILS:
        print("失败清单：")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

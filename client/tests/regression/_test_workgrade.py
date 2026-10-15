# -*- coding: utf-8 -*-
"""章节检测批改解析 / 判定 / 批改回流 回归。

真实场景（2026-10-11 真机刷课发现）：不少课程由教师关闭了「显示答案解析」，
record-detail 页面上**没有正确答案字段**，只有
  「我的答案」 + marking_dui / marking_cuo + 每题 scoreNum。
旧实现靠 '正确答案' in html 判断，会被内联 JS 里的字样骗过，导致：
  ① 每个章节测验都报「答案字段解析失败…建议人工复核」的 WARNING；
  ② 批改回流（harvest）永远拿不到数据 → 知识库沉淀功能形同虚设。

本脚本用真实页面结构做夹具，锁住修复后的行为。
运行：以本目录为工作目录（会建临时「知识库」），必须用 .venv python。
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [!!] {name}  {extra}")


# ---- 真实页面结构夹具（无正确答案字段，只有对错标记 + 选项）----
def _q(qid, num, label, title, opts, my, mark, score):
    opt_html = "".join(
        f'<li class="clearfix" tabindex="0" role="option">'
        f'<i  class="fl">{a}、</i><a href="javascript:void(0)" class="fl" '
        f'style="word-break: break-all;text-decoration: none;" tabindex="-1" '
        f'aria-hidden="true" >{t}</a></li>'
        for a, t in opts
    )
    mark_cls = "marking_dui" if mark is True else ("marking_cuo" if mark is False else "marking_none")
    return (
        f'<div class="TiMu newTiMu ans-cc singleQuesId" data="{qid}" id="question{qid}">'
        f'<div class="Zy_TItle clearfix"><i class="fl" tabindex="0" role="option">{num}</i>'
        f'<div class="clearfix font-cxsecret fontLabel" style="line-height: 1.5; font-size: 14px;padding-right:15px;" '
        f'tabindex="0" role="option"><span class="newZy_TItle">【{label}】</span>{title}</div></div>'
        f'<form action="about:blank" method="post"><ul class="Zy_ulTop qtDetail" tabindex="-1" aria-hidden="true">'
        f'{opt_html}</ul></form>'
        f'<!--新的答案开始--><div class="newAnswerBx"><div class="myAnswerBx marBot16">'
        f'<div class="myAnswer"><span class="answerFont fl">我的答案：</span>'
        f'<div class="fl answerCon">\n                {my}\n            </div><p class="clear"></p></div>'
        f'<div class="answerScore"><div class="CorrectOrNot fl"><span class="{mark_cls}"></span></div>'
        f'<div class="fr newAnswerScore"><span class="scoreNum">{score}</span>分</div></div>'
        f'</div><!--显示答案解析--><!--显示答案解析--></div><!--新的答案结束--></div>'
    )


# 页面里有内联 JS 提到「正确答案」字样（旧实现的误导来源），但没有真的字段
FAKE_JS = '<script>html += "\\#\\#\\# 正确答案：\\n";</script>'

NO_CORRECT_HTML = (
    FAKE_JS
    + _q("405703215", 1, "单选题", "数据库的主要功能是什么？",
         [("A", "存储数据"), ("B", "播放视频"), ("C", "发送邮件"), ("D", "浏览网页")],
         "A", True, "20.0")
    + _q("405703162", 2, "多选题", "在安装MySQL之前，需要做哪些准备工作？",
         [("A", "下载MySQL社区版"), ("B", "配置防火墙规则"), ("C", "安装必要的依赖库"), ("D", "创建数据库目录")],
         "ACD", False, "0.0")
)

# 有正确答案字段的页面变体（旧形态，必须继续支持）
WITH_CORRECT_HTML = (
    '<div class="TiMu newTiMu ans-cc singleQuesId" data="900001" id="question900001">'
    '<div class="Zy_TItle clearfix"><i class="fl" tabindex="0" role="option">1</i>'
    '<div class="clearfix font-cxsecret fontLabel"><span class="newZy_TItle">【单选题】</span>MySQL 属于哪类数据库？</div></div>'
    '<ul class="Zy_ulTop qtDetail"><li class="clearfix"><i  class="fl">A、</i><a href="#">关系型数据库</a></li>'
    '<li class="clearfix"><i  class="fl">B、</i><a href="#">非关系型数据库</a></li></ul>'
    '<div class="newAnswerBx"><div class="myAnswerBx">'
    '<div class="myAnswer"><span class="answerFont fl">我的答案：</span><div class="fl answerCon">A</div></div>'
    '<div class="answerScore"><div class="CorrectOrNot fl"><span class="marking_dui"></span></div>'
    '<div class="fr newAnswerScore"><span class="scoreNum">50.0</span>分</div></div></div>'
    '<div class="myAnswer" style="padding-left:68px"><span class="answerFont fl">正确答案：</span>'
    '<div class="fl answerCon">关系型数据库</div></div>'
    '</div></div>'
)


def main():
    tmp = tempfile.mkdtemp(prefix="leke_workgrade_")
    os.chdir(tmp)
    try:
        from api.base import _parse_work_record_detail, judge_work_detail
        from api.answer import harvest_correct_answers, knowledge_size
        from api import answer as A
        A._cache_dao_singleton = A.CacheDAO()  # 隔离平台解析夹具，不调用生产云端。

        print("\n[1] 解析：无正确答案字段的页面（对错标记 + 选项）")
        det = _parse_work_record_detail(NO_CORRECT_HTML)
        check("解析出 2 题", len(det) == 2, str(len(det)))
        if len(det) == 2:
            q1, q2 = det
            check("题1 标题正确", "数据库的主要功能" in q1["title"], q1["title"])
            check("题1 题型=单选", q1["type_label"] == "【单选题】", q1["type_label"])
            check("题1 我的答案=A", q1["my_answer"] == "A", q1["my_answer"])
            check("题1 无正确答案字段 has_correct=False", q1["has_correct"] is False)
            check("题1 标记为答对 is_correct=True", q1["is_correct"] is True, str(q1["is_correct"]))
            check("题1 选项 4 个", len(q1["options"]) == 4, str(q1["options"]))
            check("题1 选项映射 A→存储数据", dict(q1["options"]).get("A") == "存储数据")
            check("题1 每题得分 20.0", q1["q_score"] == 20.0, str(q1["q_score"]))
            check("题2 标记为答错 is_correct=False", q2["is_correct"] is False, str(q2["is_correct"]))
            check("题2 我的答案=ACD", q2["my_answer"] == "ACD", q2["my_answer"])

        print("\n[2] 判定：正确答案缺失时退回对错标记（不再误报「解析失败」）")
        v = judge_work_detail(det)
        check("all_correct=False（题2 判错）", v["all_correct"] is False)
        check("wrong 记录题2", len(v["wrong"]) == 1 and "在安装MySQL之前" in v["wrong"][0], str(v["wrong"]))
        check("unknown=0（标记可用，不算无法判定）", v["unknown"] == 0, str(v["unknown"]))
        check("feedback 为空（没有正确答案无法给 AI 反馈）", v["feedback"] == [], str(v["feedback"]))

        only_right = judge_work_detail([det[0]])
        check("只含答对题 → all_correct=True", only_right["all_correct"] is True)
        check("只含答对题 → wrong 为空", only_right["wrong"] == [])

        no_mark = judge_work_detail([{"title": "x", "correct_answer": "", "is_correct": None}])
        check("既无正确答案又无标记 → unknown=1", no_mark["unknown"] == 1 and no_mark["all_correct"] is False)

        print("\n[3] 解析：有正确答案字段的页面（旧形态必须继续支持）")
        det2 = _parse_work_record_detail(WITH_CORRECT_HTML)
        check("解析出 1 题", len(det2) == 1, str(len(det2)))
        if det2:
            check("has_correct=True", det2[0]["has_correct"] is True)
            check("正确答案=关系型数据库", det2[0]["correct_answer"] == "关系型数据库", det2[0]["correct_answer"])
            v2 = judge_work_detail(det2)
            check("我的答案 A vs 正确答案文本 → 判为不一致（保留原语义）", v2["all_correct"] is False)

        print("\n[4] 批改回流：答对的题（无正确答案字段）也能沉淀，答错的不沉淀")
        before = knowledge_size()
        n = harvest_correct_answers(det)
        after = knowledge_size()
        check("沉淀条数 = 1（只沉淀答对的题1）", n == 1, f"n={n}")
        check("知识库条目 +1", after == before + 1, f"{before} -> {after}")
        from api.answer import get_cache_dao
        dao = get_cache_dao()
        check("知识库键为题1标题",
              dao.get_cache("数据库的主要功能是什么？") is not None,
              str(list(dao._read_cache().keys())))
        check("知识库值为还原后的文本答案（不是字母 A）",
              dao.get_cache("数据库的主要功能是什么？") == "存储数据",
              str(dao.get_cache("数据库的主要功能是什么？")))
        check("答错的题2 未沉淀",
              dao.get_cache("【多选题】在安装MySQL之前，需要做哪些准备工作？") is None)

        n2 = harvest_correct_answers(det)
        check("重复回流不再新增（已存在不覆盖）", n2 == 0, f"n2={n2}")

        print("\n[5] 批改回流：有正确答案字段时按原逻辑沉淀")
        n3 = harvest_correct_answers(det2)
        check("沉淀条数 = 1", n3 == 1, f"n3={n3}")
        check("沉淀的是平台给的正确答案",
              get_cache_dao().get_cache("MySQL 属于哪类数据库？") == "关系型数据库",
              str(get_cache_dao().get_cache("MySQL 属于哪类数据库？")))

        print("\n[6] 多选字母还原（ACD → 三行文本）")
        qm = {"title": "多选题测试", "type_label": "【多选题】", "correct_answer": "",
              "is_correct": True, "my_answer": "ACD",
              "options": [("A", "甲"), ("B", "乙"), ("C", "丙"), ("D", "丁")]}
        harvest_correct_answers([qm])
        check("多选还原为 甲\\n丙\\n丁",
              get_cache_dao().get_cache("多选题测试") == "甲\n丙\n丁",
              repr(get_cache_dao().get_cache("多选题测试")))

        print("\n[7] 静态契约：base.py 仍不写知识库")
        base_src = open(os.path.join(ROOT, "api", "base.py"), encoding="utf8").read()
        check("api/base.py 不含 add_cache(", "add_cache" not in base_src)
        check("api/base.py 含 judge_work_detail 调用", "judge_work_detail(" in base_src)
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    if FAILED:
        print("失败项：" + "；".join(FAILED))
    sys.stdout.flush()
    os._exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()

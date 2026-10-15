# -*- coding: utf-8 -*-
"""转储章节检测 record-list / record-detail 原始 HTML，用于定位成绩/答案解析失配。

用法：LEKE_POINT=<pointId> [LEKE_TIMES=0] 运行；输出到 D:/学习通/_workdump/。
"""
import json
import os
import re
import sys

APP_DIR = os.path.abspath(os.environ.get("LEKE_APP_DIR", "D:/学习通"))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

_REAL_OUT = sys.stdout
OUT_DIR = os.path.join(APP_DIR, "_workdump")
os.makedirs(OUT_DIR, exist_ok=True)


def _emit(s):
    _REAL_OUT.write(s + "\n")
    _REAL_OUT.flush()


from gui_app import stream  # noqa: E402
stream.install()
stream.set_handlers(lambda t: _emit(t.rstrip()))

from api.base import Account, Chaoxing, _parse_work_record_list, _parse_work_record_detail  # noqa: E402

ACC_ID = os.environ.get("LEKE_ACC", "accd2ce1f69")
COURSE = os.environ.get("LEKE_COURSE", "266527964")
POINT = os.environ.get("LEKE_POINT", "1233020879")


def main():
    cp = {}
    import configparser
    p = configparser.RawConfigParser(interpolation=None)
    p.read(os.path.join(APP_DIR, "config.ini"), encoding="utf8")
    acc = {}
    for a in json.load(open(os.path.join(APP_DIR, "accounts.json"), encoding="utf-8")).get("accounts", []):
        if str(a.get("id")) == ACC_ID:
            acc = a
    cookie = os.path.join(APP_DIR, f"cookies_{ACC_ID}.txt")
    cx = Chaoxing(account=Account(acc.get("username"), acc.get("password")),
                  session_key=ACC_ID, cookie_path=cookie)
    cx.login(login_with_cookies=False)

    courses = [c for c in cx.get_course_list() if str(c.get("courseId")) == COURSE]
    c = courses[0]
    pl = cx.get_course_point(c["courseId"], c["clazzId"], c.get("cpi"))
    point = next(x for x in pl["points"] if str(x.get("id")) == POINT)
    jobs, info = cx.get_job_list(c, point, keep_passed=True)
    _emit(f"章节 {point.get('title')} jobs={[(j.get('type'), j.get('jobid')) for j in jobs]}")

    wk = next(j for j in jobs if j.get("type") == "workid")
    work_id = str(wk["jobid"]).replace("work-", "")
    _emit(f"workId = {work_id}")

    s = cx._session
    # 先取题面页，从中拿到真实 workAnswerId（否则 record-* 会返回「此作业已被删除」）
    wp0 = s.get("https://mooc1.chaoxing.com/mooc-ans/api/work",
                params={"api": "1", "workId": work_id, "jobid": wk["jobid"],
                        "originJobId": wk["jobid"], "needRedirect": "true", "skipHeader": "true",
                        "knowledgeid": str(info.get("knowledgeid", "")), "ktoken": str(info.get("ktoken", "")),
                        "cpi": str(info.get("cpi", "") or c.get("cpi")), "ut": "s",
                        "clazzId": c["clazzId"], "type": "", "enc": str(wk.get("enc", "")),
                        "mooc2": "1", "courseid": c["courseId"]},
                timeout=20)
    open(os.path.join(OUT_DIR, "work_page.html"), "w", encoding="utf-8").write(wp0.text)
    mwa = re.search(r'id=["\']workAnswerId["\'][^>]*value=["\']([^"\']*)', wp0.text)
    work_answer_id = mwa.group(1) if mwa else ""
    mwd = re.search(r'id=["\']workId["\'][^>]*value=["\']([^"\']*)', wp0.text)
    if mwd and mwd.group(1):
        work_id = mwd.group(1)
    _emit(f"workId = {work_id}  workAnswerId = {work_answer_id}")

    rl = s.get("https://mooc1.chaoxing.com/mooc-ans/work/record-list",
               params={"courseId": c["courseId"], "classId": c["clazzId"], "workId": work_id,
                       "workAnswerId": work_answer_id, "cpi": c.get("cpi"), "api": "1",
                       "mooc2": "1", "ut": "s"},
               timeout=20)
    open(os.path.join(OUT_DIR, "record_list.html"), "w", encoding="utf-8").write(rl.text)
    recs = _parse_work_record_list(rl.text)
    _emit(f"record-list 解析 => {recs}  (len={len(rl.text)})")

    times = os.environ.get("LEKE_TIMES") or (str(max(r[0] for r in recs)) if recs else "0")
    rd = s.get("https://mooc1.chaoxing.com/mooc-ans/work/record-detail",
               params={"courseId": c["courseId"], "classId": c["clazzId"], "workId": work_id,
                       "workAnswerId": work_answer_id, "times": times, "cpi": c.get("cpi"), "ut": "s",
                       "isdisplaytable": "0", "firstHeader": "2", "isWork": "false",
                       "workSystem": "0", "api": "1", "archive": "false", "mooc2": "1"},
               timeout=20)
    open(os.path.join(OUT_DIR, "record_detail.html"), "w", encoding="utf-8").write(rd.text)
    det = _parse_work_record_detail(rd.text)
    _emit(f"record-detail times={times} 解析 => {len(det)} 题  (len={len(rd.text)})")
    for q in det:
        _emit(f"   id={q['id']} parse_ok={q['parse_ok']} type={q['type_label']!r} "
              f"my={q['my_answer']!r} correct={q['correct_answer']!r} title={q['title'][:40]!r}")

    # 兜底分支：直接问题目页
    wp = s.get("https://mooc1.chaoxing.com/mooc-ans/api/work",
               params={"api": "1", "workId": work_id, "jobid": wk["jobid"],
                       "originJobId": wk["jobid"], "needRedirect": "true", "skipHeader": "true",
                       "knowledgeid": str(info.get("knowledgeid", "")), "ktoken": str(info.get("ktoken", "")),
                       "cpi": str(info.get("cpi", "") or c.get("cpi")), "ut": "s",
                       "clazzId": c["clazzId"], "type": "", "enc": str(wk.get("enc", "")),
                       "mooc2": "1", "courseid": c["courseId"]},
               timeout=20)
    open(os.path.join(OUT_DIR, "work_page.html"), "w", encoding="utf-8").write(wp.text)
    _emit(f"\napi/work 页 len={len(wp.text)}  含answerwqbid={'answerwqbid' in wp.text} "
          f"含正确答案={'正确答案' in wp.text} 含我的答案={'我的答案' in wp.text}")
    det2 = _parse_work_record_detail(wp.text)
    _emit(f"work_page 解析 => {len(det2)} 题")
    for q in det2:
        _emit(f"   id={q['id']} parse_ok={q['parse_ok']} type={q['type_label']!r} "
              f"my={q['my_answer']!r} correct={q['correct_answer']!r} title={q['title'][:40]!r}")
    m = re.search(r'本次成绩<i>([\d.]+)</i>分', wp.text)
    _emit(f"work_page 成绩匹配 => {m.group(1) if m else None}")

    # 对错标记 / 每题得分 / 选项
    raw = rd.text
    _emit(f"\n[record-detail] marking_dui={raw.count('marking_dui')} "
          f"marking_cuo={raw.count('marking_cuo')} scoreNum={re.findall(r'scoreNum\">([\d.]+)<', raw)}")
    _emit(f"[record-list] viewNum={re.findall(r'viewNum\">第(\\d+)次', rl.text)} "
          f"viewScore={re.findall(r'viewScore\">([\\d.]+)分', rl.text)}")
    for qm in re.finditer(r'<div class="TiMu[^"]*singleQuesId" data="(\d+)"[^>]*>(.*?)(?=<div class="TiMu|$)', raw, re.S):
        blk = qm.group(2)
        opts = re.findall(r'<i\s+class="fl">([A-Z])、</i>\s*<a[^>]*>(.*?)</a>', blk, re.S)
        mark = 'dui' if 'marking_dui' in blk else ('cuo' if 'marking_cuo' in blk else '?')
        sc = re.findall(r'scoreNum">([\d.]+)<', blk)
        _emit(f"   q{qm.group(1)} 标记={mark} 得分={sc} 选项={[(a, b.strip()) for a, b in opts]}")
    _emit(f"HTML 已存到 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    except BaseException:
        import traceback
        traceback.print_exc(file=_REAL_OUT)
        code = 1
    _REAL_OUT.flush()
    os._exit(code)

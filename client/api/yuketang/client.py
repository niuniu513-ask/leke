# -*- coding: utf-8 -*-
"""雨课堂（yuketang / 学堂在线）刷课引擎 —— vendor 化库实现。

来源、作者与改动清单见同目录 NOTICE.md。

库化约定（乐课踩坑记录）：
  * 第三方依赖（requests / websocket / qrcode / PIL）全部延迟导入或 try/except 可选：
    import 得到、实例化不炸、缺库时给清晰错误；
  * 不 print 调试（GUI 下 stdout 被 StreamProxy 接管），日志统一走 api.logger，
    缺 loguru 时静默降级；
  * 没有 __main__ 才赋值的模块级全局——上游脚本的 domain / headers / cookies
    全部收进实例属性。
"""
from __future__ import annotations

import io
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "DEFAULT_DOMAIN",
    "LEAF_TYPE_VIDEO",
    "LEAF_TYPE_EXERCISE",
    "LEAF_TYPE_EXAM",
    "YuketangClient",
    "YuketangError",
    "parse_cookie_string",
    "qrcode_available",
    "render_qrcode_png",
]

# 各校域名不同（如 buu.yuketang.cn / ustc.yuketang.cn），界面可填
DEFAULT_DOMAIN = ""

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_4) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/87.0.4280.67 Safari/537.36"
)

LEAF_TYPE_VIDEO = 0          # 上游 leaf_type 字典里的视频类型
LEAF_TYPE_EXERCISE = 6       # 作业 / 章节测验（上游 homeworkHelper 同名常量）
LEAF_TYPE_EXAM = 5           # 考试
COMPLETE_RATE = 0.95         # 上游 while float(val) <= 0.95
HEARTBEATS_PER_BATCH = 3     # 上游每轮 for i in range(3)
DEFAULT_LEARNING_RATE = 12   # 单个心跳推进的秒数
DEFAULT_TIMEOUT = 10
DEFAULT_POLL_INTERVAL = 2.0  # 上游硬编码 time.sleep(2)
DEFAULT_MAX_STALL = 10       # 乐课新增：进度长期不动时放弃，避免死循环
QRCODE_BOX_SIZE = 10
QRCODE_BORDER = 4

# ---------------------------------------------------------------- 答题相关
#: 题型分类（雨课堂 content.Type 字段的取值）
_TYPE_SINGLE = {"SingleChoice"}
_TYPE_MULTIPLE = {"MultipleChoice"}
_TYPE_FILL = {"FillBlank", "FillBlankQuestion"}
_TYPE_JUDGE = {"TrueFalse", "Judge", "Judgement"}
_TYPE_CHOICE = _TYPE_SINGLE | _TYPE_MULTIPLE | _TYPE_JUDGE

#: 判断题「正确 / 错误」两边各自的自然语言写法，用于把答案映射回选项 key
_JUDGE_TRUE_WORDS = frozenset({
    "true", "t", "yes", "y", "1", "正确", "对", "是", "right", "correct", "a",
})
_JUDGE_FALSE_WORDS = frozenset({
    "false", "f", "no", "n", "0", "错误", "错", "否", "不正确", "不对",
    "wrong", "incorrect", "b",
})

#: 答案文本归一化时要抹掉的标点 / 空白（比对选项时用）
_ANSWER_NOISE = re.compile(
    r"[\s，,。；;：:、（）()\[\]【】<>《》\"'“”‘’.!！?？]")


def _answer_type_of(problem_type: Any) -> str:
    """把 content.Type 归一到 single/multiple/fill/judge/unknown 五档。

    同时兼容「已经是归一结果」的入参（solve_exercise 会把 parse_problem 的
    ``type`` 原样回传给 submit_answer，避免二次归一踩空）。
    """
    value = str(problem_type or "").strip()
    if value in ("single", "multiple", "fill", "judge"):
        return value
    if value in _TYPE_FILL or "FillBlank" in value:
        return "fill"
    if value in _TYPE_MULTIPLE:
        return "multiple"
    if value in _TYPE_JUDGE:
        return "judge"
    if value in _TYPE_SINGLE:
        return "single"
    if value in _TYPE_CHOICE:
        return "single"
    return "unknown"


def _answer_clean(text: Any) -> str:
    """比对选项时的归一化：去标点空白、转小写。"""
    return _ANSWER_NOISE.sub("", str(text or "").strip().lower())


def _split_answer_tokens(answer: Any) -> List[str]:
    """把「A、B」「A;B」「A B」等写法拆成 token 列表（也接受已是 list）。"""
    if isinstance(answer, (list, tuple, set)):
        return [str(item).strip() for item in answer if str(item).strip()]
    raw = str(answer or "")
    parts = re.split(r"[,，;；|/、\s]+", raw)
    return [part.strip() for part in parts if part.strip()]


def _build_option_map(options: Any) -> Dict[str, str]:
    """options 归一成 {归一化key: 原始key}，兼容 key 是 'A' 或 '1' 两种写法。"""
    out: Dict[str, str] = {}
    if not isinstance(options, list):
        return out
    for opt in options:
        if not isinstance(opt, dict):
            continue
        raw_key = str(opt.get("key") or opt.get("Key") or "").strip()
        if not raw_key:
            continue
        out[_answer_clean(raw_key)] = raw_key
    return out


def _options_of(content: dict) -> List[dict]:
    """从 content 里取选项列表，兼容 Options / options 两种大小写。"""
    options = content.get("Options")
    if options is None:
        options = content.get("options")
    return options if isinstance(options, list) else []


def _judge_option_key(token: str, options: List[dict]) -> Optional[str]:
    """判断题：把「正确 / 对 / T」这类答案映射回题目里真实的选项 key。"""
    cleaned = _answer_clean(token)
    if not cleaned:
        return None
    wanted_true = cleaned in _JUDGE_TRUE_WORDS
    wanted_false = cleaned in _JUDGE_FALSE_WORDS
    if not wanted_true and not wanted_false:
        return None
    wanted = _JUDGE_TRUE_WORDS if wanted_true else _JUDGE_FALSE_WORDS
    for opt in options:
        if not isinstance(opt, dict):
            continue
        key = str(opt.get("key") or opt.get("Key") or "").strip()
        if not key:
            continue
        value = str(opt.get("value") or opt.get("Value") or "").strip()
        if (_answer_clean(key) in wanted) or (_answer_clean(value) in wanted):
            return key
    return None


def _match_option_key(token: str, options: List[dict]) -> Optional[str]:
    """把单个答案 token 对上题目的真实选项 key（先比字母、再比选项正文）。"""
    cleaned = _answer_clean(token)
    if not cleaned:
        return None
    for opt in options:
        if not isinstance(opt, dict):
            continue
        key = str(opt.get("key") or opt.get("Key") or "").strip()
        if key and cleaned == _answer_clean(key):
            return key
    for opt in options:
        if not isinstance(opt, dict):
            continue
        key = str(opt.get("key") or opt.get("Key") or "").strip()
        value = str(opt.get("value") or opt.get("Value") or "").strip()
        if key and value and cleaned == _answer_clean(value):
            return key
    return None


def _normalize_choice_answer(answer: Any, answer_type: str, options: List[dict]) -> Optional[List[str]]:
    """选择 / 判断题：把任意答案写法统一成「真实选项 key 的列表」。

    返回 None 表示无法落到合法选项 → 调用方应跳过这题，别把脏数据提交上去。
    """
    if not options:
        return None
    tokens = _split_answer_tokens(answer)
    if not tokens:
        return None
    # 多选常见「ABCD」连写：拆成单个字母
    if answer_type == "multiple" and len(tokens) == 1:
        compact = _answer_clean(tokens[0])
        valid = {_answer_clean(o.get("key")) for o in options
                 if isinstance(o, dict) and o.get("key")}
        if len(compact) > 1 and all(ch in valid for ch in compact):
            tokens = list(compact)

    keys: List[str] = []
    if answer_type == "judge":
        keys = _normalize_judge(answer, options)
    else:
        for token in tokens:
            key = _match_option_key(token, options)
            if key is None:
                return None
            if key not in keys:
                keys.append(key)
    if not keys:
        return None
    if answer_type in ("single", "judge") and len(keys) != 1:
        return None
    return keys


def _normalize_judge(answer: Any, options: List[dict]) -> List[str]:
    """判断题答案 → 选项 key 列表（兼容答案是「正确/对/T」或直接给 key）。"""
    tokens = _split_answer_tokens(answer)
    if not tokens:
        return []
    keys: List[str] = []
    for token in tokens:
        key = _match_option_key(token, options) or _judge_option_key(token, options)
        if key is None:
            # 判断题特殊：答案可能是"正确/错误"，而选项是 A/B 无文字
            cleaned = _answer_clean(token)
            if cleaned in _JUDGE_TRUE_WORDS:
                key = _pick_judge_by_index(options, True)
            elif cleaned in _JUDGE_FALSE_WORDS:
                key = _pick_judge_by_index(options, False)
        if key is None:
            return []
        if key not in keys:
            keys.append(key)
    return keys


def _pick_judge_by_index(options: List[dict], is_true: bool) -> Optional[str]:
    """最后的兜底：判断题只有两个选项时，按「正确在前 / 错误在后」取 key。

    雨课堂判断题的选项顺序固定为「正确、错误」，没有文字标签时只能靠位置。
    """
    keys = [str(o.get("key") or "").strip() for o in options if isinstance(o, dict)]
    keys = [k for k in keys if k]
    if len(keys) != 2:
        return None
    return keys[0] if is_true else keys[1]


def _build_submit_payload(classroom_id: Any, problem_id: Any, answer: Any,
                          answer_type: str) -> Optional[dict]:
    """按题型组装 problem_apply 的请求体；无法组装时返回 None。

    三种格式（跨 5 个参考实现交叉验证，务必区分）：
      * 填空  -> ``answers``: {"1": "...", "2": "..."}（字段名是 answers）
      * 多选  -> ``answer``: ["A", "B"]
      * 单选 / 判断 -> ``answer``: ["A"]
    """
    pid = problem_id
    if pid in (None, ""):
        return None
    try:
        cid: Any = int(classroom_id)
    except (TypeError, ValueError):
        cid = classroom_id

    if answer_type == "fill":
        if isinstance(answer, dict):
            data = {str(k): str(v) for k, v in answer.items()}
        elif isinstance(answer, (list, tuple)):
            data = {str(i + 1): str(v) for i, v in enumerate(answer)}
        else:
            data = {"1": str(answer)}
        data = {k: v for k, v in data.items() if str(v).strip()}
        if not data:
            return None
        return {"classroom_id": cid, "problem_id": pid, "answers": data}

    values = _split_answer_tokens(answer)
    values = [v for v in values if v]
    if not values:
        return None
    return {"classroom_id": cid, "problem_id": pid, "answer": values}

# Set-Cookie 的属性字段（解析 cookie 时丢弃）
_COOKIE_ATTRS = frozenset({
    "path", "domain", "expires", "max-age", "httponly", "secure", "samesite",
    "version", "comment", "commenturl", "discard", "port",
})


class YuketangError(Exception):
    """雨课堂引擎统一异常。"""


# --------------------------------------------------------------------------- #
# 可选依赖：requests
# --------------------------------------------------------------------------- #
try:  # requests 是项目既有依赖，但仍做成可选导入，缺库时只抛 YuketangError
    import requests  # type: ignore
except Exception:  # pragma: no cover - 环境异常路径
    requests = None  # type: ignore

try:
    from requests import exceptions as _requests_exceptions

    _RETRY_EXCEPTIONS: tuple = (
        _requests_exceptions.Timeout,
        _requests_exceptions.ConnectionError,
        _requests_exceptions.ChunkedEncodingError,
    )
except Exception:  # pragma: no cover - 环境异常路径
    _RETRY_EXCEPTIONS = (Exception,)


# 显式绕过所有代理（含环境变量）。requests 的 `proxies=` 只按 scheme 覆盖，
# 用 `None` 值表示"该 scheme 不走代理"；同时清空环境变量兜底。
_NO_PROXY: dict = {"http": None, "https": None}
_EMPTY_PROXY_ENV: dict = {
    "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "",
    "http_proxy": "", "https_proxy": "", "all_proxy": "",
    "NO_PROXY": "*", "no_proxy": "*",
}


def _no_proxy_env() -> dict:
    env = dict(os.environ)
    env.update(_EMPTY_PROXY_ENV)
    return env


def _dispatch(sender: Any, method: str, url: str, **kwargs: Any) -> Any:
    """调用发送方；优先 `.request()`，退化为 `.get()/.post()` 等（兼容测试替身）。"""
    request = getattr(sender, "request", None)
    if callable(request):
        return request(method, url, **kwargs)
    verb = getattr(sender, method.lower(), None)
    if callable(verb):
        return verb(url, **kwargs)
    raise YuketangError("requests 不可用，无法访问雨课堂接口")


def _send(method: str, url: str, *, session: Any = None, retries: int = 2,
          backoff: float = 1.0, log_label: str = "雨课堂", **kwargs: Any) -> Any:
    """发一个 HTTP 请求：默认信任系统代理，失败会自动绕过代理直连重试。

    为什么要这么做：requests 默认会读取 `HTTPS_PROXY` 等环境变量。用户机器上
    这些变量常来自某个临时/已失效的本地代理（本机踩过 `127.0.0.1:60152`），
    表现为 TLS 中途 EOF（`SSLEOFError UNEXPECTED_EOF_WHILE_READING`）。
    一次代理抖动就会让整个登录失败，而且错误信息完全指向不了代理。
    这里把"代理挂了"变成"自动直连重试"，用户无感。
    """
    if requests is None:
        raise YuketangError("requests 不可用，无法访问雨课堂接口")
    sender = session if session is not None else requests
    last_error: Any = None
    total = max(1, int(retries))
    for attempt in range(1, total + 1):
        bypass = True  # 2026-10-10 起一律直连（牛哥拍板「代理环境全部直连」），不再尊重环境代理
        try:
            if bypass:
                try:
                    return _dispatch(sender, method, url, proxies=_NO_PROXY, **kwargs)
                except TypeError:
                    # 发送方不支持 proxies 参数 → 退化为临时清环境变量
                    with _temporary_environ(_no_proxy_env()):
                        return _dispatch(sender, method, url, **kwargs)
            return _dispatch(sender, method, url, **kwargs)
        except _RETRY_EXCEPTIONS as exc:
            last_error = exc
            if attempt < total:
                _log("warning", f"[{log_label}] 请求失败（{method} {url}），第 "
                     f"{attempt}/{total} 次将{'绕过代理直连' if attempt == 1 else '重试'}：{exc}")
                try:
                    time.sleep(backoff * attempt)
                except Exception:
                    pass
        except Exception as exc:  # 非网络异常（如参数错误）直接抛出，不做无意义重试
            raise
    raise YuketangError(
        f"请求失败（{method} {url}），已尝试 {total} 次（含绕过代理直连）仍失败：{last_error}")


class _temporary_environ:
    """上下文管理器：临时覆盖 os.environ，退出时精确还原。"""

    def __init__(self, patch: dict):
        self._patch = patch
        self._saved: dict = {}

    def __enter__(self):
        for key, value in self._patch.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, exc_type, exc, tb):
        for key, old in self._saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        return False


# --------------------------------------------------------------------------- #
# 日志：延迟导入 api.logger，缺库静默降级（顶层绝不 import）
# --------------------------------------------------------------------------- #
_LOGGER_LOCK = threading.Lock()
_LOGGER: Any = None
_LOGGER_READY = False


def _get_logger() -> Any:
    """延迟获取 api.logger.logger；缺 loguru / 循环导入时返回 None。"""
    global _LOGGER, _LOGGER_READY
    if not _LOGGER_READY:
        with _LOGGER_LOCK:
            if not _LOGGER_READY:
                try:
                    from api.logger import logger as _lg

                    _LOGGER = _lg
                except Exception:
                    _LOGGER = None
                _LOGGER_READY = True
    return _LOGGER


def _body_hint(response: Any, limit: int = 120) -> str:
    """把响应正文截一小段附到错误信息里。

    雨课堂各校网关的行为差别很大，只报"失败"用户无从下手；
    带上服务端原话（HTTP 状态 + 正文片段）就能一眼看出是被网关拦了、
    还是这个微信号没绑定本校账号。
    """
    try:
        text = str(getattr(response, "text", "") or "")
    except Exception:
        return ""
    text = " ".join(text.split())
    if not text:
        return ""
    return "；服务端返回：" + (text[:limit] + ("…" if len(text) > limit else ""))


def _log(level: str, message: str) -> None:
    logger = _get_logger()
    if logger is None:
        return
    try:
        writer = getattr(logger, level.lower(), None)
        if callable(writer):
            writer(message)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _normalize_domain(domain: Any) -> str:
    """接受任意学校的 HTTPS 入口或域名，不自动绑定某所学校。"""
    from urllib.parse import urlsplit
    text = str(domain or "").strip()
    if not text:
        raise YuketangError("请先填写你所在学校的雨课堂网址或域名")
    parsed = urlsplit(text if '://' in text else 'https://' + text)
    try:
        host = (parsed.hostname or '').lower().encode('idna').decode('ascii')
        port = parsed.port
    except ValueError as exc:
        raise YuketangError("学校网址格式不正确") from exc
    if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
            or '.' not in host or '..' in host or port not in (None, 443)):
        raise YuketangError("请填写学校雨课堂的 HTTPS 网址或完整域名")
    if host in ('www.yuketang.cn', 'yuketang.cn'):
        raise YuketangError("这是雨课堂介绍官网，请填写学校提供的教学登录网址")
    return host


def parse_cookie_string(raw: Any) -> Dict[str, str]:
    """把 a=1; b=2 或 Set-Cookie 串解析成 cookie 字典（丢弃 path/expires 等属性）。"""
    out: Dict[str, str] = {}
    if not raw:
        return out
    for chunk in re.split("[;,]", str(raw)):
        part = chunk.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        if not key or key.lower() in _COOKIE_ATTRS:
            continue
        out[key] = value.strip()
    return out


def _cookie_header(cookies: Dict[str, str]) -> str:
    return "; ".join(str(k) + "=" + str(v) for k, v in cookies.items())


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or_self(value: Any) -> Any:
    """上游心跳里 u/c/v 都是 int(...)；转不了就原样回退，保证不炸。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return value


def _truthy_flag(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    return str(value).strip().strip('"').lower() in ("1", "true", "yes")


def _json_of(resp: Any) -> Any:
    """容错取 JSON：先 .json()，再退化成 json.loads(resp.text)。"""
    try:
        return resp.json()
    except Exception:
        pass
    try:
        return json.loads(getattr(resp, "text", "") or "")
    except Exception:
        return None


def _dig_user_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates: List[Dict[str, Any]] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
        info = data.get("user_info")
        if isinstance(info, dict):
            candidates.append(info)
    for candidate in candidates:
        for key in ("user_id", "userId", "UserID", "uid"):
            value = candidate.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _re_user_id(text: str) -> str:
    match = re.search('"user_id"[ \t]*:[ \t]*"?([^",} \t]+)', text or "")
    return match.group(1) if match else ""


def _collect_video_leaves(node: Any, out: Dict[str, str]) -> None:
    """递归收集 leaf_type==0 的视频叶子（处理 section_leaf_list 里嵌套的 leaf_list）。"""
    if not isinstance(node, dict):
        return
    children = node.get("leaf_list")
    if isinstance(children, list) and children:
        for child in children:
            _collect_video_leaves(child, out)
        return
    if node.get("leaf_type") == LEAF_TYPE_VIDEO and node.get("id") is not None:
        out[str(node["id"])] = str(node.get("name") or "")


def _strip_html(html: Any) -> str:
    """粗粒度去 HTML 标签 + 还原常见实体，题干送入题库 / AI 前用。"""
    text = str(html or "")
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", text).strip()


def _collect_exercise_leaves(node: Any, out: List[dict]) -> None:
    """递归收集 leaf_type==6（作业 / 章节测验）的叶子。"""
    if not isinstance(node, dict):
        return
    children = node.get("leaf_list")
    if isinstance(children, list) and children:
        for child in children:
            _collect_exercise_leaves(child, out)
        return
    if node.get("leaf_type") == LEAF_TYPE_EXERCISE and node.get("id") is not None:
        out.append({
            "id": node.get("id"),
            "name": str(node.get("name") or ""),
            "leaf_type": LEAF_TYPE_EXERCISE,
        })


def _stopped(should_stop: Optional[Callable[[], bool]]) -> bool:
    if should_stop is None:
        return False
    try:
        return bool(should_stop())
    except Exception:
        return False


def _safe_call(callback: Optional[Callable[[dict], None]], payload: dict) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception as exc:
        _log("warning", "[雨课堂] 进度回调执行失败：" + str(exc))


def _parse_available_in(resp: Any) -> Optional[float]:
    """识别上游那个 Expected available in N second. 阻塞响应；无匹配返回 None。"""
    text = getattr(resp, "text", "") or ""
    match = re.search("Expected available in[ \t]*(.+?)[ \t]*second", text)
    if not match:
        return None
    try:
        return float(match.group(1).strip())
    except (TypeError, ValueError):
        return 0.0


def _timestamp_ms() -> int:
    return int(round(time.time() * 1000))


def _random_tag(video_id: str) -> str:
    alphabet = "zyxwvutsrqponmlkjihgfedcba1234567890"
    return str(video_id) + "_" + "".join(random.sample(alphabet, 4))


# --------------------------------------------------------------------------- #
# 二维码：① 已安装 qrcode ② vendor 副本 ③ 清晰错误，全部延迟导入
# --------------------------------------------------------------------------- #
_QR_LOCK = threading.Lock()
_QR_MODULE: Any = None
_QR_READY = False


def _load_qrcode() -> Any:
    """三层兜底加载二维码渲染库。"""
    global _QR_MODULE, _QR_READY
    if not _QR_READY:
        with _QR_LOCK:
            if not _QR_READY:
                module = None
                try:
                    import qrcode as module  # noqa: F401
                except Exception:
                    module = None
                if module is None:
                    try:
                        from . import _vendor_qrcode as module  # type: ignore
                    except Exception as exc:
                        raise YuketangError(
                            "二维码渲染不可用：未安装 qrcode 包，且内置 vendor 副本加载失败（"
                            + str(exc) + "）"
                        ) from exc
                _QR_MODULE = module
                _QR_READY = True
    return _QR_MODULE


def qrcode_available() -> bool:
    """二维码能否渲染（GUI 可据此决定是否展示扫码登录入口）。"""
    try:
        _load_qrcode()
        return True
    except Exception:
        return False


def render_qrcode_png(data: str) -> bytes:
    """把登录串渲染成 PNG 字节；渲染库不可用时抛 YuketangError（二维码渲染不可用）。"""
    qr_module = _load_qrcode()
    try:
        qr = qr_module.QRCode(
            version=1,
            error_correction=qr_module.constants.ERROR_CORRECT_L,
            box_size=QRCODE_BOX_SIZE,
            border=QRCODE_BORDER,
        )
        qr.add_data(data)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except YuketangError:
        raise
    except Exception as exc:
        raise YuketangError("二维码渲染不可用：" + str(exc)) from exc


def _load_websocket() -> Any:
    try:
        import websocket as websocket_module

        return websocket_module
    except Exception as exc:
        raise YuketangError(
            "扫码登录不可用：未安装 websocket-client（pip install websocket-client）"
        ) from exc


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
class YuketangClient:
    """一个学校域名的雨课堂会话。所有网络调用都是显式方法，便于 GUI 线程化。"""

    def __init__(
        self,
        domain: str,
        cookie: str = "",
        user_id: str = "",
        university_id: str = "",
        learning_rate: int = DEFAULT_LEARNING_RATE,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.domain = _normalize_domain(domain)
        self.user_id = str(user_id or "")
        self.university_id = str(university_id or "")
        self.learning_rate = max(1, _as_int(learning_rate, DEFAULT_LEARNING_RATE))
        self.timeout = max(1, _as_int(timeout, DEFAULT_TIMEOUT))
        self.poll_interval = DEFAULT_POLL_INTERVAL   # 内部节流，测试可覆盖
        self.max_stall = DEFAULT_MAX_STALL           # 内部停滞保护

        self.cookies: Dict[str, str] = parse_cookie_string(cookie)
        self.cookies.setdefault("platform_id", "3")
        if self.university_id:
            self.cookies["university_id"] = self.university_id
        self.csrftoken = self.cookies.get("csrftoken", "")
        self.sessionid = self.cookies.get("sessionid", "")
        self.cookie = _cookie_header(self.cookies)

        self.headers: Dict[str, str] = {}
        self._local = threading.local()
        self._sessions: List[Any] = []
        self._sessions_lock = threading.Lock()
        self._identity: Optional[dict] = None
        self._build_headers()

    # ---------------------------------------------------------------- 内部
    def _build_headers(self) -> Dict[str, str]:
        self.cookie = _cookie_header(self.cookies)
        self.headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Cookie": self.cookie,
            "x-csrftoken": self.csrftoken,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "university-id": self.university_id,
            "xtbz": "cloud",
        }
        for session in list(self._sessions):
            try:
                session.headers.update(self.headers)
            except Exception:
                pass
        return self.headers

    def _apply_university_id(self, university_id: Any) -> None:
        value = str(university_id or "")
        if not value:
            return
        self.university_id = value
        self.cookies["university_id"] = value
        self._build_headers()

    def _apply_cookie(self, cookie: Any) -> None:
        parsed = parse_cookie_string(cookie)
        if not parsed:
            return
        self.cookies.update(parsed)
        self.cookies.setdefault("platform_id", "3")
        if self.university_id:
            self.cookies["university_id"] = self.university_id
        self.csrftoken = self.cookies.get("csrftoken", self.csrftoken)
        self.sessionid = self.cookies.get("sessionid", self.sessionid)
        self._build_headers()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return "https://" + self.domain + path

    def _session(self) -> Any:
        if requests is None:
            raise YuketangError("requests 不可用，无法访问雨课堂接口")
        session = getattr(self._local, "session", None)
        if session is None:
            try:
                session = requests.Session()
            except Exception:
                session = requests
            try:
                session.headers.update(self.headers)
            except Exception:
                pass
            self._local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def _sleep(self, seconds: Any) -> None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if value > 0:
            time.sleep(value)

    def _request(self, method: str, url: str, max_retries: int = 3, **kwargs: Any) -> Any:
        if requests is None:
            raise YuketangError("requests 不可用，无法访问雨课堂接口")
        kwargs.setdefault("timeout", self.timeout)
        last_error: Any = None
        total = max(1, int(max_retries))
        for attempt in range(1, total + 1):
            # 首次走环境代理；第 2 次起绕过代理直连（本地代理抖动最常见的坑，
            # 见模块内 _send 的说明）。两次都失败才继续退避重试。
            bypass = attempt > 1
            try:
                session = self._session()
                if bypass:
                    try:
                        return _dispatch(session, method, url, proxies=_NO_PROXY, **kwargs)
                    except TypeError:
                        with _temporary_environ(_no_proxy_env()):
                            return _dispatch(session, method, url, **kwargs)
                return _dispatch(session, method, url, **kwargs)
            except _RETRY_EXCEPTIONS as exc:
                last_error = exc
                if attempt < total:
                    _log(
                        "warning",
                        "[雨课堂] 请求失败（" + method + " " + url + "），第 "
                        + str(attempt) + "/" + str(total) + " 次"
                        + ("绕过代理直连" if bypass else "重试") + "：" + str(exc),
                    )
                    self._sleep(2 * attempt)
        raise YuketangError(
            "请求失败（" + method + " " + url + "），已尝试 " + str(total)
            + " 次（含绕过代理直连）仍失败：" + str(last_error)
        )

    def _ensure_university_id(self) -> str:
        if not self.university_id:
            self._apply_university_id(YuketangClient.get_university_id(self.domain))
        return self.university_id

    def _ensure_identity(self) -> dict:
        if not self.user_id:
            return self.fetch_identity()
        return self._identity or {
            "user_id": self.user_id,
            "realname": "",
            "university_id": self.university_id,
        }

    # ---------------------------------------------------------------- 登录
    @staticmethod
    def get_university_id(domain: str) -> str:
        """GET /edu_admin/get_custom_university_info/ 拿学校 id。"""
        normalized = _normalize_domain(domain)
        if requests is None:
            raise YuketangError("requests 不可用，无法获取 university_id")
        url = (
            "https://" + normalized
            + "/edu_admin/get_custom_university_info/?current=1&_="
            + str(_timestamp_ms())
        )
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        try:
            response = _send("GET", url, headers=headers, timeout=DEFAULT_TIMEOUT,
                             log_label="雨课堂")
            payload = _json_of(response) or {}
            data = payload.get("data") if isinstance(payload, dict) else None
            university_id = (data or {}).get("university_id") if isinstance(data, dict) else None
        except YuketangError:
            raise
        except Exception as exc:
            raise YuketangError("获取 university_id 失败：" + str(exc)) from exc
        if university_id in (None, ""):
            raise YuketangError("获取 university_id 失败：响应里没有 university_id")
        return str(university_id)

    @staticmethod
    def _cookies_from_response(response: Any) -> Dict[str, str]:
        chunks: List[str] = []
        raw = getattr(response, "raw", None)
        getlist = getattr(getattr(raw, "headers", None), "getlist", None)
        if callable(getlist):
            try:
                chunks = [str(c) for c in (getlist("Set-Cookie") or [])]
            except Exception:
                chunks = []
        if not chunks:
            try:
                value = response.headers.get("Set-Cookie") or response.headers.get("set-cookie")
            except Exception:
                value = None
            if value:
                chunks = [str(value)]
        cookies: Dict[str, str] = {}
        for chunk in chunks:
            cookies.update(parse_cookie_string(chunk))
        if not cookies:
            jar = getattr(response, "cookies", None)
            try:
                for key in list(jar.keys()):
                    cookies[str(key)] = str(jar.get(key))
            except Exception:
                pass
        return cookies

    @staticmethod
    def _verify_login(domain: str, university_id: str, auth_info: Any, origin_user_id: Any) -> dict:
        """POST /edu_admin/account/login/verify-origin-system-bind 换取正式 cookie。"""
        if requests is None:
            raise YuketangError("requests 不可用，无法完成登录校验")
        normalized = _normalize_domain(domain)
        url = (
            "https://" + normalized
            + "/edu_admin/account/login/verify-origin-system-bind?term=latest&uv_id="
            + str(university_id)
        )
        headers = {
            "referer": "https://" + normalized + "/pro/portal/home/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/json",
            "Cookie": "university_id=" + str(university_id)
            + ";platform_id=3;xtbz=cloud;platform_type=1;",
            "Origin": "https://" + normalized,
            "Platform-Id": "3",
            "University-Id": str(university_id),
            "Terminal-Type": "web",
            "X-Client": "web",
            "X-Csrftoken": "undefined",
            "Xtbz": "cloud",
        }
        form = {"auth": auth_info, "origin_user_id": str(origin_user_id)}
        try:
            response = _send("POST", url, json=form, headers=headers, timeout=15,
                             log_label="雨课堂")
        except YuketangError:
            raise
        except Exception as exc:
            raise YuketangError("登录校验请求失败：" + str(exc)) from exc
        cookies = YuketangClient._cookies_from_response(response)
        status = getattr(response, "status_code", "?")
        payload = _json_of(response)
        data = payload.get('data') if isinstance(payload, dict) else None
        if isinstance(data, dict) and data.get('bind_status') is False:
            raise YuketangError(
                "微信扫码已完成，但该微信尚未绑定当前学校的雨课堂账号。"
                "请先在浏览器打开 https://" + normalized
                + "/pro/portal/home/，按学校页面提示绑定学号或工号，再返回乐课重新扫码。"
                "若浏览器已经登录，可使用“用 cookie 登录”；同时检查所选学校域名是否正确。"
            )
        if isinstance(status, int) and status >= 400:
            raise YuketangError("学校雨课堂登录校验被拒绝（HTTP " + str(status) + "），请在学校网站确认账号状态。")
        if not cookies:
            raise YuketangError(
                "登录校验失败：响应里没有 Set-Cookie（HTTP " + str(status) + "）"
                + _body_hint(response)
            )
        # 只有拿到 sessionid 才算真的登录成功。少了它后面每个接口都会 403，
        # 界面却会显示"已登录"—— 这种"假成功"最难排查，所以直接在校验层拦住。
        if not cookies.get("sessionid"):
            raise YuketangError(
                "登录校验没有拿到 sessionid（HTTP " + str(status) + "），"
                "只拿到 " + ", ".join(sorted(cookies)) + _body_hint(response)
                + "。常见原因：这个微信还没有绑定本校雨课堂账号，"
                "或学校网关拦截了校验请求。"
            )
        user_id = _dig_user_id(_json_of(response)) or str(origin_user_id or "")
        return {
            "cookie": _cookie_header(cookies),
            "user_id": user_id,
            "university_id": str(university_id),
        }

    @staticmethod
    def _fetch_realname(domain: str, cookie: str, university_id: str) -> str:
        if requests is None:
            return ""
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Cookie": cookie,
            "university-id": str(university_id),
            "xtbz": "cloud",
        }
        try:
            response = _send(
                "GET",
                "https://" + _normalize_domain(domain) + "/edu_admin/get_user_basic_info/",
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
                log_label="雨课堂",
            )
            payload = _json_of(response) or {}
            data = payload.get("data") if isinstance(payload, dict) else None
            info = (data or {}).get("user_info") if isinstance(data, dict) else None
            info = info if isinstance(info, dict) else {}
            return str(info.get("name") or info.get("realname") or "")
        except Exception as exc:
            _log("warning", "[雨课堂] 获取真实姓名失败（不阻断登录）：" + str(exc))
            return ""

    @staticmethod
    def login_by_qrcode(
        domain: str,
        on_qrcode: Callable[[bytes], None],
        should_stop: Optional[Callable[[], bool]] = None,
        timeout: int = 300,
    ) -> dict:
        """打开 wss://{domain}/wsapp/ 扫码登录，成功后返回 cookie / user_id / realname。"""
        normalized = _normalize_domain(domain)
        websocket_module = _load_websocket()
        university_id = YuketangClient.get_university_id(normalized)

        state: Dict[str, Any] = {"message": "", "error": None, "render_error": None, "timer": None}
        holder: Dict[str, Any] = {"ws": None}

        def cancel_timer() -> None:
            timer = state.get("timer")
            if timer is not None:
                try:
                    timer.cancel()
                except Exception:
                    pass
                state["timer"] = None

        def fetch_qrcode() -> None:
            ws = holder.get("ws")
            if ws is None:
                return
            try:
                ws.send(json.dumps({
                    "op": "requestlogin",
                    "role": "web",
                    "version": 1.4,
                    "type": "qrcode",
                }))
            except Exception as exc:
                state["error"] = state["error"] or exc
                return
            # 重新计时，形成 60 秒一轮的刷新。
            # 上游那只 Timer 只响一次，之后完全靠服务端推 requestlogin 才会再刷新 ——
            # 服务端不推的话二维码就永久过期，用户扫的是一张废码。
            start_timer()

        def start_timer() -> None:
            cancel_timer()
            try:
                timer = threading.Timer(60.0, fetch_qrcode)
                timer.daemon = True
                state["timer"] = timer
                timer.start()
            except Exception:
                state["timer"] = None

        def on_open(ws: Any) -> None:
            fetch_qrcode()
            start_timer()

        def on_message(ws: Any, message: str) -> None:
            try:
                msg = json.loads(message)
            except Exception:
                return
            if not isinstance(msg, dict):
                return
            qr_data = msg.get("qrcode")
            if "ticket" in msg and qr_data:
                try:
                    png = render_qrcode_png(qr_data)
                except Exception as exc:
                    state["render_error"] = exc
                    cancel_timer()
                    try:
                        ws.close()
                    except Exception:
                        pass
                    return
                if on_qrcode is not None:
                    try:
                        on_qrcode(png)
                    except Exception as exc:
                        _log("warning", "[雨课堂] 二维码回调执行失败：" + str(exc))
            if msg.get("op") == "requestlogin":
                fetch_qrcode()
            if msg.get("op") == "loginsuccess":
                state["message"] = message
                cancel_timer()
                try:
                    ws.close()
                except Exception:
                    pass

        def on_error(ws: Any, error: Any) -> None:
            state["error"] = state["error"] or error
            _log("warning", "[雨课堂] 扫码登录 WebSocket 错误：" + str(error))

        def on_close(ws: Any, *args: Any) -> None:
            cancel_timer()

        if _stopped(should_stop):
            raise YuketangError("已取消")

        ws = websocket_module.WebSocketApp(
            "wss://" + normalized + "/wsapp/",
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.on_open = on_open
        holder["ws"] = ws

        def runner() -> None:
            try:
                # 带上浏览器同款 Origin：部分学校的网关会校验来源，而
                # websocket-client 默认发的是 http://host（连 scheme 都不对）。
                # 不开 ping：上游也没开，且有些网关不回 pong，ping_timeout 反而会
                # 掐断"正在等用户扫码"的那条连接。
                ws.run_forever(origin="https://" + normalized, suppress_origin=False)
            except Exception as exc:
                state["error"] = state["error"] or exc

        thread = threading.Thread(target=runner, name="yuketang-qrcode-ws", daemon=True)
        thread.start()
        deadline = time.time() + max(10, _as_int(timeout, 300))
        try:
            while thread.is_alive() and not state["message"] and state["render_error"] is None:
                if _stopped(should_stop):
                    raise YuketangError("已取消")
                if time.time() > deadline:
                    raise YuketangError("二维码登录超时")
                time.sleep(0.2)
        finally:
            cancel_timer()
            if thread.is_alive():
                try:
                    ws.close()
                except Exception:
                    pass
            thread.join(timeout=3.0)

        if state["render_error"] is not None:
            raise YuketangError("二维码渲染不可用：" + str(state["render_error"]))
        if not state["message"]:
            if state["error"] is not None:
                raise YuketangError("扫码登录失败：" + str(state["error"]))
            raise YuketangError("扫码登录失败：未收到登录成功消息")

        try:
            payload = json.loads(state["message"])
        except Exception as exc:
            raise YuketangError("扫码登录失败：返回消息不是合法 JSON（" + str(exc) + "）") from exc
        auth_info = payload.get("Auth") or payload.get("auth")
        origin_user_id = payload.get("UserID") or payload.get("userId") or payload.get("user_id") or ""
        if not auth_info:
            raise YuketangError("扫码登录失败：返回消息缺少 Auth 字段")

        result = YuketangClient._verify_login(normalized, university_id, auth_info, origin_user_id)
        result["realname"] = YuketangClient._fetch_realname(
            normalized, result["cookie"], result["university_id"]
        )
        _log("info", "[雨课堂] 扫码登录成功：" + str(result.get("realname") or result.get("user_id")))
        return result

    def fetch_identity(self) -> dict:
        """GET check_user_session 拿 user_id、get_user_basic_info 拿 realname，并缓存。

        方法无参，所以缓存命中时直接返回上次结果（要强制刷新就新建 client）。
        """
        if requests is None:
            raise YuketangError("requests 不可用，无法获取用户信息")
        if self._identity is not None:
            return dict(self._identity)
        self._ensure_university_id()

        try:
            response = self._request("GET", self._url("/edu_admin/check_user_session/"))
        except YuketangError:
            raise
        except Exception as exc:
            raise YuketangError("获取 user_id 失败：" + str(exc)) from exc
        user_id = _dig_user_id(_json_of(response)) or _re_user_id(getattr(response, "text", ""))
        if not user_id:
            raise YuketangError(
                "获取 user_id 失败：cookie 可能已失效（HTTP "
                + str(getattr(response, "status_code", "?")) + "）" + _body_hint(response)
            )
        self.user_id = str(user_id)

        realname = ""
        try:
            response = self._request("GET", self._url("/edu_admin/get_user_basic_info/"))
            payload = _json_of(response) or {}
            data = payload.get("data") if isinstance(payload, dict) else None
            info = (data or {}).get("user_info") if isinstance(data, dict) else None
            info = info if isinstance(info, dict) else {}
            realname = str(info.get("name") or info.get("realname") or "")
        except Exception as exc:
            _log("warning", "[雨课堂] 获取真实姓名失败（不阻断）：" + str(exc))

        self._identity = {
            "user_id": self.user_id,
            "realname": realname,
            "university_id": self.university_id,
        }
        return dict(self._identity)

    # ------------------------------------------------------------ 课程 / 视频
    def list_courses(self) -> List[dict]:
        """GET /mooc-api/v1/lms/user/user-courses/ 返回可刷课程列表。"""
        self._ensure_university_id()
        url = self._url("/mooc-api/v1/lms/user/user-courses/") + (
            "?status=1&page=1&no_page=1&term=latest&uv_id=" + self.university_id
        )
        response = self._request("GET", url)
        payload = _json_of(response)
        try:
            products = payload["data"]["product_list"]
        except Exception as exc:
            raise YuketangError("获取课程列表失败：响应结构异常（" + str(exc) + "）") from exc
        courses: List[dict] = []
        for item in products or []:
            if not isinstance(item, dict):
                continue
            courses.append({
                key: item.get(key)
                for key in ("course_name", "classroom_id", "course_sign", "sku_id", "course_id")
            })
        _log("info", "[雨课堂] 共获取 " + str(len(courses)) + " 门课程")
        return courses

    def list_videos(self, classroom_id: Any, course_sign: Any) -> Dict[str, str]:
        """GET course/chapter，返回 {video_id: video_name}（仅 leaf_type==0）。"""
        self._ensure_university_id()
        url = self._url("/mooc-api/v1/lms/learn/course/chapter") + (
            "?cid=" + str(classroom_id)
            + "&term=latest&uv_id=" + self.university_id
            + "&sign=" + str(course_sign)
        )
        response = self._request("GET", url)
        payload = _json_of(response)
        try:
            chapters = payload["data"]["course_chapter"]
        except Exception as exc:
            raise YuketangError("获取章节列表失败：响应结构异常（" + str(exc) + "）") from exc
        videos: Dict[str, str] = {}
        for chapter in chapters or []:
            if not isinstance(chapter, dict):
                continue
            for leaf in chapter.get("section_leaf_list") or []:
                _collect_video_leaves(leaf, videos)
        _log("info", "[雨课堂] 章节解析出 " + str(len(videos)) + " 个视频")
        return videos

    def list_exercises(self, classroom_id: Any, course_sign: Any) -> List[dict]:
        """GET course/chapter，返回所有 leaf_type==6 的作业 / 测验。

        每条：{"id": leaf_id, "name": 标题, "leaf_type": 6}。
        与 list_videos 同一个接口，只是筛的类型不同（上游 homeworkHelper 同理）。
        """
        self._ensure_university_id()
        url = self._url("/mooc-api/v1/lms/learn/course/chapter") + (
            "?cid=" + str(classroom_id)
            + "&term=latest&uv_id=" + self.university_id
            + "&sign=" + str(course_sign)
        )
        response = self._request("GET", url)
        payload = _json_of(response)
        try:
            chapters = payload["data"]["course_chapter"]
        except Exception as exc:
            raise YuketangError("获取章节列表失败：响应结构异常（" + str(exc) + "）") from exc
        exercises: List[dict] = []
        for chapter in chapters or []:
            if not isinstance(chapter, dict):
                continue
            for leaf in chapter.get("section_leaf_list") or []:
                _collect_exercise_leaves(leaf, exercises)
        _log("info", "[雨课堂] 章节解析出 " + str(len(exercises)) + " 个作业 / 测验")
        return exercises

    def get_exercise_problems(self, classroom_id: Any, leaf_id: Any,
                              sku_id: Any = "") -> dict:
        """拉一个作业 / 测验的全部题目。

        两步（上游 homeworkHelper / Raincourse 流程一致）：
          1. GET leaf_info/{cid}/{leaf_id}/ -> content_info.leaf_type_id、sku_id；
          2. GET exercise/get_exercise_list/{leaf_type_id}/{sku_id}/ -> data.problems。

        返回 {"name": 作业名, "problems": [...], "font": 字体混淆 URL 或 "",
              "leaf_type_id": ..., "sku_id": ...}；problems 里的题目**原样透传**，
        交给上层解析（content.Body / content.Options / content.Type / id）。
        """
        self._ensure_university_id()
        info_url = self._url("/mooc-api/v1/lms/learn/leaf_info/"
                             + str(classroom_id) + "/" + str(leaf_id) + "/") + (
            "?sign=&term=latest&uv_id=" + self.university_id
        )
        info_resp = self._request("GET", info_url)
        info_payload = _json_of(info_resp)
        info_data = info_payload.get("data") if isinstance(info_payload, dict) else None
        content_info = (info_data or {}).get("content_info") if isinstance(info_data, dict) else None
        if not isinstance(content_info, dict):
            raise YuketangError("获取作业信息失败：响应里没有 content_info")
        leaf_type_id = content_info.get("leaf_type_id")
        if leaf_type_id in (None, ""):
            raise YuketangError("获取作业信息失败：缺少 leaf_type_id")
        real_sku = sku_id or (info_data or {}).get("sku_id") or ""
        name = str((info_data or {}).get("name") or "")

        list_url = self._url("/mooc-api/v1/lms/exercise/get_exercise_list/"
                             + str(leaf_type_id) + "/") + (
            "?term=latest&uv_id=" + self.university_id
        )
        if real_sku not in (None, ""):
            list_url += "&sku_id=" + str(real_sku)
        list_resp = self._request("GET", list_url)
        list_payload = _json_of(list_resp)
        data = list_payload.get("data") if isinstance(list_payload, dict) else None
        if not isinstance(data, dict):
            raise YuketangError("获取题目列表失败：响应结构异常")
        problems = data.get("problems")
        if not isinstance(problems, list):
            problems = []
        font = data.get("font") or ""
        if not name:
            name = str(data.get("name") or "")
        _log("info", "[雨课堂] 作业「" + name + "」共 " + str(len(problems)) + " 道题")
        return {
            "name": name,
            "problems": problems,
            "font": font,
            "leaf_type_id": leaf_type_id,
            "sku_id": real_sku,
        }

    def submit_answer(self, classroom_id: Any, problem_id: Any, answer: Any,
                      problem_type: Any = "") -> dict:
        """POST exercise/problem_apply 提交一道题；返回原始响应 dict。

        answer 可以是：
          * 选择 / 判断：["A"] / ["A","B"] / "A" / "正确"
          * 填空：{"1": "答案"} 或 ["答案1", "答案2"] 或纯字符串
        本方法按题型把写法归一成后台认的格式（选择题必须落到真实选项 key）。
        """
        answer_type = _answer_type_of(problem_type)
        if answer_type == "unknown":
            # 兜底：拿不到题型时按「纯选择」尝试（填空只能靠 answers 字段，
            # 无法猜；由调用方显式给 type 更稳）
            answer_type = "single"
        payload = _build_submit_payload(classroom_id, problem_id, answer, answer_type)
        if payload is None:
            raise YuketangError("提交答案失败：答案为空或无法归一（problem_id="
                                + str(problem_id) + "）")
        url = self._url("/mooc-api/v1/lms/exercise/problem_apply/") + (
            "?term=latest&uv_id=" + self.university_id
        )
        response = self._request("POST", url, json=payload)
        payload_out = _json_of(response)
        return payload_out if isinstance(payload_out, dict) else {}

    @staticmethod
    def parse_problem(problem: Any) -> dict:
        """把一道题的 content 摊平成统一结构（不依赖字体解密，原文保留）。

        返回 {"problem_id", "type"(single/multiple/fill/judge/unknown),
              "type_text", "body"(去标签题干), "options":[{key,value}],
              "blanks", "score", "submitted"}。
        """
        problem = problem if isinstance(problem, dict) else {}
        content = problem.get("content")
        if not isinstance(content, dict):
            content = {}
        options_raw = _options_of(content)
        options = []
        for opt in options_raw:
            if not isinstance(opt, dict):
                continue
            options.append({
                "key": str(opt.get("key") or opt.get("Key") or ""),
                "value": str(opt.get("value") or opt.get("Value") or ""),
            })
        body_html = str(content.get("Body") or content.get("body") or "")
        body = _strip_html(body_html)
        submitted = problem.get("submission_status")
        return {
            "problem_id": content.get("ProblemID") or content.get("problem_id")
            or problem.get("problem_id"),
            "type": _answer_type_of(content.get("Type")),
            "type_text": str(content.get("TypeText") or ""),
            "body": body,
            "body_html": body_html,
            "options": options,
            "blanks": content.get("Blanks") or [],
            "score": content.get("Score") or 0,
            "submitted": submitted,
            "user": problem.get("user") or {},
        }

    def solve_exercise(
        self,
        classroom_id: Any,
        leaf_id: Any,
        sku_id: Any = "",
        answer_fn: Optional[Callable[[dict], Any]] = None,
        on_progress: Optional[Callable[[dict], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        delay_range: tuple = (1.0, 2.5),
    ) -> dict:
        """做完一个作业 / 测验：拉题 → 逐题取答案 → 提交。

        answer_fn(problem_dict) 由上层提供（可接题库链 + AI），返回答案；
        返回 None / 空表示没答案，这题计为 skipped（不提交）。
        已被接口判定为用户可见答案的题（``user.answer``）由 answer_fn 自己决定用不用。

        返回 {"name", "total", "done", "skipped", "failed", "details": [...]}。
        """
        exercise = self.get_exercise_problems(classroom_id, leaf_id, sku_id)
        problems = exercise.get("problems") or []
        result: Dict[str, Any] = {
            "name": exercise.get("name") or "",
            "total": len(problems),
            "done": 0,
            "skipped": 0,
            "failed": 0,
            "details": [],
        }
        for index, raw in enumerate(problems):
            if _stopped(should_stop):
                break
            parsed = self.parse_problem(raw)
            item = {
                "problem_id": parsed.get("problem_id"),
                "type": parsed.get("type"),
                "type_text": parsed.get("type_text"),
                "body": parsed.get("body"),
                "ok": False,
                "note": "",
            }
            # 已提交过的题跳过（雨课堂允许重复提交，但没必要）
            if parsed.get("submitted") not in (None, "", 0, False):
                item["note"] = "已提交过，跳过"
                result["skipped"] += 1
                result["details"].append(item)
                _safe_call(on_progress, {"index": index + 1, "total": len(problems),
                                         "status": "skipped", "problem": item})
                continue

            answer = None
            if answer_fn is not None:
                try:
                    answer = answer_fn(parsed)
                except Exception as exc:
                    _log("warning", "[雨课堂] 取答案失败（" + str(parsed.get("body"))[:30]
                         + "）：" + str(exc))
                    answer = None
            if answer in (None, "", [], {}):
                item["note"] = "无答案，跳过"
                result["skipped"] += 1
                result["details"].append(item)
                _safe_call(on_progress, {"index": index + 1, "total": len(problems),
                                         "status": "skipped", "problem": item})
                continue

            try:
                resp = self.submit_answer(classroom_id, parsed.get("problem_id"),
                                          answer, parsed.get("type"))
            except Exception as exc:
                item["note"] = "提交异常：" + str(exc)
                result["failed"] += 1
                result["details"].append(item)
                _safe_call(on_progress, {"index": index + 1, "total": len(problems),
                                         "status": "failed", "problem": item})
                continue

            ok = bool(resp.get("success")) or resp.get("errcode") in (0, "0")
            item["ok"] = ok
            item["note"] = "提交成功" if ok else ("提交失败：" + str(resp.get("msg")
                                                             or resp.get("errmsg") or ""))
            if ok:
                result["done"] += 1
            else:
                result["failed"] += 1
            result["details"].append(item)
            _safe_call(on_progress, {"index": index + 1, "total": len(problems),
                                     "status": "done" if ok else "failed",
                                     "problem": item})
            if delay_range and index < len(problems) - 1:
                self._sleep(random.uniform(*delay_range))

        _log("info", "[雨课堂] 作业「" + str(result["name"]) + "」完成：答对提交 "
             + str(result["done"]) + "，跳过 " + str(result["skipped"])
             + "，失败 " + str(result["failed"]))
        return result

    def get_video_progress(self, cid: Any, classroom_id: Any, video_id: Any) -> dict:
        """GET get_video_watch_progress；字段缺失给安全默认值，绝不抛。"""
        result = {"completed": False, "rate": 0.0, "watch_length": 0}
        video_key = str(video_id)
        try:
            self._ensure_identity()
            url = self._url("/video-log/get_video_watch_progress/") + (
                "?cid=" + str(cid)
                + "&user_id=" + self.user_id
                + "&classroom_id=" + str(classroom_id)
                + "&video_type=video&vtype=rate&video_id=" + video_key
                + "&snapshot=1&term=latest&uv_id=" + self.university_id
            )
            response = self._request("GET", url)
        except Exception as exc:
            _log("warning", "[雨课堂] 获取视频进度失败（按未完成处理）：" + str(exc))
            return result

        payload = _json_of(response)
        item: Any = {}
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                item = data.get(video_key) or data.get(video_id) or {}
        if not isinstance(item, dict):
            item = {}

        result["rate"] = _as_float(item.get("rate"))
        result["watch_length"] = _as_int(item.get("watch_length"))
        if "completed" in item:
            result["completed"] = _truthy_flag(item.get("completed"))
        else:
            match = re.search('"completed"[ \t]*:[ \t]*"?([0-9]+)', getattr(response, "text", "") or "")
            result["completed"] = bool(match and match.group(1) == "1")
        return result

    # -------------------------------------------------------------- 心跳伪造
    def _build_heartbeat(
        self,
        video_id: str,
        cid: Any,
        classroom_id: Any,
        skuid: Any,
        cp_start: int,
    ) -> tuple:
        """构造上游那 3 条心跳包，返回 (heart_data, 下一批 cp)。"""
        heart_data: List[dict] = []
        cp = _as_int(cp_start)
        timestamp = str(_timestamp_ms())
        for index in range(HEARTBEATS_PER_BATCH):
            heart_data.append({
                "i": 5,
                "et": "loadeddata",
                "p": "web",
                "n": "ali-cdn.xuetangx.com",
                "lob": "cloud4",
                "cp": cp,
                "fp": 0,
                "tp": 0,
                "sp": 2,
                "ts": timestamp,
                "u": _int_or_self(self.user_id),
                "uip": "",
                "c": _int_or_self(cid),
                "v": _int_or_self(video_id),
                "skuid": skuid,
                "classroomid": str(classroom_id),
                "cc": str(video_id),
                "d": 4976.5,
                "pg": _random_tag(video_id),
                "sq": index + 1,
                "t": "video",
            })
            cp += self.learning_rate
        return heart_data, cp

    def watch_video(
        self,
        video_id: Any,
        video_name: str,
        cid: Any,
        classroom_id: Any,
        skuid: Any,
        on_progress: Optional[Callable[[dict], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """伪造心跳刷完一个视频：True=完成/已跳过，False=放弃。"""
        video_id = str(video_id)
        classroom_id = str(classroom_id)

        progress = self.get_video_progress(cid, classroom_id, video_id)
        if progress.get("completed"):
            _log("info", "[雨课堂] " + str(video_name) + " 已学习完毕，跳过")
            _safe_call(on_progress, {
                "video_id": video_id,
                "video_name": video_name,
                "rate": float(progress.get("rate") or 0.0),
                "watch_length": _as_int(progress.get("watch_length")),
            })
            return True

        rate = float(progress.get("rate") or 0.0)
        frame = _as_int(progress.get("watch_length"))
        heartbeat_url = self._url("/video-log/heartbeat/")
        submit_url = self._url("/mooc-api/v1/lms/exercise/problem_apply/") + (
            "?term=latest&uv_id=" + self.university_id
        )
        _log("info", "[雨课堂] " + str(video_name) + " 尚未学完（"
             + format(rate * 100, ".2f") + "%），开始伪造心跳")

        stall = 0
        self._sleep(self.poll_interval)
        while rate <= COMPLETE_RATE:
            if _stopped(should_stop):
                _log("info", "[雨课堂] " + str(video_name) + " 收到停止信号，中断")
                return False

            heart_data, frame = self._build_heartbeat(video_id, cid, classroom_id, skuid, frame)
            response = self._request("POST", heartbeat_url, json={"heart_data": heart_data})

            # 上游的「网络阻塞」退避：等到解禁后把同一批心跳再投给 problem_apply
            delay = _parse_available_in(response)
            if delay is not None:
                _log("warning", "[雨课堂] 心跳被阻塞，等待 " + str(delay) + " 秒后重试")
                self._sleep(delay + 0.5)
                self._request("POST", submit_url, data={"heart_data": heart_data})

            if _stopped(should_stop):
                _log("info", "[雨课堂] " + str(video_name) + " 收到停止信号，中断")
                return False

            progress = self.get_video_progress(cid, classroom_id, video_id)
            new_rate = float(progress.get("rate") or 0.0)
            if new_rate > rate + 1e-9:
                rate = new_rate
                stall = 0
            else:
                rate = max(rate, new_rate)
                stall += 1

            _safe_call(on_progress, {
                "video_id": video_id,
                "video_name": video_name,
                "rate": float(rate),
                "watch_length": _as_int(frame),
            })
            self._sleep(self.poll_interval)

            if rate > COMPLETE_RATE:
                break
            if stall >= self.max_stall:
                _log("warning", "[雨课堂] " + str(video_name) + " 进度停滞 "
                     + str(stall) + " 轮，放弃该视频")
                return False

        _log("info", "[雨课堂] 视频 " + str(video_name) + " 学习完成（"
             + format(rate * 100, ".2f") + "%）")
        return True

    # -------------------------------------------------------------- 整门课
    def _watch_one(
        self,
        video_id: str,
        video_name: str,
        cid: Any,
        classroom_id: str,
        skuid: Any,
        on_progress: Optional[Callable[[dict], None]],
        should_stop: Optional[Callable[[], bool]],
        on_video_start: Optional[Callable[[str, str], None]],
        result: dict,
        lock: threading.Lock,
    ) -> None:
        if _stopped(should_stop):
            return
        if on_video_start is not None:
            try:
                on_video_start(video_id, video_name)
            except Exception as exc:
                _log("warning", "[雨课堂] on_video_start 回调执行失败：" + str(exc))

        progress = self.get_video_progress(cid, classroom_id, video_id)
        if progress.get("completed"):
            with lock:
                result["skipped"] += 1
            _safe_call(on_progress, {
                "video_id": video_id,
                "video_name": video_name,
                "rate": float(progress.get("rate") or 0.0),
                "watch_length": _as_int(progress.get("watch_length")),
            })
            return

        try:
            ok = self.watch_video(
                video_id, video_name, cid, classroom_id, skuid,
                on_progress=on_progress, should_stop=should_stop,
            )
        except Exception as exc:
            _log("error", "[雨课堂] " + str(video_name) + " 刷课异常：" + str(exc))
            ok = False

        if not ok and _stopped(should_stop):
            # 被 should_stop 中断：不算失败，剩余视频视为未处理
            return

        with lock:
            if ok:
                result["done"] += 1
            else:
                result["failed"].append(video_name)

    def watch_course_videos(
        self,
        classroom_id: Any,
        course_sign: Any,
        cid: Any,
        skuid: Any,
        on_progress: Optional[Callable[[dict], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        on_video_start: Optional[Callable[[str, str], None]] = None,
        max_workers: int = 1,
    ) -> dict:
        """把一门课的视频列表刷完，返回 {total, done, skipped, failed}。"""
        videos = self.list_videos(classroom_id, course_sign)
        items = list(videos.items())
        result: Dict[str, Any] = {"total": len(items), "done": 0, "skipped": 0, "failed": []}
        lock = threading.Lock()
        workers = max(1, _as_int(max_workers, 1))

        if workers > 1 and len(items) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(
                        self._watch_one, video_id, video_name, cid, str(classroom_id), skuid,
                        on_progress, should_stop, on_video_start, result, lock,
                    )
                    for video_id, video_name in items
                ]
                for future in futures:
                    try:
                        future.result()
                    except Exception as exc:
                        _log("error", "[雨课堂] 并发刷课任务异常：" + str(exc))
        else:
            for video_id, video_name in items:
                self._watch_one(
                    video_id, video_name, cid, str(classroom_id), skuid,
                    on_progress, should_stop, on_video_start, result, lock,
                )

        _log(
            "info",
            "[雨课堂] 课程完成：共 " + str(result["total"])
            + "，完成 " + str(result["done"])
            + "，跳过 " + str(result["skipped"])
            + "，失败 " + str(len(result["failed"])),
        )
        return result

    # ---------------------------------------------------------------- 收尾
    def close(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions)
            self._sessions = []
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass
        self._local = threading.local()

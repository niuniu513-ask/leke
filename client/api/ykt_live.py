# -*- coding: utf-8 -*-
"""雨课堂「课堂互动」协议层：扫码/口令签到、PPT 展示、课堂答题。

与 ``api/yuketang/client.py``（刷课引擎）共用同一套请求设施与登录态，
这里只补上**课堂内**的三件事：

* :meth:`YKTLive.scan` / :meth:`YKTLive.check_in` —— 动态二维码 / 口令签到
* :meth:`YKTLive.presentation` —— 拉当前课堂 PPT（逐页）
* :meth:`YKTLive.answer` —— 课堂答题（支持延时提交）

接口都挂在雨课堂域名下（``https://<域名>/api/v3/…``），鉴权靠 cookie +
登录签到后服务端下发的 ``set-auth`` / ``lessonToken``（Bearer）。
"""
from __future__ import annotations

import time
from typing import Any, Optional

from loguru import logger

try:  # vendor 缺失时给可读报错，不炸导入（见项目 vendor 兜底约定）
    from api.yuketang.client import YuketangClient, YuketangError

    _VENDOR_OK = True
except Exception as _exc:  # pragma: no cover - 仅在裁剪包时命中
    YuketangClient = Any  # type: ignore
    YuketangError = Exception  # type: ignore
    _VENDOR_OK = False
    _VENDOR_ERR = _exc


def vendor_available() -> bool:
    return _VENDOR_OK


class YKTLive:
    """雨课堂课堂互动客户端（复用 :class:`YuketangClient` 的连接与登录态）。"""

    def __init__(self, client):
        self.client = client
        #: lessonId -> (bearer, lessonToken)，签到后才有，答题/PPT 依赖它
        self._lesson_tokens: dict[str, tuple[str, str]] = {}

    # ------------------------------------------------------------ 内部
    def _request(self, method: str, path: str, **kwargs) -> Any:
        if not _VENDOR_OK:
            raise YuketangError(f"雨课堂组件不可用：{_VENDOR_ERR}")
        return self.client._request(method, self.client._url(path), **kwargs)

    @staticmethod
    def _json(resp) -> Optional[dict]:
        if resp is None:
            return None
        try:
            return resp.json()
        except Exception:
            logger.debug("雨课堂返回非 JSON：{}", getattr(resp, "text", "")[:160])
            return None

    def _bearer(self, lesson_id: str) -> Optional[str]:
        pair = self._lesson_tokens.get(str(lesson_id))
        return pair[0] if pair else None

    # ------------------------------------------------------------ 课堂列表 / 签到
    def on_lesson(self) -> list:
        """正在进行中的课堂（``onLessonClassrooms``）。"""
        try:
            resp = self._request("GET", "/api/v3/classroom/on-lesson")
        except Exception as exc:
            logger.warning("雨课堂 on-lesson 失败：{}", exc)
            return []
        data = self._json(resp)
        if not data or data.get("code") != 0:
            return []
        try:
            return data["data"]["onLessonClassrooms"] or []
        except (TypeError, KeyError):
            return []

    def check_in(self, lesson_id: str, join_if_not_in: bool = True) -> dict:
        """课堂签到（返回 ``{code, lessonToken?, error?}``）。"""
        payload = {"source": 21, "lessonId": str(lesson_id),
                   "joinIfNotIn": bool(join_if_not_in)}
        try:
            resp = self._request("POST", "/api/v3/lesson/checkin", json=payload)
        except Exception as exc:
            return {"code": -1, "error": str(exc)}
        data = self._json(resp)
        if not data:
            return {"code": -1, "error": "无响应"}
        code = data.get("code")
        if code == 0:
            bearer = ""
            token = ""
            try:
                bearer = resp.headers.get("set-auth") or ""
            except Exception:
                bearer = ""
            try:
                token = str(data["data"]["lessonToken"])
            except (TypeError, KeyError):
                token = ""
            if bearer or token:
                self._lesson_tokens[str(lesson_id)] = (bearer, token)
            return {"code": 0, "lessonToken": token}
        return {"code": code, "error": data.get("msg") or "签到失败"}

    def scan(self, qr_code_url: str) -> dict:
        """扫码签到：解析二维码链接拿到 lessonId 再签到。"""
        try:
            resp = self._request("POST", "/api/v3/app/scan", json={"url": qr_code_url})
        except Exception as exc:
            return {"code": -1, "error": str(exc)}
        data = self._json(resp)
        if not data or data.get("code") != 0:
            return {"code": (data or {}).get("code", -1),
                    "error": (data or {}).get("msg") or "二维码无效"}
        try:
            lesson_id = str(data["data"]["value"])
        except (TypeError, KeyError):
            return {"code": -1, "error": "二维码里没有 lessonId"}
        return self.check_in(lesson_id)

    # ------------------------------------------------------------ PPT / 答题
    def presentation(self, presentation_id: str) -> Optional[dict]:
        """拉取课堂 PPT（含逐页内容）。"""
        lesson = self._lesson_tokens
        bearer = ""
        if lesson:
            bearer = next(iter(lesson.values()))[0]
        headers = {"authorization": f"Bearer {bearer}"} if bearer else None
        try:
            resp = self._request(
                "GET", f"/api/v3/lesson/presentation/fetch?presentation_id={presentation_id}",
                headers=headers)
        except Exception as exc:
            logger.warning("雨课堂 PPT 拉取失败：{}", exc)
            return None
        data = self._json(resp)
        return (data or {}).get("data")

    def answer(self, lesson_id: str, problem_id: str, problem_type: int,
               options: Optional[list] = None, content: Optional[str] = None,
               image_urls: Optional[list] = None, retry: bool = False,
               time_ms: Optional[int] = None) -> dict:
        """提交课堂答题。

        ``problem_type``：选择/判断类传 ``options``（字母列表），
        ``5``（主观题）传 ``content`` / ``image_urls``。
        """
        bearer = self._bearer(lesson_id)
        path = "/api/v3/lesson/problem/retry" if retry else "/api/v3/lesson/problem/answer"
        headers = {"authorization": f"Bearer {bearer}"} if bearer else None
        stamp = int(time_ms if time_ms is not None else time.time() * 1000)

        if problem_type == 5:
            pics = [{"pic": url, "thumb": f"{url}?imageView2/2/w/568"}
                    for url in (image_urls or [])] or [{"pic": "", "thumb": ""}]
            result: Any = {"content": content or "", "pics": pics, "videos": []}
        else:
            result = options or []

        item = {"problemId": str(problem_id), "dt": stamp,
                "problemType": int(problem_type), "result": result}
        payload: Any = item
        if retry:
            item["retry_times"] = None
            payload = {"problems": [item]}
        try:
            resp = self._request("POST", path, json=payload, headers=headers)
        except Exception as exc:
            return {"code": -1, "error": str(exc)}
        return self._json(resp) or {"code": -1, "error": "无响应"}


def build_from_client(client) -> Optional[YKTLive]:
    """从已构造的 :class:`YuketangClient` 造课堂互动客户端。"""
    if client is None or not _VENDOR_OK:
        return None
    return YKTLive(client)

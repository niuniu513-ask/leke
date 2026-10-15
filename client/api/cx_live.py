# -*- coding: utf-8 -*-
"""学习通「课堂互动」协议层：签到 / 主题讨论 / 评分 / 投票 / 问卷 / 随堂练习 / 群聊签到。

协议来源
--------
移动端接口（mobilelearn / groupyd / pan-yz）按抓包结果逆向后用 Python 实现，
接口地址与参数口径参考了社区同类客户端（GPL-3.0，署名见
``THIRD_PARTY_NOTICES.md``）。与 ``api/base.py`` 的**网页版**协议是两条独立路线：

* ``api/base.py``  ——  mooc2-ans / mooc1 网页接口（章节、视频、文档、章节测验）
* ``api/cx_live.py`` —— mobilelearn / groupyd / pan-yz 移动端接口（课堂互动）

两条路线共用同一份登录 cookie：网页登录拿到的 ``_uid`` / ``fid`` 等被移动端接口
直接接受（已实测），所以**不需要**额外做 App 登录。

桌面端适配（相对移动端的差异）
------------------------------
移动端才有的能力，在桌面端用等价手段替代：

==========================  ==================================================
移动端能力                  桌面端替代
==========================  ==================================================
扫码（二维码签到）          粘贴二维码链接 / 直接填 enc —— :meth:`sign_qrcode`
GPS 定位（位置签到）        手填地址 + 经纬度 —— :meth:`sign_location`
拍照（拍照签到）            选本地图片上传拿 objectId —— :meth:`upload_image`
活体人脸（人脸签到）        用已采集人脸 :meth:`face_id` + 签名 :meth:`face_enc`
==========================  ==================================================

设备信息（``cid`` / ``sc``）
---------------------------
位置签名与人脸签名都要拼 ``sc`` 盐、写 ``cxcid``。这两个值来自 App 登录时下发的
``clientId``（RSA 解密），网页登录拿不到 —— 但学习通对**未采集设备信息**的账号
返回的就是 ``{"sc":"","_t":"","cid":""}``，用空串一样能过接口，因此这里默认留空。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import pyaes
from loguru import logger

from api._net import NO_PROXY

# ====================================================================== 协议常量

#: schild 盐（构造 App User-Agent 用，桌面端走网页 cookie 时一般不校验）
SCHILD_SALT = r"ipL$TkeiEmfy1gTXb2XHrdLN0a@7c^vu"
#: 人脸 objectId 获取的盐
GET_FACE_SALT = "uWwjeEKsri"
#: 部分接口的固定 token
INF_ENC_TOKEN = "4faa8662c59590c6f43ae9fe5b002b42"
#: inf_enc 签名尾部盐
INF_ENC_KEY = "Z(AfY@XS"
#: 设备指纹 AES-ECB 密钥
DEVICE_CODE_KEY = "QrCbNY@MuK1X8HGw"
#: 学习通滑块验证码 captchaId
CX_CAPTCHA_ID = "Qt9FIw9o4pwRjOyqM6yizZBh682qN2TU"

SIGN_URL = "https://mobilelearn.chaoxing.com/pptSign/stuSignajax"
GROUP_SIGN_URL = "https://mobilelearn.chaoxing.com/sign/stuSignajax"
CHECK_SIGN_CODE_URL = "https://mobilelearn.chaoxing.com/widget/sign/pcStuSignController/checkSignCode"
SIGN_DETAIL_URL = "https://mobilelearn.chaoxing.com/newsign/signDetail"
ATTEND_INFO_URL = "https://mobilelearn.chaoxing.com/v2/apis/sign/getAttendInfo"
GROUP_ATTEND_LIST_URL = "https://mobilelearn.chaoxing.com/widget/sign/group/pcTeaSignGroupController/getAttendList"
GROUP_ATTEND_COUNT_URL = "https://mobilelearn.chaoxing.com/widget/sign/group/pcTeaSignGroupController/getCount"
GROUP_SIGN_DETAIL_URL = "https://mobilelearn.chaoxing.com/sign/getSignDetail"

ACTIVE_LIST_URL = "https://mobilelearn.chaoxing.com/ppt/activeAPI/taskactivelist"
ACTIVE_LIST_WEB_URL = "https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist"
ACTIVE_INFO_WEB_URL = "https://mobilelearn.chaoxing.com/v2/apis/active/getPPTActiveInfo"
JOIN_CLASS_TIME_URL = "https://mooc1-api.chaoxing.com/gas/clazzperson"

QUIZ_CHECK_STATUS_URL = "https://mobilelearn.chaoxing.com/pptTestPaperStu/checkActiveStatus"
QUIZ_SUBMIT_URL = "https://mobilelearn.chaoxing.com/v2/apis/studentQuestion/doQuestionAnswering"
QUIZ_DETAIL_URL = "https://mobilelearn.chaoxing.com/v2/apis/quiz/quizDetail"
QUIZ_DETAIL2_URL = "https://mobilelearn.chaoxing.com/v2/apis/quiz/quizDetail2"
VOTE_SUBMIT_URL = "https://mobilelearn.chaoxing.com/widget/quickvote/doQuestion"
QUESTIONNAIRE_SUBMIT_URL = "https://mobilelearn.chaoxing.com/v2/apis/studentQuestion/doQuestion"
EVALUATE_SUBMIT_URL = "https://mobilelearn.chaoxing.com/v2/apis/score/stuSubmitAnswer"
EVALUATE_DETAIL_URL = "https://mobilelearn.chaoxing.com/v2/apis/score/getStuScoreDetail"

TOPIC_GET_URL = "https://groupyd.chaoxing.com/apis/topic/getTopic"
TOPIC_REPLIES_URL = "https://groupyd.chaoxing.com/apis/invitation/getReplys2"
TOPIC_ADD_REPLY_URL = "https://groupyd.chaoxing.com/apis/invitation/addReply"

PAN_TOKEN_URL = "https://pan-yz.chaoxing.com/api/token/uservalid"
PAN_CRC_URL = "https://pan-yz.chaoxing.com/api/crcStorageStatus"
PAN_UPLOAD_URL = "https://pan-yz.chaoxing.com/upload"
FACE_ID_URL = "https://passport2-api.chaoxing.com/api/getUserFaceid"
FACE_ENC_URL = "https://mobilelearn.chaoxing.com/pptSign/check-face-result"

#: 活动类型（activeType）→ 中文名
ACTIVE_TYPES: dict[int, str] = {
    2: "签到", 4: "抢答", 5: "主题讨论", 11: "选人", 14: "问卷", 17: "直播",
    23: "评分", 35: "分组任务", 40: "PPT课堂", 42: "随堂练习", 43: "投票",
    45: "通知", 46: "学生反馈", 47: "计时器", 49: "白板", 51: "同步课堂",
    54: "定时签到", 56: "超星课堂", 59: "抽签", 64: "腾讯会议", 68: "互动练习",
    74: "签退", 77: "AI实践",
}

#: 签到子类型（otherId）→ 内部键
SIGN_TYPE_INDEX: dict[int, str] = {
    0: "normal", 2: "qrcode", 3: "pattern", 4: "location", 5: "code",
}
SIGN_TYPE_LABEL: dict[str, str] = {
    "normal": "普通签到", "qrcode": "二维码签到", "pattern": "手势签到",
    "location": "位置签到", "code": "签到码签到",
}

#: 签到状态码 → 中文（来自 attendInfo.status）
SIGN_STATUS_TEXT: dict[int, str] = {
    0: "未签到", 1: "已签到", 2: "教师代签", 4: "请假", 5: "缺勤", 7: "病假",
    8: "事假", 9: "迟到", 10: "早退", 11: "签到已过期", 12: "公假",
}

#: 一门课的签到位置（用户可保存，省得每次手填）
_DEFAULT_TIMEOUT = 15


# ====================================================================== 工具函数

def md5_hex(text: Any) -> str:
    return hashlib.md5(str(text).encode("utf-8")).hexdigest()


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad = block_size - (len(data) % block_size)
    return data + bytes([pad]) * pad


def aes_ecb_encrypt(text: str, key: str) -> str:
    """AES/ECB/PKCS7 + base64（设备指纹用）。"""
    raw = _pkcs7_pad(str(text).encode("utf-8"))
    ecb = pyaes.AESModeOfOperationECB(key.encode("utf-8"))
    out = b""
    for i in range(0, len(raw), 16):
        out += ecb.encrypt(raw[i:i + 16])
    return base64.b64encode(out).decode("ascii")


def new_uuid() -> str:
    return str(uuid.uuid4())


def unique_id() -> str:
    """伪设备唯一标识（与 ``_c_0_`` 的生成方式一致）。"""
    return new_uuid().replace("-", "")


def device_code() -> str:
    """设备指纹：AES-ECB(伪 OAID)。"""
    return aes_ecb_encrypt(new_uuid(), DEVICE_CODE_KEY)


def _dart_quote(value: Any) -> str:
    """对齐 Dart ``Uri.encodeComponent`` 的转义集（多保留 ``!*'()``）。"""
    return quote(str(value), safe="!~*'()")


#: 伪装移动端（学习通 App）的 UA —— 移动端接口会校验，浏览器 UA 直接 403 /
#: 返回「请用学习通客户端打开活动列表」。字段来自 App 6.7.4（@Kalimdor 正式版）。
_UA_DEVICE = "(device:Pixel 9 Pro) Language/zh_CN com.chaoxing.mobile/ChaoXingStudy_3_6.7.4_android_phone_10940_314 (@Kalimdor)"
_UA_SYSTEM = "Dalvik/2.1.0 (Linux; U; Android 16; Pixel 9 Pro Build/BP4A.260205.002)"


def app_user_agent() -> str:
    """构造带 schild 签名的 App User-Agent。"""
    tail = f"{_UA_DEVICE}_{unique_id()}"
    schild = md5_hex(f"(schild:{SCHILD_SALT}) {tail}")
    return f"{_UA_SYSTEM} (schild:{schild}) {tail}"


def enc_params(params: dict) -> dict:
    """给参数表补 ``_c_0_`` / ``token`` / ``_time`` / ``inf_enc``。

    ``inf_enc = md5(按插入序拼接的 query + "&DESKey=" + INF_ENC_KEY)``。
    """
    merged = dict(params)
    extra = {
        "_c_0_": unique_id(),
        "token": INF_ENC_TOKEN,
        "_time": str(int(time.time() * 1000)),
    }
    merged.update(extra)
    qs = "&".join(f"{_dart_quote(k)}={_dart_quote(v)}" for k, v in merged.items())
    extra["inf_enc"] = md5_hex(f"{qs}&DESKey={INF_ENC_KEY}")
    return extra


def sign_token(pairs: dict, salt: str = "") -> str:
    """把 dict 按键排序后拼成一串再过 md5（位置签名 / 人脸签名共用）。"""
    buf = "".join(f"{k}{pairs[k] or ''}" for k in sorted(pairs))
    return md5_hex(buf + (salt or ""))


def sign_type_name(index: Optional[int]) -> str:
    return SIGN_TYPE_LABEL.get(SIGN_TYPE_INDEX.get(index, ""), "签到")


def active_type_name(active_type: int) -> str:
    return ACTIVE_TYPES.get(int(active_type or 0), "课堂活动")


# ====================================================================== 数据模型

@dataclass
class Active:
    """一条课堂活动（活动列表项）。"""
    id: str
    type: int = 0
    name: str = ""
    description: str = ""
    start_time: int = 0
    url: str = ""
    status: bool = False
    sign_type_index: Optional[int] = None
    raw: dict = field(default_factory=dict)

    @property
    def type_name(self) -> str:
        return active_type_name(self.type)

    @property
    def sign_type(self) -> str:
        return SIGN_TYPE_INDEX.get(self.sign_type_index or -1, "")

    @property
    def is_sign(self) -> bool:
        return self.type in (2, 54, 74)

    @property
    def label(self) -> str:
        if self.is_sign:
            return sign_type_name(self.sign_type_index)
        return self.type_name

    @classmethod
    def from_json(cls, data: dict) -> "Active":
        try:
            atype = int(data.get("activeType") or 0)
        except (TypeError, ValueError):
            atype = 0
        return cls(
            id=str(data.get("id") or ""),
            type=atype,
            name=str(data.get("nameOne") or ""),
            description=str(data.get("nameTwo") or ""),
            start_time=int(data.get("startTime") or 0),
            url=str(data.get("url") or ""),
            status=str(data.get("status")) == "1" or data.get("status") is True,
            raw=dict(data),
        )


# ====================================================================== 客户端

class CXLive:
    """学习通课堂互动客户端。

    Parameters
    ----------
    session : requests.Session
        已经登录学习通的会话（``Chaoxing._session``）。
    uid : str
        当前账号的 uid（``Chaoxing.get_uid()``）。
    fid : str, optional
        机构 id（``Chaoxing.get_fid()``）。
    name : str, optional
        真实姓名，签到接口要带。
    cid, sc : str, optional
        设备信息（见模块 docstring），默认空串。
    """

    def __init__(self, session, uid: str, fid: Any = "0", name: str = "",
                 cid: str = "", sc: str = ""):
        self.session = session
        self.uid = str(uid or "")
        self.fid = str(fid or "0")
        self.name = str(name or "")
        self.cid = str(cid or "")
        self.sc = str(sc or "")
        self._device_code: Optional[str] = None
        # 移动端接口认 App UA：每个客户端实例固定一枚，避免签名与 UA 对不上
        self._app_headers = {"User-Agent": app_user_agent()}

    # ------------------------------------------------------------ HTTP 基座
    def _req(self, method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", _DEFAULT_TIMEOUT)
        kwargs["proxies"] = NO_PROXY
        headers = dict(self._app_headers)
        headers.update(kwargs.get("headers") or {})
        kwargs["headers"] = headers
        try:
            return self.session.request(method, url, **kwargs)
        except Exception as exc:  # 网络异常不往上抛，统一回落 None
            logger.warning("课堂互动请求失败 {} {}: {}", method, url, exc)
            return None

    def _json(self, method: str, url: str, **kwargs) -> Optional[dict]:
        resp = self._req(method, url, **kwargs)
        if resp is None:
            return None
        if resp.status_code != 200:
            logger.warning("课堂互动 HTTP {} <- {}", resp.status_code, url)
            return None
        try:
            return resp.json()
        except ValueError:
            logger.debug("课堂互动返回非 JSON：{}", (resp.text or "")[:160])
            return None

    def _text(self, method: str, url: str, **kwargs) -> Optional[str]:
        resp = self._req(method, url, **kwargs)
        return None if resp is None else resp.text

    @property
    def device_code(self) -> str:
        """每次签到重新生成（移动端也是每设备一次，这里保持稳定即可）。"""
        if self._device_code is None:
            self._device_code = device_code()
        return self._device_code

    # ------------------------------------------------------------ 活动列表
    def get_join_class_time(self, course: dict) -> str:
        params = {
            "courseid": course.get("courseId", ""),
            "clazzid": course.get("clazzId", ""),
            "userid": self.uid,
            "personid": course.get("cpi", ""),
            "view": "json",
            "fields": "clazzid,popupagreement,personid,clazzname,createtime",
        }
        data = self._json("GET", JOIN_CLASS_TIME_URL, params=params)
        try:
            return str(data["data"][0]["createtime"])
        except (TypeError, KeyError, IndexError):
            return ""

    def active_list(self, course: dict) -> list[Active]:
        """拉取课程活动列表（App 接口 + Web 接口合并）。

        App 侧 ``taskactivelist`` 带完整活动、Web 侧 ``activelist`` 补 ``otherId``
        （签到子类型）。两边都拿到才齐全。
        """
        course_id = str(course.get("courseId", ""))
        class_id = str(course.get("clazzId", ""))
        cpi = str(course.get("cpi", ""))
        join_time = self.get_join_class_time(course)

        base = {"courseId": course_id, "classId": class_id, "uid": self.uid,
                "cpi": cpi, "joinclasstime": join_time}
        base.update(enc_params(base))
        task = self._json("GET", ACTIVE_LIST_URL, params=base)

        web = self._json("GET", ACTIVE_LIST_WEB_URL, params={
            "fid": self.fid, "courseId": course_id, "classId": class_id,
            "showNotStartedActive": "0", "_": str(int(time.time() * 1000)),
        })

        if not task or task.get("result") != 1:
            logger.warning("活动列表(任务)获取失败：{}", (task or {}).get("errorMsg"))
            return []
        web_map: dict[str, dict] = {}
        try:
            for item in (web or {}).get("data", {}).get("activeList", []) or []:
                web_map[str(item.get("id"))] = item
        except (AttributeError, TypeError):
            web_map = {}

        result: list[Active] = []
        for item in task.get("activeList") or []:
            act = Active.from_json(item)
            other = web_map.get(act.id)
            if other:
                if act.status and not act.description:
                    act.description = str(other.get("nameFour") or "")
                if act.is_sign and other.get("otherId") is not None:
                    try:
                        act.sign_type_index = int(other["otherId"])
                    except (TypeError, ValueError):
                        pass
            result.append(act)
        result.sort(key=lambda a: a.start_time, reverse=True)
        return result

    def active_info_web(self, active_id: str) -> dict:
        """活动详情（含 otherId / ifphoto / numberCount / openCheckFaceFlag …）。"""
        data = self._json("GET", ACTIVE_INFO_WEB_URL, params={"activeId": str(active_id)})
        if not data or data.get("result") != 1:
            return {}
        return data.get("data") or {}

    def fetch_html(self, url: str) -> str:
        """按活动 url 取页面（随堂练习的题目藏在页面内联 JS 里）。"""
        if not url:
            return ""
        return self._text("GET", url) or ""

    # ------------------------------------------------------------ 签到
    def _identity(self) -> dict:
        return {"uid": self.uid, "fid": self.fid, "name": self.name}

    def sign_normal(self, course: dict, active_id: str, object_id: Optional[str] = None,
                    validate: Optional[str] = None) -> Optional[str]:
        """普通签到（可带拍照 objectId）。返回原始响应文本。"""
        params = {
            "activeId": str(active_id),
            "courseId": str(course.get("courseId", "")),
            "uid": self.uid,
            "clientip": "",
            "latitude": "-1",
            "longitude": "-1",
            "appType": "15",
            "fid": "0",
            "name": self.name,
            "deviceCode": self.device_code,
        }
        if object_id:
            params["objectId"] = object_id
        if validate:
            params["validate"] = validate
        return self._text("GET", SIGN_URL, params=params)

    def check_sign_code(self, active_id: str, sign_code: str) -> Optional[str]:
        """校验手势/签到码。返回 ``None`` 表示通过，否则返回错误原文。"""
        data = self._json("GET", CHECK_SIGN_CODE_URL, params={
            "activeId": str(active_id), "signCode": str(sign_code),
        })
        if data is None:
            return "校验失败：无响应"
        return data.get("errorMsg")

    def sign_code(self, course: dict, active_id: str, sign_code: str,
                  address: Optional[str] = None, latitude: Optional[float] = None,
                  longitude: Optional[float] = None,
                  validate: Optional[str] = None) -> Optional[str]:
        """手势码 / 数字签到码签到（可带定位）。"""
        params = {
            "activeId": str(active_id),
            "courseId": str(course.get("courseId", "")),
            "uid": self.uid,
            "clientip": "",
            "latitude": "-1",
            "longitude": "-1",
            "appType": "15",
            "fid": "0",
            "name": self.name,
            "signCode": str(sign_code),
            "deviceCode": self.device_code,
        }
        if validate:
            params["validate"] = validate
        if address and latitude is not None and longitude is not None:
            params["latitude"] = f"{float(latitude):.6f}"
            params["longitude"] = f"{float(longitude):.6f}"
            params["location"] = self._location_json(address, latitude, longitude)
            params["locationResult"] = self._location_result(address, latitude, longitude)
        return self._text("GET", SIGN_URL, params=params)

    def sign_location(self, course: dict, active_id: str, address: str,
                      latitude: float, longitude: float,
                      validate: Optional[str] = None,
                      face_id: Optional[str] = None,
                      face_enc: Optional[str] = None) -> Optional[str]:
        """位置签到（POST）。"""
        data = {
            "name": self.name,
            "address": address,
            "activeId": str(active_id),
            "courseId": str(course.get("courseId", "")),
            "uid": self.uid,
            "clientip": "",
            "latitude": f"{float(latitude):.6f}",
            "longitude": f"{float(longitude):.6f}",
            "fid": "0",
            "appType": "15",
            "ifTiJiao": "1",
            "deviceCode": self.device_code,
            "vpProbability": "-1",
            "vpStrategy": "",
            "currentFaceId": "",
            "ifCFP": "0",
            "faceEnc": "",
        }
        if validate:
            data["validate"] = validate
        if face_id:
            data["currentFaceId"] = face_id
            data["ifCFP"] = "1"
        if face_enc:
            data["faceEnc"] = face_enc
        data["locationResult"] = self._location_result(address, latitude, longitude)
        return self._text("POST", SIGN_URL, data=data)

    def sign_qrcode(self, course: dict, active_id: str, enc: str,
                    address: Optional[str] = None, latitude: Optional[float] = None,
                    longitude: Optional[float] = None,
                    enc2: Optional[str] = None, validate: Optional[str] = None,
                    face_id: Optional[str] = None,
                    face_enc: Optional[str] = None) -> Optional[str]:
        """二维码签到（可带定位 / 人脸）。"""
        params = {
            "enc": str(enc),
            "name": self.name,
            "activeId": str(active_id),
            "uid": self.uid,
            "clientip": "",
            "location": "",
            "latitude": "-1",
            "longitude": "-1",
            "fid": "0",
            "appType": "15",
            "deviceCode": self.device_code,
            "vpProbability": "",
            "vpStrategy": "",
            "enc2": "",
            "validate": "",
            "currentFaceId": "",
            "ifCFP": "0",
            "courseId": str(course.get("courseId", "")),
            "faceEnc": "",
        }
        if address and latitude is not None and longitude is not None:
            params["location"] = self._location_json(address, latitude, longitude)
            params["locationResult"] = self._location_result(address, latitude, longitude)
        if enc2 and validate:
            params["enc2"] = enc2
            params["validate"] = validate
        if face_id:
            params["currentFaceId"] = face_id
            params["ifCFP"] = "1"
        if face_enc:
            params["faceEnc"] = face_enc
        return self._text("GET", SIGN_URL, params=params)

    def sign_detail(self, active_id: str, code: Optional[str] = None) -> dict:
        """签到详情（二维码 ``code`` 用于校验是否最新）。"""
        params = {"activePrimaryId": str(active_id), "type": "1"}
        if code:
            params["msg"] = code
        data = self._json("GET", SIGN_DETAIL_URL, params=params)
        return data or {}

    def attend_info(self, active_id: str) -> Optional[dict]:
        """本人签到状态（``status`` 见 :data:`SIGN_STATUS_TEXT`）。"""
        data = self._json("GET", ATTEND_INFO_URL, params={
            "activeId": str(active_id), "moreClassAttendEnc": "",
        })
        if not data or data.get("result") != 1:
            return None
        return data.get("data")

    def _location_json(self, address: str, lat: float, lng: float) -> str:
        return json.dumps({
            "result": 1, "address": address,
            "longitude": float(lng), "latitude": float(lat),
        }, ensure_ascii=False, separators=(",", ":"))

    def _location_result(self, address: str, lat: float, lng: float) -> str:
        ts = str(int(time.time() * 1000))
        conf = {
            "data": json.dumps(
                {"latitude": float(lat), "longitude": float(lng), "address": address},
                ensure_ascii=False, separators=(",", ":")),
            "cxtime": ts,
            "cxcid": self.cid,
        }
        buf = "".join(f"{k}{conf[k]}" for k in sorted(conf))
        return json.dumps({
            "result": 1, "latitude": float(lat), "longitude": float(lng),
            "mockData": {"strategy": 0, "probability": -1},
            "locType": 161, "address": address,
            "signConfig": {
                "signToken": md5_hex(buf + self.sc),
                "cxcid": self.cid, "cxtime": ts,
            },
        }, ensure_ascii=False)

    # ------------------------------------------------------------ 群聊签到
    def group_sign(self, active_id: str, object_id: Optional[str] = None,
                   address: Optional[str] = None, latitude: Optional[float] = None,
                   longitude: Optional[float] = None) -> Optional[str]:
        params = {"activeId": str(active_id), "uid": self.uid, "clientip": ""}
        if object_id:
            params["objectId"] = object_id
        elif address:
            params.update({
                "address": address,
                "latitude": f"{float(latitude):.6f}",
                "longitude": f"{float(longitude):.6f}",
                "fid": "", "ifTiJiao": "1",
            })
        return self._text("GET", GROUP_SIGN_URL, params=params)

    def group_sign_detail(self, active_id: str) -> dict:
        data = self._json("GET", GROUP_SIGN_DETAIL_URL, params={"id": str(active_id)})
        return data or {}

    def group_attend_list(self, active_id: str) -> Optional[dict]:
        data = self._json("GET", GROUP_ATTEND_LIST_URL, params={"activeId": str(active_id)})
        if not data or data.get("result") != 1:
            return None
        return data.get("data")

    def group_attend_count(self, active_id: str) -> Optional[dict]:
        data = self._json("GET", GROUP_ATTEND_COUNT_URL, params={"activeId": str(active_id)})
        if not data or data.get("result") != 1:
            return None
        return data.get("data")

    # ------------------------------------------------------------ 主题讨论
    def topic_detail(self, topic_id: str) -> dict:
        # getTopic 的 inf_enc 基于**空业务参数**算（业务参数走 body），不能混进签名
        params = enc_params({})
        form = {"puid": self.uid, "maxW": "1080", "topicId": str(topic_id)}
        data = self._json("POST", TOPIC_GET_URL, params=params, data=form)
        if not data or data.get("result") != 1:
            return {}
        return data.get("data") or {}

    def topic_replies(self, topic_uuid: str) -> list:
        params = enc_params({})
        form = {"puid": self.uid, "uuid": str(topic_uuid), "maxW": "1080",
                "lastValue": "", "lastAuxValue": "", "order": "0"}
        data = self._json("POST", TOPIC_REPLIES_URL, params=params, data=form)
        try:
            return data["data"]["list"]
        except (TypeError, KeyError):
            return []

    def topic_add_reply(self, topic_uuid: str, content: str,
                        anonymous: bool = False) -> Optional[str]:
        """发一条主题讨论回复。返回 ``None`` 表示成功，否则是错误原文。"""
        params = {
            "puid": self.uid, "uuid": new_uuid(), "maxW": "1080",
            "topicUUID": str(topic_uuid), "anonymous": "1" if anonymous else "0",
        }
        params.update(enc_params(params))
        data = self._json("POST", TOPIC_ADD_REPLY_URL, params=params,
                          data={"content": content})
        if data is None:
            return "请求无响应"
        if data.get("result") == 1:
            return None
        return data.get("errorMsg") or "发布失败"

    # ------------------------------------------------------------ 评分
    def evaluate_submit(self, active_id: str, class_id: str, course_id: str,
                        score: int, content: str = "",
                        score_list: Optional[list] = None) -> bool:
        form = {
            "classId": str(class_id), "content": content or "",
            "score": str(int(score)),
            "scoreList": json.dumps(score_list or [], ensure_ascii=False),
            "activeId": str(active_id), "courseId": str(course_id),
        }
        data = self._json("POST", EVALUATE_SUBMIT_URL, data=form)
        return bool(data and data.get("result") == 1)

    def evaluate_detail(self, active_id: str, class_id: str, course_id: str) -> dict:
        data = self._json("GET", EVALUATE_DETAIL_URL, params={
            "activeId": str(active_id), "classId": str(class_id),
            "courseId": str(course_id),
        })
        return data or {}

    # ------------------------------------------------------------ 随堂练习 / 投票 / 问卷
    def quiz_check_status(self, class_id: str, active_id: str) -> Optional[bool]:
        data = self._json("POST", QUIZ_CHECK_STATUS_URL, data={
            "classId": str(class_id), "activePrimaryId": str(active_id), "appType": "15",
        })
        if not data or data.get("status") is None:
            return None
        return data.get("status") == 1

    def quiz_detail(self, active_id: str, v2: bool = False) -> dict:
        """投票 / 问卷 / 随堂练习的题目详情。"""
        if v2:
            url = QUIZ_DETAIL_URL
            params = {"activeId": str(active_id)}
        else:
            url = QUIZ_DETAIL2_URL
            params = {"activeId": str(active_id), "moreClassAttendEnc": "",
                      "DB_STRATEGY": "PRIMARY_KEY", "STRATEGY_PARA": "activeId"}
        data = self._json("GET", url, params=params)
        if not data:
            return {}
        return data.get("data") or {}

    def quiz_submit(self, class_id: str, course_id: str, active_id: str,
                    answer_json: str) -> dict:
        """随堂练习提交（``answer_json`` 是整份题目的 JSON 数组字符串）。"""
        params = {
            "activeId": str(active_id), "courseId": str(course_id),
            "classId": str(class_id), "DB_STRATEGY": "PRIMARY_KEY",
            "STRATEGY_PARA": "activeId",
        }
        data = self._json("POST", QUIZ_SUBMIT_URL, params=params,
                          headers={"Content-Type": "application/json"},
                          data=answer_json.encode("utf-8"))
        return data or {}

    def vote_submit(self, course_id: str, class_id: str, active_id: str,
                    question_id: str, option: str) -> dict:
        data = self._json("POST", VOTE_SUBMIT_URL, data={
            "courseId": str(course_id), "classId": str(class_id),
            "activeId": str(active_id), "questionId": str(question_id),
            "option": option,
        })
        return data or {}

    def questionnaire_submit(self, course_id: str, class_id: str, active_id: str,
                             answers: dict) -> dict:
        """问卷提交。``answers`` = ``{questionId: [答案, …]}``。"""
        parts = [f"preventsubmit=1&courseId={course_id}&classId={class_id}&activeId={active_id}"]
        for qid, values in answers.items():
            parts.append(f"&questionId={qid}")
            for value in values:
                parts.append(f"&answer{qid}={value}")
        body = "".join(parts)
        data = self._json("POST", QUESTIONNAIRE_SUBMIT_URL, data=body.encode("utf-8"),
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        return data or {}

    # ------------------------------------------------------------ 图片 / 人脸
    def upload_image(self, file_path: str) -> Optional[str]:
        """上传本地图片到学习通云盘，返回 objectId。"""
        token_data = self._json("GET", PAN_TOKEN_URL)
        token = (token_data or {}).get("_token")
        if not token:
            logger.warning("获取云盘 token 失败")
            return None
        try:
            crc = _file_crc(file_path)
        except OSError as exc:
            logger.warning("读取图片失败 {}: {}", file_path, exc)
            return None
        crc_data = self._json("GET", PAN_CRC_URL, params={
            "puid": self.uid, "crc": crc, "_token": token,
        })
        if crc_data and crc_data.get("result") and crc_data.get("exist"):
            try:
                return crc_data["data"]["objectid"]
            except (KeyError, TypeError):
                pass
        try:
            with open(file_path, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            logger.warning("读取图片失败 {}: {}", file_path, exc)
            return None
        stamp = time.strftime("%Y%m%d%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
        resp = self._req("POST", PAN_UPLOAD_URL,
                         params={"_from": "mobilelearn", "_token": token},
                         files={"file": (f"{stamp}.jpg", blob, "image/jpeg")},
                         data={"puid": self.uid})
        if resp is None or resp.status_code != 200:
            return None
        try:
            return resp.json()["data"]["objectId"]
        except (ValueError, KeyError, TypeError):
            logger.warning("上传图片返回异常：{}", (resp.text or "")[:160])
            return None

    def face_id(self) -> Optional[str]:
        """获取账号已采集的人脸 objectId（用于人脸签到复用）。"""
        enc = md5_hex(self.uid + GET_FACE_SALT)
        data = self._json("GET", FACE_ID_URL, params={"enc": enc})
        try:
            if data["result"] == 1 and data["data"]:
                return data["data"].get("objectid")
        except (TypeError, KeyError):
            pass
        return None

    def face_enc(self, active_id: str, face_id: str) -> Optional[str]:
        """人脸加密参数（签到带 faceEnc）。"""
        ts = str(int(time.time() * 1000))
        face_result = {
            "currentFaceId": str(face_id),
            "LiveDetectionStatus": "1",
            "collectStatus": "1",
            "cxcid": self.cid,
            "cxtime": ts,
        }
        face_result["signToken"] = sign_token(face_result, self.sc)
        data = self._json("GET", FACE_ENC_URL, params={
            "DB_STRATEGY": "PRIMARY_KEY", "STRATEGY_PARA": "activeId",
            "activeId": str(active_id),
            "faceResult": json.dumps(face_result, ensure_ascii=False),
        })
        if data and data.get("status") == 1:
            return data.get("enc")
        return None


def _file_crc(file_path: str) -> str:
    """学习通云盘的「非标准 CRC」：>1MB 取首尾各 512KB，附文件大小十六进制。"""
    import os
    size = os.path.getsize(file_path)
    with open(file_path, "rb") as fh:
        if size > 1048576:
            head = fh.read(524288)
            fh.seek(size - 524288)
            tail = fh.read(524288)
            blob = head + tail
        else:
            blob = fh.read(size)
    blob = blob + str(hex(size))[2:].encode("ascii")
    return hashlib.md5(blob).hexdigest()


# ====================================================================== 便捷构造

def build_from_chaoxing(cx) -> CXLive:
    """从已登录的 ``base.Chaoxing`` 实例造一个课堂互动客户端。"""
    uid = ""
    fid = "0"
    name = ""
    try:
        uid = cx.get_uid()
    except Exception:
        uid = ""
    try:
        fid = cx.get_fid()
    except Exception:
        fid = "0"
    try:
        name = cx.get_name() or ""
    except Exception:
        name = ""
    return CXLive(cx._session, uid=uid, fid=fid, name=name)

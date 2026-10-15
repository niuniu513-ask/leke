# -*- coding: utf-8 -*-
"""AI 答题服务配置面板（参考 mather 的「AI 模型设置」页）。

三步式配置：
  1. 选择模型服务（厂商卡片网格，选中即自动填好服务地址与默认模型）
  2. 填写连接信息（服务地址 / 模型名称 / 访问密钥），可一键检查是否能用
  3. 查看并切换当前生效的配置

面板只负责界面与连通性检测，真正的答题逻辑仍由项目原有 `api.answer.AI` 承担。
"""
from __future__ import annotations
from api.token_usage import tracked_completion

import time
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Callable

from PySide6.QtCore import QByteArray, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from gui_app import ai_providers as AIP
from gui_app import theme as T
from gui_app.ai_icons import get_svg
from gui_app.widgets import Card

TILE_COLS = 3

# mather 目录里没有的品牌，这里补上简易图标（纯几何形状，不涉及第三方标识）
EXTRA_SVG: dict[str, str] = {
    "custom": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="#5B6572" d="M10.4 3.6h3.2v6.8h6.8v3.2h-6.8v6.8h-3.2v-6.8H3.6v-3.2h6.8z"/>'
        "</svg>"
    ),
}


def svg_for(provider_id: str) -> str | None:
    return get_svg(provider_id) or EXTRA_SVG.get(provider_id)


class ElidedLabel(QLabel):
    """超宽时自动加省略号的标签（卡片副标题用，避免文字被硬裁）。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._apply()

    def setText(self, text: str):        # noqa: N802
        self._full = text or ""
        self._apply()

    def full_text(self) -> str:
        return self._full

    def _apply(self):
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, Qt.ElideRight, max(16, self.width())))

    def resizeEvent(self, event):        # noqa: N802
        super().resizeEvent(event)
        self._apply()


# ====================================================================== 图标
class GlyphIcon(QWidget):
    """品牌图标：优先渲染内嵌 SVG，缺图标时回退为品牌色文字标记。"""

    def __init__(self, provider_id: str = "", size: int = 30, parent=None):
        super().__init__(parent)
        self._pid = provider_id
        self._size = size
        self._svg: QSvgRenderer | None = None
        self._renderer_ok = False
        self.setFixedSize(size, size)
        self.set_provider(provider_id)

    def set_provider(self, provider_id: str):
        self._pid = provider_id or ""
        src = svg_for(self._pid)
        self._svg = None
        self._renderer_ok = False
        if src:
            r = QSvgRenderer(QByteArray(src.encode("utf-8")))
            if r.isValid():
                self._svg = r
                self._renderer_ok = True
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        s = self._size
        radius = max(6.0, s * 0.24)
        info = AIP.get(self._pid)
        bg = QColor(info.brand_bg if info else T.CARD_SUB)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(0, 0, s, s), radius, radius)

        pad = s * 0.22
        inner = QRectF(pad, pad, s - pad * 2, s - pad * 2)
        if self._renderer_ok and self._svg is not None:
            self._svg.render(p, inner)
        else:
            text = (info.short if info else "AI") or "AI"
            p.setPen(QColor(info.brand if info else T.TEXT_SUB))
            f = QFont(self.font())
            f.setPixelSize(int(s * (0.36 if len(text) > 2 else 0.46)))
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, text)
        p.end()


# ====================================================================== 检测线程
def normalize_probe_url(value: str) -> str:
    """Accept an API root or a pasted chat endpoint without duplicating its path."""
    parsed = urlsplit(value.strip())
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('请填写 HTTP(S) 服务地址，不要包含密钥、查询参数或片段')
    # Also validate the port before constructing the HTTP client.
    parsed.port
    path = parsed.path.rstrip('/')
    if path.endswith('/chat/completions'):
        path = path[:-len('/chat/completions')]
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


class TestWorker(QThread):
    """在后台验证「服务地址 + 密钥 + 模型」是否真的可用，并顺带拉取可用模型列表。"""

    done = Signal(bool, str, float, list)   # ok, message, latency_ms, models

    def __init__(self, base_url: str, api_key: str, model: str, proxy: str = "", parent=None):
        super().__init__(parent)
        self.base_url = (base_url or "").strip()
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.proxy = (proxy or "").strip()

    def run(self):  # noqa: D102
        self._probe_client = None
        self._probe_http = None
        try:
            self._probe()
        finally:
            for resource in (self._probe_client, self._probe_http):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

    def _probe(self):
        t0 = time.time()
        try:
            from openai import OpenAI
        except Exception as e:  # pragma: no cover
            self.done.emit(False, f"缺少 openai 库：{e}", 0.0, [])
            return

        if not self.base_url:
            self.done.emit(False, "请先填写服务地址", 0.0, [])
            return
        if not self.api_key:
            self.done.emit(False, "请先填写访问密钥", 0.0, [])
            return

        try:
            base_url = normalize_probe_url(self.base_url)
        except ValueError:
            self.done.emit(False, '服务地址格式无效，请填写 HTTP(S) API 地址，不要包含密钥或查询参数', 0.0, [])
            return
        kwargs: dict = {"base_url": base_url, "api_key": self.api_key,
                        "timeout": 25.0, "max_retries": 0}
        try:
            import httpx
            if self.proxy:
                kwargs["http_client"] = httpx.Client(proxy=self.proxy, timeout=25.0)
            else:
                # 未配代理时强制不吃环境代理（trust_env=False），避免撞死端口
                kwargs["http_client"] = httpx.Client(trust_env=False, timeout=25.0)
            self._probe_http = kwargs["http_client"]
        except Exception as e:
            self.done.emit(False, '代理或 HTTP 客户端配置无效，请检查设置', 0.0, [])
            return

        try:
            client = OpenAI(**kwargs)
            self._probe_client = client
        except Exception as e:
            self.done.emit(False, '初始化客户端失败，请检查连接设置', 0.0, [])
            return

        # ── 第一步：拉模型列表（不是所有服务都提供，失败不致命）
        models: list[str] = []
        list_err = ""
        try:
            resp = client.models.list()
            models = sorted({m.id for m in resp.data if getattr(m, "id", None)})
        except Exception as e:
            list_err = self._friendly(e)

        # ── 第二步：发一个最小对话请求，确认真能答题
        if not self.model:
            if list_err:
                self.done.emit(False, list_err + '；未填写模型名称，无法进一步验证', 0.0, [])
                return
            ms = f"服务地址可用，已读到 {len(models)} 个模型" if models else "服务地址可用"
            if list_err:
                ms = "服务地址可用，但没能读到模型列表；请手动填写模型名称"
            self.done.emit(True, f"{ms}（未填模型名，无法试答）", (time.time() - t0) * 1000, models)
            return

        try:
            completion = tracked_completion(client, 
                model=self.model,
                messages=[{"role": "user", "content": "只回答数字：1+1=?"}],
                max_tokens=200,
            )
            msg = completion.choices[0].message if completion.choices else None
            got = bool(msg and (msg.content or getattr(msg, "reasoning_content", None)))
            if got:
                extra = f"，顺带读到 {len(models)} 个可用模型" if models else ""
                self.done.emit(True, f"模型「{self.model}」可以正常答题{extra}",
                               (time.time() - t0) * 1000, models)
            else:
                self.done.emit(False, "接口有响应但内容为空，可能是模型名不对或该模型不支持对话",
                               0.0, models)
        except Exception as e:
            hint = self._friendly(e)
            if models:
                hint += f"；不过服务地址是通的，该账号有 {len(models)} 个模型可选"
            self.done.emit(False, hint, 0.0, models)

    @staticmethod
    def _friendly(e: Exception) -> str:
        """把底层异常翻译成用户能看懂的话。"""
        text = str(e)
        low = text.lower()
        if "401" in text or "invalid_api_key" in low or "incorrect api key" in low or "unauthorized" in low:
            return "密钥无效或已过期，请重新复制"
        if "403" in text:
            return "密钥没有权限（或余额不足），请到服务商后台确认"
        if "404" in text:
            return "服务地址或模型名不存在，检查地址是否漏了 /v1"
        if "429" in text:
            return "请求太频繁或已超额，稍后再试"
        if "insufficient" in low or "balance" in low or "quota" in low:
            return "账户余额或额度不足"
        if "timeout" in low or "timed out" in low:
            return "连接超时，可能需要配置代理"
        if "connect" in low or "network" in low or "ssl" in low or "proxy" in low:
            return "连不上服务地址，检查网络或代理设置"
        return '接口返回异常，请检查服务配置或联系服务商'


# ====================================================================== 小部件
class SectionTitle(QWidget):
    """分区标题：左侧主标题 + 说明，右侧补充信息。"""

    def __init__(self, title: str, hint: str = "", tail: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("StepTitle")
        left.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setObjectName("StepHint")
            h.setWordWrap(True)
            left.addWidget(h)
        lay.addLayout(left, 1)
        self.tail = QLabel(tail)
        self.tail.setObjectName("StepTail")
        self.tail.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.tail, 0)

    def set_tail(self, text: str):
        self.tail.setText(text)


class ProviderTile(QFrame):
    """厂商选择卡片：图标 + 名称 + 状态，可点选。"""

    clicked = Signal(str)

    def __init__(self, provider: AIP.Provider, parent=None):
        super().__init__(parent)
        self.pid = provider.id
        self.setObjectName("ProviderTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        self.icon = GlyphIcon(provider.id, 32)
        lay.addWidget(self.icon)

        col = QVBoxLayout()
        col.setSpacing(1)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.name = ElidedLabel(provider.name)
        self.name.setObjectName("TileName")
        name_row.addWidget(self.name, 1)
        if provider.id == "deepseek":
            ribbon = QLabel("推荐")
            ribbon.setObjectName("TileRibbon")
            ribbon.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            name_row.addWidget(ribbon, 0)
        col.addLayout(name_row)
        self.sub = ElidedLabel("在线服务")
        self.sub.setObjectName("TileSub")
        col.addWidget(self.sub)
        lay.addLayout(col, 1)

        self.check = QLabel("\u2713")
        self.check.setObjectName("TileCheck")
        self.check.setVisible(False)
        lay.addWidget(self.check)

        self._selected = False
        self._saved_model = ""

    def set_selected(self, value: bool):
        self._selected = bool(value)
        self.check.setVisible(self._selected)
        self.setProperty("sel", "true" if self._selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_saved_model(self, model: str):
        self._saved_model = model or ""
        info = AIP.get(self.pid)
        if self._saved_model:
            self.sub.setText(f"已保存 · {self._saved_model}")
        elif info and info.id == "custom":
            self.sub.setText("自己填写服务地址")
        elif info and info.note:
            self.sub.setText(info.note)
        else:
            self.sub.setText("在线服务")

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.pid)
        super().mouseReleaseEvent(event)


class StatusHero(QFrame):
    """顶部状态条：当前生效的模型与配置状态。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ApiHero")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        self.icon = GlyphIcon("", 38)
        lay.addWidget(self.icon)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.model = QLabel("还没有配置")
        self.model.setObjectName("HeroModel")
        col.addWidget(self.model)
        self.meta = ElidedLabel("完成下面 3 步后就能用你自己的密钥答题")
        self.meta.setObjectName("HeroMeta")
        col.addWidget(self.meta)
        lay.addLayout(col, 1)

        self.status = QLabel("未配置")
        self.status.setObjectName("HeroStatus")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        lay.addWidget(self.status)

    def update_state(self, base_url: str, key: str, model: str, provider_id: str = ""):
        pid = provider_id or AIP.guess_id(base_url)
        self.icon.set_provider(pid)
        ready = bool(base_url.strip() and key.strip() and model.strip())
        if ready:
            info = AIP.get(pid)
            self.model.setText(model.strip())
            self.meta.setText(f"{info.name if info else '自定义'} · {base_url.strip()}")
            self.status.setText("已配置")
            self.setProperty("state", "on")
        elif base_url.strip():
            self.model.setText("配置未完成")
            self.meta.setText("服务地址已填，还缺密钥或模型名")
            self.status.setText("未完成")
            self.setProperty("state", "warn")
        else:
            self.model.setText("还没有配置")
            self.meta.setText("完成下面 3 步后就能用你自己的密钥答题")
            self.status.setText("未配置")
            self.setProperty("state", "off")
        self.style().unpolish(self)
        self.style().polish(self)
        self.style().unpolish(self.status)
        self.style().polish(self.status)


class ResultBar(QFrame):
    """检测结果提示条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ResultBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(9)
        self.icon = QLabel("")
        self.icon.setObjectName("ResultIcon")
        self.icon.setFixedWidth(16)
        lay.addWidget(self.icon)
        self.text = QLabel("")
        self.text.setObjectName("ResultText")
        self.text.setWordWrap(True)
        lay.addWidget(self.text, 1)
        self.setVisible(False)

    def show_result(self, ok: bool, message: str, latency_ms: float = 0.0):
        body = message
        if ok and latency_ms:
            body += f"（{latency_ms:.0f} 毫秒）"
        self.icon.setText("\u2713" if ok else "!")
        self.text.setText(body)
        self.setProperty("kind", "ok" if ok else "bad")
        self.style().unpolish(self)
        self.style().polish(self)
        self.setVisible(True)


class ModelChip(QPushButton):
    """可用模型选项（点一下就填进模型名输入框）。"""

    def __init__(self, name: str, parent=None):
        super().__init__(name, parent)
        self.setObjectName("ModelChip")
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setToolTip(name)


# ====================================================================== 主面板
class AiConfigPanel(Card):
    """AI 答题配置面板。"""

    changed = Signal()                 # 任何输入变化（用于标记未保存）
    request_enable_ai = Signal()       # 要求把题库 provider 切到 AI
    request_save = Signal()            # 要求把面板内容写回配置

    def __init__(self, parent=None):
        super().__init__(
            "AI 大模型答题",
            "用你自己的 API Key 调用大模型答题，密钥只保存在本机的 config.ini 里。"
            "先选一家服务商，再粘贴密钥，就能让 AI 完成章节检测。",
            parent, icon="spark",
        )
        self._tiles: dict[str, ProviderTile] = {}
        self._current_pid = ""
        self._picked = False          # 用户是否手动点过卡片（点过则以卡片为准）
        self._worker: TestWorker | None = None
        self._models: list[str] = []
        self._model_box = None
        self._model_grid: QGridLayout | None = None

        self.hero = StatusHero()
        self.add(self.hero)

        # ---------------- 第 1 步
        self.add(self._divider())
        self.step1 = SectionTitle("第 1 步：选择模型服务",
                                  "点一下卡片就能自动填好服务地址和默认模型。")
        self.add(self.step1)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.ed_search = QLineEdit()
        self.ed_search.setObjectName("SearchBox")
        self.ed_search.setPlaceholderText("搜索服务名称，如 deepseek、kimi、通义")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self._apply_filter)
        head.addWidget(self.ed_search, 1)
        self.btn_more = QPushButton("显示全部")
        self.btn_more.setObjectName("GhostButton")
        self.btn_more.setCheckable(True)
        self.btn_more.setCursor(Qt.PointingHandCursor)
        self.btn_more.toggled.connect(lambda _: self._rebuild_grid())
        head.addWidget(self.btn_more)
        self.add(head)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        self.add(self.grid_host)

        # ---------------- 第 2 步
        self.add(self._divider())
        self.step2 = SectionTitle("第 2 步：填写连接信息",
                                  "大多数服务只需要粘贴密钥，服务地址已经自动填好。")
        self.add(self.step2)

        self.ed_ep = QLineEdit()
        self.ed_ep.setPlaceholderText("https://api.deepseek.com/v1")
        self.ed_ep.textChanged.connect(self._on_input_changed)
        # 只有用户亲手改地址时才取消卡片选中，程序自动填充不算
        self.ed_ep.textEdited.connect(self._on_ep_edited)
        self.add(self._labeled("服务地址", self.ed_ep,
                               "OpenAI 兼容地址，通常以 /v1 结尾。换服务商后这里要跟着改。"))

        self.ed_model = QLineEdit()
        self.ed_model.setPlaceholderText("如 deepseek-chat")
        self.ed_model.textChanged.connect(self._on_input_changed)
        self.add(self._labeled("模型名称", self.ed_model,
                               "不确定就先点下面的「检查是否能用」，程序会列出账号可用的模型。"))

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.ed_key = QLineEdit()
        self.ed_key.setPlaceholderText("把服务商网站上的密钥复制粘贴到这里")
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.textChanged.connect(self._on_input_changed)
        key_row.addWidget(self.ed_key, 1)
        self.btn_eye = QPushButton("显示")
        self.btn_eye.setObjectName("GhostButton")
        self.btn_eye.setCheckable(True)
        self.btn_eye.setCursor(Qt.PointingHandCursor)
        # 固定 56px 时「显示」只剩 4px 余量（QSS padding 一叠加就贴边），
        # 改成最小宽度，让它按文字自适应撑开。
        self.btn_eye.setMinimumWidth(56)
        self.btn_eye.toggled.connect(self._toggle_key_visible)
        key_row.addWidget(self.btn_eye)
        self.add(self._labeled("访问密钥（API Key）", key_row,
                               "密钥相当于登录密码，只写在 config.ini，不会上传到任何地方。"))

        self.btn_keypage = QPushButton("去哪申请密钥？")
        self.btn_keypage.setObjectName("LinkButton")
        self.btn_keypage.setCursor(Qt.PointingHandCursor)
        self.btn_keypage.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.btn_keypage.clicked.connect(self._open_key_page)
        self.add(self.btn_keypage)

        self.result = ResultBar()
        self.add(self.result)

        # 账号可用模型（检测成功后出现）
        self._model_box = QFrame()
        self._model_box.setObjectName("ModelBox")
        mb = QVBoxLayout(self._model_box)
        mb.setContentsMargins(12, 10, 12, 12)
        mb.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        mt = QLabel("账号可用的模型")
        mt.setObjectName("FieldLabel")
        head.addWidget(mt)
        head.addStretch(1)
        self._model_hint = QLabel("点一下即可填入")
        self._model_hint.setObjectName("FieldHelp")
        head.addWidget(self._model_hint)
        mb.addLayout(head)
        self.ed_mfilter = QLineEdit()
        self.ed_mfilter.setObjectName("SearchBox")
        self.ed_mfilter.setPlaceholderText("筛选模型名")
        self.ed_mfilter.setClearButtonEnabled(True)
        self.ed_mfilter.textChanged.connect(self._filter_models)
        mb.addWidget(self.ed_mfilter)
        self._model_grid = QGridLayout()
        self._model_grid.setHorizontalSpacing(7)
        self._model_grid.setVerticalSpacing(7)
        mb.addLayout(self._model_grid)
        self._model_box.setVisible(False)
        self.add(self._model_box)

        # 高级项
        self.adv = QFrame()
        self.adv.setObjectName("AdvBox")
        adv_lay = QVBoxLayout(self.adv)
        adv_lay.setContentsMargins(12, 10, 12, 10)
        adv_lay.setSpacing(9)
        self.cb_adv = QCheckBox("显示高级选项（请求间隔、代理）")
        self.cb_adv.toggled.connect(lambda v: self.adv_body.setVisible(v))
        adv_lay.addWidget(self.cb_adv)
        self.adv_body = QWidget()
        ab = QGridLayout(self.adv_body)
        ab.setContentsMargins(0, 0, 0, 0)
        ab.setHorizontalSpacing(14)
        ab.setVerticalSpacing(7)
        self.sp_minint = QLineEdit()
        self.sp_minint.setPlaceholderText("3")
        self.sp_minint.setFixedWidth(90)
        self.sp_minint.textChanged.connect(self._on_input_changed)
        self.ed_proxy = QLineEdit()
        self.ed_proxy.setPlaceholderText("可选，如 http://127.0.0.1:7890")
        self.ed_proxy.textChanged.connect(self._on_input_changed)
        ab.addWidget(QLabel("请求最小间隔(秒)"), 0, 0)
        ab.addWidget(self.sp_minint, 0, 1)
        ab.addWidget(QLabel("答题代理"), 1, 0)
        ab.addWidget(self.ed_proxy, 1, 1)
        ab.setColumnStretch(1, 1)
        adv_lay.addWidget(self.adv_body)
        self.adv_body.setVisible(False)
        self.add(self.adv)

        # 按钮行
        foot = QHBoxLayout()
        foot.setSpacing(9)
        tip = QLabel("密钥只保存在这台电脑")
        tip.setObjectName("FootTip")
        foot.addWidget(tip)
        foot.addStretch(1)
        self.btn_save = QPushButton("保存并使用")
        self.btn_save.setObjectName("GhostButton")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.clicked.connect(self.save_config)
        foot.addWidget(self.btn_save)
        self.btn_test = QPushButton("检查是否能用")
        self.btn_test.setObjectName("PrimaryButton")
        self.btn_test.setCursor(Qt.PointingHandCursor)
        self.btn_test.clicked.connect(self.run_test)
        foot.addWidget(self.btn_test)
        self.add(foot)

        # ---------------- 第 3 步
        self.add(self._divider())
        self.step3 = SectionTitle("第 3 步：当前生效的配置",
                                  "保存后启用 AI：先查题库，未命中再调用大模型。",
                                  "可以随时改")
        self.add(self.step3)
        self.summary = QFrame()
        self.summary.setObjectName("SavedRow")
        sl = QHBoxLayout(self.summary)
        sl.setContentsMargins(12, 10, 12, 10)
        sl.setSpacing(10)
        self.sum_icon = GlyphIcon("", 30)
        sl.addWidget(self.sum_icon)
        sc = QVBoxLayout()
        sc.setSpacing(1)
        self.sum_model = QLabel("还没有保存")
        self.sum_model.setObjectName("SavedModel")
        sc.addWidget(self.sum_model)
        self.sum_meta = QLabel("填好第 2 步后点「保存并使用」")
        self.sum_meta.setObjectName("SavedMeta")
        sc.addWidget(self.sum_meta)
        sl.addLayout(sc, 1)
        self.add(self.summary)

        # 所有控件就绪后再铺厂商卡片（网格的选中态依赖第 2 步的输入框）
        self._rebuild_grid()
        self._on_input_changed()

    # ------------------------------------------------------------ 小工具
    @staticmethod
    def _divider() -> QFrame:
        d = QFrame()
        d.setObjectName("Divider")
        d.setFixedHeight(1)
        return d

    def _labeled(self, text: str, field, help_text: str = "") -> QWidget:
        """标签 + 输入控件（也可传一行布局）+ 说明文字。"""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        lb = QLabel(text)
        lb.setObjectName("FieldLabel")
        lay.addWidget(lb)
        if isinstance(field, QWidget):
            lay.addWidget(field)
        else:
            lay.addLayout(field)
        if help_text:
            h = QLabel(help_text)
            h.setObjectName("FieldHelp")
            h.setWordWrap(True)
            lay.addWidget(h)
        return box

    def _open_key_page(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        info = AIP.get(self._current_pid)
        if info and info.key_url:
            QDesktopServices.openUrl(QUrl(info.key_url))
        elif info is None or info.id != "custom":
            QDesktopServices.openUrl(QUrl("https://platform.deepseek.com/api_keys"))

    def _toggle_key_visible(self, shown: bool):
        self.ed_key.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.btn_eye.setText("隐藏" if shown else "显示")

    # ------------------------------------------------------------ 厂商网格
    def _rebuild_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.grid_host.setUpdatesEnabled(False)
        self._tiles.clear()
        for i, p in enumerate(AIP.CATALOG):
            tile = ProviderTile(p)
            tile.clicked.connect(self._choose_provider)
            self._tiles[p.id] = tile
            self.grid.addWidget(tile, i // TILE_COLS, i % TILE_COLS)
        for c in range(TILE_COLS):
            self.grid.setColumnStretch(c, 1)
        self.grid_host.setUpdatesEnabled(True)
        self._apply_filter(self.ed_search.text())
        self._sync_tiles()

    def _apply_filter(self, text: str):
        q = (text or "").strip().lower()
        show_all = self.btn_more.isChecked()
        for p in AIP.CATALOG:
            tile = self._tiles.get(p.id)
            if tile is None:
                continue
            if q:
                hit = q in p.name.lower() or q in p.id.lower() or q in p.short.lower()
            else:
                hit = True if show_all else p.id in AIP.FEATURED_IDS
            tile.setVisible(hit)
        # 第 1 步的提示挂在第 1 步标题右侧
        if hasattr(self, "step1"):
            if q or show_all:
                self.step1.set_tail("")
            else:
                rest = len(AIP.CATALOG) - len(AIP.FEATURED_IDS)
                self.step1.set_tail(f"收录 {len(AIP.CATALOG)} 家，点「显示全部」还能看 {rest} 家")

    def _choose_provider(self, pid: str):
        info = AIP.get(pid)
        if info is None:
            return
        self._picked = True
        self._current_pid = pid
        if info.base_url:
            self.ed_ep.setText(info.base_url)
        if info.model:
            self.ed_model.setText(info.model)
        self._sync_tiles()
        self._on_input_changed()

    def _sync_tiles(self):
        model = self.ed_model.text().strip()
        for pid, tile in self._tiles.items():
            tile.set_selected(pid == self._current_pid)
            tile.set_saved_model(model if pid == self._current_pid else "")

    # ------------------------------------------------------------ 状态同步
    def _on_ep_edited(self, *_):
        self._picked = False

    def _effective_pid(self) -> str:
        """手动点过卡片就以卡片为准；否则按服务地址反推。"""
        if self._picked:
            return self._current_pid
        return AIP.guess_id(self.ed_ep.text().strip()) or self._current_pid

    def _on_input_changed(self, *_):
        pid = self._effective_pid()
        if pid != self._current_pid:
            self._current_pid = pid
            self._sync_tiles()
        self.hero.update_state(self.ed_ep.text(), self.ed_key.text(), self.ed_model.text(),
                               self._current_pid)
        self._refresh_summary()
        self.changed.emit()

    def _refresh_summary(self):
        ep = self.ed_ep.text().strip()
        model = self.ed_model.text().strip()
        info = AIP.get(self._current_pid)
        ready = bool(ep and self.ed_key.text().strip() and model)
        if ready:
            self.sum_model.setText(model)
            self.sum_meta.setText(f"{info.name if info else '自定义'} · {ep}")
        else:
            self.sum_model.setText("还没有保存")
            self.sum_meta.setText("填好第 2 步后点「保存并使用」")
        self.sum_icon.set_provider(self._current_pid)

    # ------------------------------------------------------------ 连通性检测
    def run_test(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self.btn_test.setEnabled(False)
        self.btn_test.setText("正在检查…")
        self.result.show_result(True, "正在连接服务商，请稍候…")
        self.result.setProperty("kind", "wait")
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

        self._worker = TestWorker(
            self.ed_ep.text(), self.ed_key.text(), self.ed_model.text(), self.ed_proxy.text(), self,
        )
        self._worker.done.connect(self._on_test_done)
        self._worker.start()

    def _on_test_done(self, ok: bool, message: str, latency_ms: float, models: list):
        self.btn_test.setEnabled(True)
        self.btn_test.setText("检查是否能用")
        self.result.show_result(ok, message, latency_ms)
        if models:
            self._models = list(models)
            self._render_models()
        self._worker = None

    def _render_models(self):
        if not self._models or self._model_grid is None or self._model_box is None:
            return
        while self._model_grid.count():
            item = self._model_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        cur = self.ed_model.text().strip()
        for i, name in enumerate(sorted(self._models)[:60]):
            chip = ModelChip(name)
            chip.setChecked(name == cur)
            chip.clicked.connect(lambda _=False, n=name: self._pick_model(n))
            self._model_grid.addWidget(chip, i // 2, i % 2)
        self._model_hint.setText(f"账号共 {len(self._models)} 个，点一下即可填入")
        self._filter_models(self.ed_mfilter.text())
        self._model_box.setVisible(True)

    def _filter_models(self, text: str):
        if self._model_grid is None:
            return
        q = (text or "").strip().lower()
        for i in range(self._model_grid.count()):
            w = self._model_grid.itemAt(i).widget()
            if isinstance(w, ModelChip):
                w.setVisible(q in w.text().lower() if q else True)

    def _pick_model(self, name: str):
        self.ed_model.setText(name)
        if self._model_grid is not None:
            for i in range(self._model_grid.count()):
                w = self._model_grid.itemAt(i).widget()
                if isinstance(w, ModelChip):
                    w.setChecked(w.text() == name)
        self._sync_tiles()

    # ------------------------------------------------------------ 读写
    def load(self, getter: Callable[[str, str, str], str]):
        """getter(section, key, fallback) —— 由调用方注入，避免面板直接依赖 store。"""
        self.ed_ep.setText(getter("tiku", "endpoint", ""))
        self.ed_key.setText(getter("tiku", "key", ""))
        self.ed_model.setText(getter("tiku", "model", ""))
        self.sp_minint.setText(getter("tiku", "min_interval_seconds", "3"))
        self.ed_proxy.setText(getter("tiku", "http_proxy", ""))
        self._current_pid = AIP.guess_id(self.ed_ep.text())
        self._picked = False
        self._sync_tiles()
        self.hero.update_state(self.ed_ep.text(), self.ed_key.text(), self.ed_model.text(),
                               self._current_pid)
        self._refresh_summary()
        if self.ed_proxy.text().strip() or self.sp_minint.text().strip() not in ("", "3"):
            self.cb_adv.setChecked(True)

    def values(self) -> dict[tuple[str, str], str]:
        interval = self.sp_minint.text().strip() or "3"
        try:
            float(interval)
        except ValueError:
            interval = "3"
        return {
            ("tiku", "endpoint"): normalize_probe_url(self.ed_ep.text()) if self.ed_ep.text().strip() else '',
            ("tiku", "key"): self.ed_key.text().strip(),
            ("tiku", "model"): self.ed_model.text().strip(),
            ("tiku", "min_interval_seconds"): interval,
            ("tiku", "http_proxy"): self.ed_proxy.text().strip(),
        }

    def current(self) -> dict:
        v = self.values()
        return {k[1]: val for k, val in v.items()}

    def save_config(self):
        if not self.ed_ep.text().strip() or not self.ed_key.text().strip():
            self.result.show_result(False, "服务地址和访问密钥都要填，才能保存")
            return
        try:
            endpoint = normalize_probe_url(self.ed_ep.text())
        except ValueError:
            self.result.show_result(False, '请填写有效的 HTTP(S) API 地址，不要包含密钥或查询参数')
            return
        self.ed_ep.setText(endpoint)
        self.request_save.emit()

    def notify_saved(self, ok: bool, message: str):
        """由外部在真正落盘后调用，给用户一个明确回执。"""
        self.result.show_result(ok, message)
        self._refresh_summary()
        self.hero.update_state(self.ed_ep.text(), self.ed_key.text(), self.ed_model.text(),
                               self._current_pid)

    def set_provider_enabled_hint(self, enabled: bool):
        self.step3.set_tail("已在题库来源中启用" if enabled else "保存后会自动启用")

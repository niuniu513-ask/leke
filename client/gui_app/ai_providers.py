# -*- coding: utf-8 -*-
"""AI 答题服务厂商目录。

桌面版通过（兼容 OpenAI 格式的）大模型接口答题，套用的是项目原有 `api.answer.AI`，
它用 `openai` 库以 `base_url + api_key` 访问，所以这里只收录 OpenAI 兼容的端点。
参考 mather 项目的 provider catalog 整理。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    short: str                      # 无图标时的文字回退
    base_url: str = ""              # 服务地址（OpenAI base_url，通常带 /v1）
    model: str = ""                 # 默认模型
    key_url: str = ""               # 去哪个页面申请密钥
    note: str = ""
    featured: bool = False          # 是否在未搜索时优先展示
    brand: str = "#5B6572"          # 品牌主色（用于回退标记）
    brand_bg: str = "#F1F5F9"       # 品牌底色


CATALOG: list[Provider] = [
    Provider(
        id="deepseek", name="DeepSeek", short="DS",
        base_url="https://api.deepseek.com/v1", model="deepseek-chat",
        key_url="https://platform.deepseek.com/api_keys",
        note="国内直连稳定，价格低，只需填密钥",
        featured=True, brand="#4C6BFD", brand_bg="#EAF0FF",
    ),
    Provider(
        id="siliconflow", name="硅基流动", short="硅",
        base_url="https://api.siliconflow.cn/v1", model="THUDM/glm-4-9b-chat",
        key_url="https://cloud.siliconflow.cn/account/ak",
        note="注册免实名绑卡，glm-4-9b-chat 等模型永久免费",
        featured=True,
        brand="#6E28F5", brand_bg="#F0EBFF",
    ),
    Provider(
        id="zhipu", name="智谱 GLM", short="智谱",
        base_url="https://open.bigmodel.cn/api/paas/v4", model="glm-4-flash",
        key_url="https://open.bigmodel.cn/usercenter/apikeys",
        note="有免费模型可用", featured=True,
        brand="#3859FF", brand_bg="#EAF1FB",
    ),
    Provider(
        id="moonshot", name="Kimi", short="Kimi",
        base_url="https://api.moonshot.cn/v1", model="moonshot-v1-8k",
        key_url="https://platform.moonshot.cn/console/api-keys",
        featured=True, brand="#5F5AB8", brand_bg="#EFEDFF",
    ),
    Provider(
        id="qwen", name="阿里千问", short="千问",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", model="qwen-plus",
        key_url="https://bailian.console.aliyun.com/?apiKey=1",
        featured=True, brand="#3D75AD", brand_bg="#EAF5FF",
    ),
    Provider(
        id="volcengine", name="豆包", short="豆包",
        base_url="https://ark.cn-beijing.volces.com/api/v3", model="",
        key_url="https://console.volcengine.com/ark",
        note="模型名要填「接入点 ID」，不能直接填模型名", featured=True,
        brand="#5F80EA", brand_bg="#EAF3FF",
    ),
    Provider(
        id="tencent", name="腾讯混元", short="混元",
        base_url="https://api.lkeap.cloud.tencent.com/v1", model="hunyuan-turbo",
        key_url="https://console.cloud.tencent.com/lkeap",
        brand="#0055E9", brand_bg="#EAF3FF",
    ),
    Provider(
        id="baidu", name="百度文心", short="文心",
        base_url="https://qianfan.baidubce.com/v2", model="ernie-4.0-turbo-8k",
        key_url="https://console.bce.baidu.com/iam/#/iam/apikey/list",
        brand="#167ADE", brand_bg="#EAF3FF",
    ),
    Provider(
        id="minimax", name="MiniMax", short="MM",
        base_url="https://api.minimax.chat/v1", model="MiniMax-Text-01",
        key_url="https://platform.minimaxi.com/user-center/basic-information/interface-key",
        brand="#F03D5A", brand_bg="#FFF0F3",
    ),
    Provider(
        id="xiaomi", name="小米 MiMo", short="小米",
        base_url="https://api.xiaomimimo.com/v1", model="mimo-v2-flash",
        key_url="https://platform.xiaomimimo.com/",
        brand="#B7512C", brand_bg="#FFF0E9",
    ),
    Provider(
        id="spark", name="讯飞星火", short="星火",
        base_url="https://spark-api-open.xf-yun.com/v1", model="4.0Ultra",
        key_url="https://console.xfyun.cn/services/cbm",
        brand="#297FE1", brand_bg="#EAF3FF",
    ),
    Provider(
        id="baichuan", name="百川智能", short="百川",
        base_url="https://api.baichuan-ai.com/v1", model="Baichuan4",
        key_url="https://platform.baichuan-ai.com/console/apikey",
        brand="#FF6832", brand_bg="#FFF0EB",
    ),
    Provider(
        id="openai", name="OpenAI", short="AI",
        base_url="https://api.openai.com/v1", model="gpt-4o-mini",
        key_url="https://platform.openai.com/api-keys",
        note="国内直连通常需要代理，可填在下方「答题代理」",
        brand="#2F806C", brand_bg="#E8F6F0",
    ),
    Provider(
        id="gemini", name="Gemini", short="GM",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-2.0-flash",
        key_url="https://aistudio.google.com/app/apikey",
        note="走 Google 的 OpenAI 兼容层，国内通常需要代理",
        brand="#658BA8", brand_bg="#EAF3FF",
    ),
    Provider(
        id="xai", name="Grok", short="X",
        base_url="https://api.x.ai/v1", model="grok-3-mini",
        key_url="https://console.x.ai/",
        note="国内通常需要代理",
        brand="#555F6E", brand_bg="#EEF1F4",
    ),
    Provider(
        id="anthropic", name="Claude", short="CL",
        base_url="", model="",
        key_url="https://console.anthropic.com/settings/keys",
        note="Anthropic 官方接口不是 OpenAI 格式，请填中转服务的地址",
        brand="#A36B35", brand_bg="#FFF3E6",
    ),
    Provider(
        id="custom", name="自定义", short="自定",
        base_url="", model="",
        note="自己填写服务地址",
        featured=True, brand="#5B6572", brand_bg="#F1F5F9",
    ),
]

BY_ID: dict[str, Provider] = {p.id: p for p in CATALOG}

# 未搜索时优先展示的厂商
FEATURED_IDS: list[str] = [p.id for p in CATALOG if p.featured]


def get(provider_id: str) -> Provider | None:
    return BY_ID.get(provider_id)


def guess_id(base_url: str) -> str:
    """按服务地址反推厂商 id，用于导入已有配置时高亮对应卡片。"""
    url = (base_url or "").strip().lower()
    if not url:
        return ""
    for p in CATALOG:
        if p.base_url and p.id != "custom" and p.base_url.lower() in url:
            return p.id
    # 少量别名，覆盖常见自定义写法
    aliases = {
        "deepseek.com": "deepseek",
        "siliconflow": "siliconflow",
        "bigmodel.cn": "zhipu",
        "moonshot": "moonshot",
        "dashscope": "qwen",
        "volces.com": "volcengine",
        "hunyuan": "tencent",
        "baidubce": "baidu",
        "minimax": "minimax",
        "xiaomimimo": "xiaomi",
        "xf-yun": "spark",
        "baichuan": "baichuan",
        "openai.com": "openai",
        "generativelanguage": "gemini",
        "x.ai": "xai",
        "anthropic": "anthropic",
    }
    for key, pid in aliases.items():
        if key in url:
            return pid
    return "custom"

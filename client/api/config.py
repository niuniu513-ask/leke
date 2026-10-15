# -*- coding: utf-8 -*-
class GlobalConst:
    AESKey = "u2oh6Vu^HWe4_AES"
    COOKIES_PATH = "cookies.txt"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"'
    }
    VIDEO_HEADERS = {
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842",
    }
    AUDIO_HEADERS = {
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2025-0725-1842",
    }

    THRESHOLD = 1

    # 登录等关键请求的超时（秒）。必须设置：在「路由丢包 / 代理端口开着但不转发」
    # 这类半死网络下，不发超时的 socket 会一直挂着，界面永远停在「登录中…」。
    HTTP_TIMEOUT = 15

    # 视频学习模式：
    #   fast  - 一次上报到视频末尾（「秒过」）。任务点立刻变绿，但平台按
    #           「真实间隔 vs playingTime 增量」判定，学习时长几乎不增长。
    #   timed - 按真实节奏逐段上报心跳（playingTime 从 0 递增到 duration），
    #           任务点与学习时长一起拿，代价是耗时 ≈ 视频总长 ÷ 倍速。
    # 由 main.init_chaoxing / StudyScheduler 在启动时按 config.ini 覆盖。
    VIDEO_MODE = "fast"

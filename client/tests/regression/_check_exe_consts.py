# -*- coding: utf-8 -*-
"""扫描打包后的 exe，确认关键常量 / 参数名 / 模块真的进了 PYZ。

用途：防止「源码改了但没进包」这种假通过。
字符串可能在 f-string 片段里、dict 键会以元组落在 co_consts、
参数名落在 co_varnames，所以这几处都要采。

用法：
    .venv\\Scripts\\python.exe tests/regression/_check_exe_consts.py <exe路径>
"""
from __future__ import annotations

import sys

from PyInstaller.archive.readers import CArchiveReader

#: 必须在 PYZ 里能找到的模块（少一个都说明包装残了）
WANT_MODULES = [
    "gui_app",
    "gui_app.app",
    "gui_app.theme",
    "gui_app.icons",          # v1.4.0 新增，没进 --hidden-import，靠静态分析收
    "gui_app.widgets",
    "gui_app.store",
    "gui_app.stream",
    "gui_app.main_window",
    "gui_app.worker",
    "gui_app.study_engine",
    "gui_app.page_account",
    "gui_app.page_courses",
    "gui_app.page_run",
    "gui_app.page_settings",
    "gui_app.page_ai",        # v1.5.3 AI 大模型独立页
    "gui_app.ai_panel",
    "gui_app.ai_providers",
    "gui_app.ai_icons",
    "gui_app.profiles",       # v1.5.0 多账号档案
    "gui_app.unipus_engine",  # v1.5.2 U校园适配层
    "gui_app.browser_workspace",
    "gui_app.page_unipus",    # v1.5.2 U校园页签
    "api.unipus",             # v1.5.2 vendor 包
    "gui_app.platform_accounts",  # v1.5.5 平台账号档案（U校园/雨课堂共用）
    "gui_app.yuketang_engine",  # v1.5.4 雨课堂适配层
    "gui_app.page_yuketang",    # v1.5.4 雨课堂页签
    "api.yuketang",             # v1.5.4 雨课堂 vendor 包
    "api.yuketang.client",      # v1.5.4 vendor 引擎主体
    "api.yuketang._vendor_qrcode",       # v1.5.4 二维码纯 Python 兜底副本
    "api.yuketang._vendor_qrcode.main",  # 二维码编码核心
    "api.unipus.unipus_engine",
    "api.unipus.audio_recognizer",
    "gui_app.zhihuishu_engine",  # v1.5.8 智慧树适配层
    "gui_app.page_zhihuishu",    # v1.5.8 智慧树页签
    "gui_app.page_profile",      # v1.6.3 用户中心（用户名 / 头像 / 设备）
    "gui_app.page_live",         # v1.6.9 课堂互动页（签到 / 讨论 / 投票 / 随堂练习）
    "api.cx_live",               # v1.6.9 学习通课堂互动协议层（移动端接口）
    "api.ykt_live",              # v1.6.9 雨课堂课堂互动协议层
    "api.zhihuishu",             # v1.5.8 智慧树 vendor 包
    "api.zhihuishu.client",      # v1.5.8 vendor 引擎主体（纯接口 + AES）
    "api.zhihuishu.zd_utils",    # v1.5.8 AES 工具（改用项目自带 pyaes）
    "api.kc_client",             # v1.6.0 授权闸门客户端（登录/心跳/事件）
    "gui_app.login_dialog",      # v1.6.0 登录对话框（注册跳浏览器门户）
    "gui_app.gate",              # v1.6.0 授权闸门编排（含 72h 离线宽限）
    "gui_app.brand",             # v1.6.1 品牌图标（内置 SVG，函数内延迟导入）
    "selenium",
    "selenium.webdriver",
    "openai",
]

#: 必须能在包内找到的字符串（v1.5.0 多账号 + 改名乐课）
WANT_STRINGS = [
    # —— 自动续刷（v1.3.1）
    "自动续刷",
    "续刷已达兜底上限",
    "遍没有新完成的章节，续刷结束",
    "不需要再手动跑第二遍",
    "on_job",
    "already_finished",
    "sig_study_pass",
    "MAX_PASSES",
    "NO_PROGRESS_PASSES",
    # —— 界面（v1.4.0，v1.5.0 文案重写后仍保留的部分）
    "课程与进度",
    "还没有课程数据",
    "check-circle",           # icons.ICONS 的键：证明 icons.py 进包了
    "BrandMark",              # theme.QSS 里的选择器
    # —— 品牌图标（v1.6.1）：内置 SVG 两档 + 兜底画法
    "ICON_FULL_SVG",
    "ICON_COMPACT_SVG",
    "COMPACT_BELOW",
    "345FE5",                 # 品牌主色（写在内嵌 SVG 的渐变里，v1.6.7 青绿）
    "linearGradient",
    "render_pixmap",
    "svg_for",
    "mark_pixmap",
    # —— 多账号 + 品牌（v1.5.0）
    "乐课",
    "在线课程学习助手",
    "账号登录",
    "账号列表",
    "＋ 添加",                 # v1.6.2 按钮并进卡头后改短（旧串「添加账号」已废弃）
    "管理学习通账号",          # v1.6.0 同步当前导航文案（旧串已随 UI 改版废弃）
    "账号档案",
    "accounts.json",
    "cookie_path",
    "session_key",
    "create_session",
    "sig_request_login",
    "参与刷课的账号",
    "关于乐课",
    # —— v1.5.0 重写后的界面文案（替代 v1.4.0 的旧措辞）
    "账号进度",                # v1.6.2 卡片副标题已删，标题保留
    "运行日志",
    "并行",
    "自动续刷 · 第",
    "leke.log",
    "绿色免安装",
    # —— 章节选择（v1.5.1）
    "chapter_sel",            # profiles 档案里的 dict 键
    "selected_points",        # worker._collect_tasks 过滤键（course dict 挂载）
    "set_chapters",           # profiles 持久化方法
    "ChapterDialog",          # page_courses 对话框类
    "sig_chapters",           # worker 信号
    "sig_chapters_request",   # page_courses 信号
    "sig_chapters_saved",     # main_window 接线
    "invalidate_chapter_cache",
    "load_chapters",
    "章节 · 已选",             # 按钮文案（f-string 拆片段，子串匹配）
    "读取中",
    "全不选",
    "只选未完成",
    "只刷",                    # 「X 只刷 N 章」日志片段
    # —— UI 修复（v1.5.1：运行页空态 + 退出登录按状态禁用）
    "NowEmpty",               # theme.QSS 选择器 / objectName
    "等待开始",                # lb_now_empty 文案片段
    "ChapterBtn",             # 章节按钮 QSS 类
    "ChapterState",           # 已完成绿标 QSS 类
    "DialogTitle",            # 对话框标题 QSS 类
    "_refresh_actions",       # page_account 按钮状态刷新
    # —— 从头重刷修复（v1.5.1b：章节级门穿透 + 测验防重复提交）
    "skip_work",              # process_job 参数：重刷已完成章节时跳过测验
    "从头重刷：",              # 门穿透日志片段（f-string 前缀）
    "重新处理以累计学习时长",   # 门穿透日志片段（f-string 后缀）
    "跳过章节检测（避免重复提交）",
    "全部章节已重新处理一遍，结束",  # 从头重刷 1 遍即停（GUI+CLI 共用语义）
    # —— 从头重刷修复（v1.5.1c：isPassed 保留 + 多 mArg 块 + job 字段移除兼容）
    "keep_passed",            # decode/base/main 参数：保留已通过任务点（co_varnames）
    "ispassed",               # job 键：任务点通过标记（防测验重复提交）
    "mArg 卡片块解析失败",      # 多 mArg 块遍历的告警片段（f-string 拆分，子串匹配）
    # —— 重播修复（v1.5.1d：服务端对已通过任务点恒回 isPassed=True 导致提前完成）
    "重播模式:",               # study_video 重播入口日志（f-string 前缀）
    "从头播放补时长",           # 重播日志片段
    "播放中:",                 # 心跳低频进度日志（解决播放期间零输出像卡死）
    "gui_mode",               # GUI 下禁用 tqdm（\r 帧是日志竞态源头）
    # —— 进度可视化（v1.5.1e：视频播放实时进度条）→ v1.5.1f 升级为并行槽
    "video_progress_cb",      # study_video 心跳回调（实例属性，CLI 为 None）
    "sig_video_progress",     # worker 信号（acc_id + 进度帧）
    "set_video_progress",     # RunPage/卡片路由方法
    "播放中",                  # 心跳进度日志（loguru 整条）+ 槽位 meta（f-string 片段）
    # —— 本地知识库（v1.5.1f：知识库文件夹 + 单例 + 多账号共享 + TikuLocal 离线选项）
    "知识库",                  # KNOWLEDGE_DIR_NAME（exe 旁知识库文件夹）
    "get_cache_dao",          # CacheDAO 进程内单例
    "knowledge_size",         # 知识库条目数（设置页展示）
    "_migrate_legacy",        # 旧根目录 cache.json 自动迁移
    "TikuLocal",              # 本地知识库 provider（纯离线，绝不联网）
    "_get_shared_tiku",       # worker 多账号共享题库实例（配置指纹重建）
    "PROVIDER_PRESET_GROUPS", # 设置页预设分组（本地优先 / 远端知识库 / 组合示例）
    "云端共享题库 + AI",
    "CloudCacheDAO",
    "knowledge_query",
    "knowledge_write",
    "knowledge_stats",
    "kc_knowledge_migrated",
    "导入旧本地题库",
    "1.6.26",
    "append_ai_provider",     # AI 一键启用（provider 为空时写 TikuLocal,AI）
    # —— AI 大模型独立页（v1.5.3：从参数设置页拆出 + 中介模式接线）
    "AI 大模型",               # 导航页签名 + 面板标题
    "AiPage",                 # 页面类
    "sig_save_requested",     # AI 页 → 主窗口「保存并使用」
    "sig_enable_requested",   # AI 页 → 主窗口「启用到题库」
    "_on_save_ai",            # 主窗口中介：provider + AI 字段一次落盘
    "provider_collect",       # 设置页只出 provider 一项
    "provider_names",         # 设置页 provider 公有读法
    "已保存到 config.ini，题库来源已启用 AI",   # 落盘回执
    # —— 并行任务点槽（v1.5.1f：多章节并行各占一行，不再互相覆盖）
    "_upsert_slot",           # 槽位建立/刷新
    "SlotRow",                # 槽位行 QSS 类名
    "SLOT_STALE_SEC",         # 槽位超时灰显阈值
    "_refresh_slot_text",     # 槽位文案刷新（i/n 或 播放进度）
    # —— U校园接入（v1.5.2：独立页签 + vendor 引擎 + Selenium Edge）
    "U校园",                  # 导航页签名
    "unipus.json",            # U校园账号配置文件（与 accounts.json 分离）
    "UnipusRunner",           # 适配层 runner（后台线程 + 泵）
    "UnipusPage",             # U校园页签类
    "_tab_views",             # 扫描结果视图（_element 不跨信号）
    "_scan_course_directory",  # 课程目录 Unit 页扫描（自 fluent_ui 抽取）
    "UCampusBot",             # vendor 引擎主类
    "AISolver",               # vendor 引擎答题器
    "gui_log_queue",          # vendor 引擎日志队列（泵线程转发）
    "PopupWatcher",           # vendor 弹窗自动关闭
    "skip_check",             # 永远跳过引擎环境检查（FFmpeg/Edge 引导不进 GUI）
    "连接并登录",              # U校园控制按钮
    "扫描任务列表",            # U校园控制按钮
    "处理选中任务 (",          # U校园控制按钮（f-string 片段，子串匹配）
    "快速处理当前页",          # U校园控制按钮
    "仅必修",                  # 任务清单勾选按钮
    "断开",                    # U校园控制按钮
    "_extract_course_task_section",  # section 标题抽取 JS
    "globe",                  # 导航图标键
    # —— U校园品牌视觉（v1.5.2b）
    "unipus",                 # 单色品牌图标键（导航/空态）
    "brand_pixmap",           # 彩色品牌标志渲染函数
    "UNIPUS_BRAND_SVG",       # 品牌标志 SVG 常量（主色渐变）
    "UnipusHero",             # Hero 品牌条 QSS/objectName
    "UnipusHeroMark",         # Hero 标志块（主色渐变圆角）
    "UnipusHeroTitle",        # Hero 标题
    "variant=\"unipus\"",     # unipus 主按钮 QSS 变体
    "TaskPanel",              # 任务面板 QSS
    # —— 视频进度条「一跳一跳」修复（v1.5.4）
    "SmoothBar",              # 自绘浮点进度条（替代 QProgressBar）
    "set_display",            # 插值直通口（不缓动，视频条专用）
    "VIDEO_REWIND_TOL",       # 「多大回退才算真重播」的容差
    "anchor_pt",              # 心跳锚点：引擎上报的播放秒数
    "SLOT_EXTRAPOLATE_SEC",   # 心跳断了就冻结外推（不许凭空编进度）
    "anchor_ts",              # 心跳锚点：到达时的本地墙钟
    "session",                # 播放会话号（重播判定）
    "_emit_video_frame",      # 引擎进度帧发射（阻塞式上报之前）
    "play_time",              # 帧字段：浮点播放秒
    # —— 导航分门别类 + 数据驱动（v1.5.4）
    "应用设置",                # 导航分组标题（配置层）
    # —— 导航分组 = 平台（v1.5.6：一个平台一组，组标题带品牌色圆点）
    "PlatformChip",           # 顶栏常驻平台 pill（objectName + QSS）
    "TopAccent",              # 顶栏下方 3px 品牌色条
    "PAGE_GROUP",             # 页面 key -> 分组（平台）
    "GROUP_BRAND",            # 平台 -> 品牌 key
    "BRAND_COLOR",            # 品牌 key -> 主色（侧栏圆点）
    "数据目录",                # 启动留痕那行日志（v1.5.6 起，便于核对用户跑的哪版）
    "PAGE_KEYS",              # 扁平页面 key 表
    "PAGE_INDEX",             # key -> 页面栈下标
    "_key_row",               # key -> 导航行号（分组标题会让两者不再相等）
    "last_page",              # 记住上次停留的页面
    "Ctrl+",                  # 导航项 tooltip 里的快捷键提示
    # —— 雨课堂接入（v1.5.4：扫码登录 + 心跳刷课 + 品牌视觉）
    "雨课堂",                  # 导航页签名 / Hero 标题
    "yuketang",               # 导航 key + 单色品牌图标键
    "yuketang.json",          # 雨课堂账号文件（与 accounts.json/unipus.json 隔离）
    "YuketangRunner",         # 适配层 runner（后台线程 + 信号）
    "YuketangPage",           # 雨课堂页签类
    "YuketangClient",         # vendor 引擎主类
    "login_by_qrcode",        # 扫码登录（WebSocket）
    "watch_course_videos",    # 按课程刷视频
    "list_courses",           # 课程列表接口
    "list_videos",            # 章节树 → 视频叶子
    "get_video_progress",     # 视频进度查询
    "render_qrcode_png",      # 二维码渲染（vendor 兜底）
    "_vendor_qrcode",         # vendor 副本模块名（证明真的进包了）
    "show_qrcode",            # 页面把二维码画进 QLabel（不弹系统看图器）
    "YuketangHero",           # Hero 品牌条 QSS/objectName
    "variant=\"yuketang\"",   # 青绿主按钮 QSS 变体
    "扫码登录",                # 登录按钮文案
    # —— 平台多账号并行（v1.5.5：U校园 / 雨课堂 都改成多账号 + AccountBar）
    "platform_accounts",      # 共享账号档案模块
    "AccountStore",           # 账号档案类（增删改查 + 旧单账号文件迁移）
    "migrate_legacy",         # 旧 unipus.json / yuketang.json 自动迁移
    "yuketang_accounts.json", # 雨课堂多账号档案文件
    "unipus_accounts.json",   # U校园多账号档案文件
    "AccountBar",             # 平台账号条控件
    "AccChip",                # 账号 chip 的 QSS 类名 / objectName
    "set_running",            # chip 运行态圆点（刷课中高频调用）
    "sig_login_step",         # 登录分步骤上报（排查"扫码没反应"）
    # —— v1.5.7：题库空配置防护 + 设置页 dirty 跟踪
    "_ready_check",           # 题库基类就绪钩子（缺 token/地址 → 发请求前停用）
    "配置不完整，已跳过",       # 引擎侧日志：不会向服务端发送题目
    "_config_warnings",       # 设置页「这样配跑起来会被跳过」提示
    "_wire_dirty",            # 设置页「未保存」徽标（改动不再静默丢失）
    "只保存在本机，不会上传",            # 侧栏文案（版本号只在状态栏显示一处）
    # —— 智慧树接入（v1.5.8：纯接口刷课 + 账密登录 + 共享课/校内课）
    "智慧树",                  # 导航页签名 / Hero 标题
    "zhihuishu",              # 导航 key + 单色品牌图标键
    "zhihuishu_accounts.json",  # 智慧树多账号档案文件
    "ZhihuishuRunner",        # 适配层 runner（后台线程 + 信号）
    "ZhihuishuPage",          # 智慧树页签类
    "Fucker",                 # vendor 引擎主类（fuckZHS）
    "TimeLimitExceeded",      # 引擎超时中断异常（停止机制靠它）
    "validateAccountAndPassword",  # 账密登录接口
    "getZhidaoList",          # 共享课（知到）列表
    "getHikeList",            # 校内学分课列表
    "getZhidaoContext",       # 共享课章节树
    "getHikeContext",         # 校内课章节树
    "ZhihuishuHero",          # Hero 品牌条 QSS/objectName
    "variant=\"zhihuishu\"",  # zhihuishu 主按钮 QSS 变体
    "共享课",                  # 「共享课（知到）」文案
    "校内学分课",              # 「校内学分课」文案
    "cookie 登录",            # 兜底通道按钮文案
    # —— 用户中心（v1.6.3：用户名 / 头像与网页共用同一份账号数据）
    "用户中心",                # 导航页签名
    "profile",                # 导航 key + 导航图标名（NAV_BRAND 里用主色）
    "ProfilePage",            # 用户中心页类
    "update_profile",         # kc_client 资料写入（用户名 / 头像）
    "/me/profile",            # 资料接口路径
    "fetch_avatar",           # 头像下载
    "unbind_device",          # 自助解绑设备
    "change_password",        # 修改密码（kc_client 接口 + /auth/change_password，改密已迁网页）
    "kc_nickname",            # 本地缓存的用户名（离线也能显示）
    "MiniStat",               # 账号概况小块 QSS 类名
    "DeviceRow",              # 设备行 QSS 类名
    "更换头像",                # 头像按钮文案
    "保存用户名",              # 用户名按钮文案
    "退出登录",                # 登出入口文案
    "sig_logout",             # 页面 → 主窗口的登出信号
    "apply_logout",           # 登出收尾（由主窗口确认后调用）
    # —— 密码入口（v1.6.6：改密表单迁到网页，客户端只留跳转）
    "密码与安全",              # 密码卡标题
    "打开网页修改密码",        # 跳 /profile 改密
    "忘记密码 / 找回账号",     # 跳 /forget（客户端登录框与用户中心共用文案）
    "/forget",                # 找回账号页面路径
    # —— 课堂互动（v1.6.9：学习通移动端协议 + 雨课堂课堂互动）
    "课堂互动",                # 导航页签名
    "live",                   # 导航 key
    "LivePage",               # 学习通课堂互动页类
    "SignDialog",             # 签到对话框（五类签到合一）
    "DiscussDialog",          # 主题讨论对话框（看帖 / 发帖）
    "Active",                 # 活动模型类名
    "CXLive",                 # 学习通课堂互动客户端
    "YKTLive",                # 雨课堂课堂互动客户端
    "stuSignajax",            # 签到接口路径（普通/位置/二维码共用）
    "taskactivelist",         # 活动列表（App 接口，会校验 App UA）
    "getPPTActiveInfo",       # 活动详情（拿签到子类型）
    "getTopic",               # 主题讨论详情
    "addReply",               # 主题讨论发帖
    "doQuestionAnswering",    # 随堂练习提交
    "quickvote",              # 投票提交
    "getUserFaceid",          # 已采集人脸 objectId
    "on-lesson",              # 雨课堂「正在上课」接口
    "checkin",                # 雨课堂签到接口
    "parse_quiz_html",        # 随堂练习题目抠取（页面内联 JS）
    "app_user_agent",         # App UA 伪装（移动端接口硬门槛）
    "ChaoXingStudy_3_6.7.4",  # UA 里的 App 版本号
    "inf_enc",                # 移动端接口签名参数
    "sign_token",             # 位置 / 人脸签名
    "课堂活动",                # 未知 activeType 的回落名
    "新活动监控",              # 监控卡标题（环信 IM 推送的桌面端等价实现）
    "开启监控",                # 监控开关文案
    "已开启新活动监控",         # 开启后的日志
    "_watch_tick",            # 轮询回调
    "_alert_new",             # 新活动提示（日志 + 卡片高亮 + 任务栏闪烁）
    "个新活动",                # 提示文案片段
]

#: 必须**不能**再出现的字符串（已删除的旧实现 / 开源痕迹）
BAN_STRINGS = [
    "刷课轮次",
    "parse_rounds",
    "AUTO_MAX_ROUNDS",
    "sig_study_round",
    "单轮刷课",
    "开源项目",
    "Samueli924",
    "学习通助手",
    # —— v1.5.3 移除 / 改名（GUI 已删，_bak_v150 不进包不校验）
    "current_notify",         # 通知配置读取（GUI 通知功能整体移除）
    "NOTIFY_PROVIDERS",       # 通知 provider 常量
    "_enable_ai_provider",    # 旧私有方法名，已公有化为 append_ai_provider
    # —— v1.5.4：导航改为数据驱动后不再需要的死样式
    "QLabel#NavGroup",        # 从未被引用的分组标题 QSS（改走 QListWidget 禁用项）
]


def walk(code, out: set, seen: set):
    """把 code 对象里所有字符串常量收集起来。

    注意：常量里会出现**多层嵌套元组**（例如 ``PAGES = [("账号登录","同时…","user"), …]``
    在字节码里是 tuple(tuple(...), tuple(...))），必须递归进元组，
    否则会漏掉只出现在嵌套元组里的文案，造成假阴性。
    """
    if id(code) in seen:
        return
    seen.add(id(code))

    def take(x):
        if isinstance(x, str):
            out.add(x)
        elif isinstance(x, (tuple, frozenset, list)):
            for y in x:
                take(y)
        elif hasattr(x, "co_consts"):
            walk(x, out, seen)

    for c in code.co_consts:
        take(c)
    for seq in (code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars):
        out.update(seq)


def main(exe: str) -> int:
    arch = CArchiveReader(exe)
    pyz = arch.open_embedded_archive("PYZ.pyz")
    toc = getattr(pyz, "toc", None) or {}

    def toc_names() -> set:
        names = set()
        for key in toc.keys():
            if isinstance(key, tuple) and len(key) >= 2:
                names.add(str(key[1]))
            else:
                names.add(str(key))
        return names

    strings: set[str] = set()
    for name in list(toc.keys()):
        try:
            code = pyz.extract(name)
        except Exception:
            continue
        if code is not None and hasattr(code, "co_consts"):
            walk(code, strings, set())

    names = toc_names()
    print(f"PYZ 模块数: {len(toc)}；采集到字符串 {len(strings)} 条\n")

    def has_sub(s: str) -> bool:
        """按子串找，不能做集合精确匹配。

        f-string 在字节码里会被拆成多个字面片段（`f"连续 {N} 遍…"` 会变成
        `"连续 "` + 插值 + `" 遍…"`），精确匹配会漏报，造成假阴性。
        """
        return any(s in t for t in strings)

    bad = 0
    print("—— 模块必须在 ——")
    for m in WANT_MODULES:
        hit = m in names
        print(f"  [{'OK' if hit else 'XX'}] {m}")
        bad += 0 if hit else 1

    print("\n—— 字符串必须存在 ——")
    for s in WANT_STRINGS:
        hit = has_sub(s)
        print(f"  [{'OK' if hit else 'XX'}] {s!r}")
        bad += 0 if hit else 1

    print("\n—— 必须已移除 ——")
    for s in BAN_STRINGS:
        clean = not has_sub(s)
        print(f"  [{'OK' if clean else 'XX'}] {s!r}" + ("" if clean else "  ← 仍然存在！"))
        bad += 0 if clean else 1

    total = len(WANT_MODULES) + len(WANT_STRINGS) + len(BAN_STRINGS)
    print(f"\n{'=' * 52}")
    print(f"检查 {total} 项，问题 {bad} 项")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "乐课.exe"))

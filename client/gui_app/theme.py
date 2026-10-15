# -*- coding: utf-8 -*-
"""浅灰白课程工作台：中性内容面、蓝色操作与选中态、独立语义状态色。

配色由下方令牌统一管理，平台共享同一套强调色。
标题和正文使用系统中文字体，数据使用等宽字体；无需远程字体。
"""
from __future__ import annotations

# ---------------- 调色板 ----------------
# 底层（v1.6.7：中性冷灰 —— 去掉原来的紫调，避免与青绿主色冷暖打架）
BG = "#F5F6F8"            # 页面底
BG_DEEP = "#ECEFF4"       # 更深的凹槽 / 分隔
SIDEBAR = "#FFFFFF"
CARD = "#FFFFFF"
CARD_SUB = "#F8F9FB"      # 卡片内的次级块
CARD_SUB_2 = "#EEF1F6"    # 悬停 / 选中底色
BORDER = "#E5E8EF"        # 极淡描边（卡片）
BORDER_STRONG = "#D3D9E3"  # 控件描边（刻意极淡，靠明度差分层）
INDICATOR_LINE = "#8791A2"  # 勾选框描边：白底对比度 ≈3.1:1（BORDER_STRONG 只有 1.38:1）

# 文字
TEXT = "#202838"
TEXT_SUB = "#526078"
# v1.6.4：TEXT_WEAK 由 #8A8DA1 提亮 —— 它是「说明文字」的主力色，
# 原值在白底只有 3.3:1，整屏小字发灰看不清。
# v1.6.7：随中性色一起去紫调，改 #66738A（白底 5.0:1，达 WCAG AA），
# 与 TEXT_SUB 仍有明显层次。
TEXT_WEAK = "#66738A"
# 仅用于「边框 / 大号装饰图标」；正文与小字一律不用它（太淡）。
TEXT_FAINT = "#9AA5B7"

# 禁用态：浅底 + 中灰字。原来各平台禁用按钮是「浅品牌色 + 白字」，
# 对比度仅 1.4:1 —— 看着像「按钮没渲染出来」。禁用也要读得出按钮叫什么。
TEXT_DISABLED = "#7C879A"     # vs DISABLED_BG ≈3.1:1
DISABLED_BG = "#F0F2F6"
DISABLED_LINE = "#E1E5EC"

# 主色：克制的蓝色
PRIMARY = "#345FE5"
PRIMARY_HOVER = "#294DC2"
PRIMARY_PRESS = "#203C9D"
PRIMARY_SOFT = "#EEF2FF"
PRIMARY_SOFT_2 = "#E1E8FF"
PRIMARY_LINE = "#BACBFA"

# 状态色
OK = "#14704E"
OK_SOFT = "#E6F7EE"
OK_LINE = "#BEE7D1"
WARN = "#8A5510"
WARN_SOFT = "#FEF4E4"
WARN_LINE = "#F2DDB6"
DANGER = "#B0332B"
DANGER_SOFT = "#FDEDED"
DANGER_LINE = "#F3C9C9"

# ---- 平台品牌色（v1.6.8）----
# 三平台全部并回主色蓝色系：原来「学习通青绿 / U校园橙 / 雨课堂蓝 / 智慧树紫」
# 四个鲜艳色相并排像彩虹，一屏里四五种强调色互相抢注意力。
# 现统一为「一个主色 + 中性灰 + 少量语义色」，平台靠名称与图标区分。
# 变量名保留（外部 QSS / 页面 / 回归都按名字取色），只把值收敛。

# U校园（Unipus）
UNIPUS = PRIMARY            # 主色：导航选中 / 主按钮 / 卡片强调
UNIPUS_HOVER = PRIMARY_HOVER
UNIPUS_PRESS = PRIMARY_PRESS
UNIPUS_SOFT = PRIMARY_SOFT  # 浅底（卡片图标块）
UNIPUS_SOFT_2 = PRIMARY_SOFT_2
UNIPUS_LINE = PRIMARY_LINE  # 浅描边
UNIPUS_HERO_A = "#F7F9FF"   # Hero 渐变起
UNIPUS_HERO_B = "#EDF2FF"   # Hero 渐变止
UNIPUS_HERO_TEXT = "#283B69"    # Hero 标题深青绿
UNIPUS_HERO_SUB = "#52688F"     # Hero 副标题

# 雨课堂（学堂在线）
YUKETANG = PRIMARY
YUKETANG_HOVER = PRIMARY_HOVER
YUKETANG_PRESS = PRIMARY_PRESS
YUKETANG_SOFT = PRIMARY_SOFT
YUKETANG_SOFT_2 = PRIMARY_SOFT_2
YUKETANG_LINE = PRIMARY_LINE
YUKETANG_HERO_A = "#F7F9FF"
YUKETANG_HERO_B = "#EDF2FF"
YUKETANG_HERO_TEXT = "#283B69"
YUKETANG_HERO_SUB = "#52688F"

# 智慧树（知到）
ZHIHUISHU = PRIMARY             # 主色：导航选中 / 主按钮 / 卡片强调
ZHIHUISHU_HOVER = PRIMARY_HOVER
ZHIHUISHU_PRESS = PRIMARY_PRESS
ZHIHUISHU_SOFT = PRIMARY_SOFT   # 浅底
ZHIHUISHU_SOFT_2 = PRIMARY_SOFT_2
ZHIHUISHU_LINE = PRIMARY_LINE   # 浅描边
ZHIHUISHU_HERO_A = "#F7F9FF"    # Hero 渐变起
ZHIHUISHU_HERO_B = "#EDF2FF"    # Hero 渐变止
ZHIHUISHU_HERO_TEXT = "#283B69"  # Hero 标题深青绿
ZHIHUISHU_HERO_SUB = "#52688F"   # Hero 副标题

#: 导航选中时按平台换品牌色（键 = PAGES 里的图标名）
#: profile = 「用户中心」，属于账号域，用主色（不是课程平台品牌色）
NAV_BRAND = {"unipus": UNIPUS, "yuketang": YUKETANG, "zhihuishu": ZHIHUISHU,
             "profile": PRIMARY}

TRACK = "#E9EDF4"

# ---------------- 形状 ----------------
R_CARD = 12
R_CTRL = 8
R_PILL = 999

LOG_COLORS = {
    "TRACE": "#A8B0AF",
    "DEBUG": "#7C8583",
    "INFO": "#222B29",
    "SUCCESS": "#0F8C46",
    "WARNING": "#B26A08",
    "ERROR": "#CE3535",
    "CRITICAL": "#A81E1E",
}

FONT_STACK = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif'
MONO_STACK = '"Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace'


QSS = f"""
/* ==================== 全局 ==================== */
* {{
    font-family: {FONT_STACK};
    font-size: 13px;
    color: {TEXT};
    outline: none;
}}
QWidget#Root, QMainWindow, QStackedWidget#MainStack {{ background: {BG}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ==================== 侧边导航 ==================== */
QWidget#Sidebar {{
    background: {SIDEBAR};
    border-right: 1px solid {BORDER};
}}
/* 品牌标记：正常态是位图（品牌图标自带底色与圆角），
   只有位图渲染失败、回落到文字标记时才套下面的方块样式 */
QLabel#BrandMark {{ background: transparent; }}
QLabel#BrandMark[textMark="true"] {{
    background: {PRIMARY};
    color: #FFFFFF;
    border-radius: 11px;
    font-size: 19px;
    font-weight: bold;
}}
QLabel#BrandTitle {{ font-size: 15px; font-weight: bold; color: {TEXT}; }}

QListWidget#Nav {{
    background: transparent;
    border: none;
    padding: 2px 10px 6px 10px;
}}
QListWidget#Nav:focus {{ border: 1px solid {PRIMARY_LINE}; border-radius: 8px; }}
QListWidget#Nav::item {{
    height: 42px;
    padding-left: 11px;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 9px;
    color: {TEXT_SUB};
    margin: 3px 0;
}}
QListWidget#Nav::item:hover {{
    background: {CARD_SUB_2};
    color: {TEXT};
}}
QListWidget#Nav::item:selected {{
    background: {PRIMARY_SOFT};
    border-left: 3px solid {PRIMARY};
    color: {PRIMARY};
    font-weight: bold;
}}
/* 选中项整行跟着平台换色（v1.6.8 起四平台共用同一主色，规则按 brand 保留，
   以后要给某平台单独配色只改这几条即可）。
   brand 属性由 main_window 在切页时设置，改完要 unpolish+polish 让 ::item 重新匹配。 */
QListWidget#Nav[brand="unipus"]::item:selected {{
    background: {UNIPUS_SOFT};
    border-left: 3px solid {UNIPUS};
    color: {UNIPUS};
}}
QListWidget#Nav[brand="yuketang"]::item:selected {{
    background: {YUKETANG_SOFT};
    border-left: 3px solid {YUKETANG};
    color: {YUKETANG};
}}
QListWidget#Nav[brand="zhihuishu"]::item:selected {{
    background: {ZHIHUISHU_SOFT};
    border-left: 3px solid {ZHIHUISHU};
    color: {ZHIHUISHU};
}}

/* 分组标题：QListWidget 里的禁用项（Qt.NoItemFlags），只当标题用，不可点不可选。
   左沿 = 容器 padding 10px + item padding 11px，和普通项的图标列对齐。 */
/* ⚠ 这里刻意**不写 color**：分组标题的颜色由 main_window 用
   item.setForeground(平台色) 逐个设置，QSS 一旦指定 color 会把它盖掉。 */
QListWidget#Nav::item:disabled {{
    height: 26px;
    margin: 10px 0 2px 0;
    padding-left: 9px;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 0.6px;
    background: transparent;
}}
QListWidget#Nav::item:disabled:first {{ margin-top: 2px; }}

/* ==================== 顶栏 / 状态栏 ==================== */
QWidget#TopBar {{
    background: {CARD};
    border-bottom: 1px solid {BORDER};
}}
QLabel#PageTitle {{ font-size: 17px; font-weight: bold; }}

/* 平台标识：pill（保留样式，备用）+ 顶栏下方 3px 品牌色条。
   v1.6.8 起四平台共用一个主色，顶栏改用平台切换标签表达"当前在哪个平台"。 */
QLabel#PlatformChip {{
    border-radius: 9px;
    padding: 3px 11px;
    font-size: 11px;
    font-weight: bold;
}}
QLabel#PlatformChip[brand="chaoxing"] {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; }}
/* 品牌 pill 的文字统一用「深一档」的品牌色（PRESS）：浅品牌底 + 本色文字
   对比度偏低；深一档后才够读。 */
QLabel#PlatformChip[brand="unipus"] {{ background: {UNIPUS_SOFT}; color: {UNIPUS_PRESS}; }}
QLabel#PlatformChip[brand="yuketang"] {{ background: {YUKETANG_SOFT}; color: {YUKETANG_PRESS}; }}
QLabel#PlatformChip[brand="zhihuishu"] {{ background: {ZHIHUISHU_SOFT}; color: {ZHIHUISHU_PRESS}; }}
QLabel#PlatformChip[brand="settings"] {{ background: {CARD_SUB_2}; color: {TEXT_SUB}; }}
QLabel#PlatformChip[brand="account"] {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; }}
QFrame#TopAccent {{ border: none; }}
QFrame#TopAccent[brand="chaoxing"] {{ background: {PRIMARY}; }}
QFrame#TopAccent[brand="unipus"] {{ background: {UNIPUS}; }}
QFrame#TopAccent[brand="yuketang"] {{ background: {YUKETANG}; }}
QFrame#TopAccent[brand="zhihuishu"] {{ background: {ZHIHUISHU}; }}
QFrame#TopAccent[brand="settings"] {{ background: {BORDER_STRONG}; }}
QFrame#TopAccent[brand="account"] {{ background: {PRIMARY}; }}
QWidget#StatusBar {{
    background: {CARD};
    border-top: 1px solid {BORDER};
}}
QLabel#StatusText {{ color: {TEXT_SUB}; font-size: 12px; }}
QLabel#StatusActivity {{ color: {TEXT_WEAK}; font-size: 12px; }}
QLabel#VersionChip {{
    color: {TEXT_WEAK};
    background: {CARD_SUB_2};
    border-radius: 8px;
    padding: 1px 9px;
    font-size: 11px;
}}

/* ---- 顶部平台切换栏（v1.6.7：平台切换上移到顶栏，侧栏只留当前平台的子页）---- */
QPushButton#PlatformTab {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px 13px;
    color: {TEXT_SUB};
    font-size: 13px;
    font-weight: bold;
}}
QPushButton#PlatformTab:hover {{ background: {CARD_SUB_2}; color: {TEXT}; }}
QPushButton#PlatformTab:checked {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; }}
QPushButton#PlatformTab[brand="unipus"]:checked {{ background: {UNIPUS_SOFT}; color: {UNIPUS_PRESS}; }}
QPushButton#PlatformTab[brand="yuketang"]:checked {{ background: {YUKETANG_SOFT}; color: {YUKETANG_PRESS}; }}
QPushButton#PlatformTab[brand="zhihuishu"]:checked {{ background: {ZHIHUISHU_SOFT}; color: {ZHIHUISHU_PRESS}; }}
QPushButton#UtilTab {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px 12px;
    color: {TEXT_WEAK};
    font-size: 13px;
}}
QPushButton#UtilTab:hover {{ background: {CARD_SUB_2}; color: {TEXT}; }}
QPushButton#UtilTab:checked {{ background: {CARD_SUB_2}; color: {TEXT}; font-weight: bold; }}
QFrame#TopDivider {{ background: {BORDER}; max-width: 1px; border: none; }}
QPushButton#BrandBtn {{ background: transparent; border: none; padding: 0; }}

/* ==================== 卡片 ==================== */
QFrame#Card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: {R_CARD}px;
}}
QFrame#CardFlat {{
    background: {CARD_SUB};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel#CardTitle {{ font-size: 14px; font-weight: bold; }}
QLabel#CardHint {{ font-size: 12px; color: {TEXT_WEAK}; }}
QWidget#DiagnosticRow {{ background: {CARD_SUB}; border: 1px solid {BORDER}; border-radius: 8px; }}
QLabel#DiagnosticTitle {{ font-size: 13px; font-weight: bold; }}
QLabel#FieldLabel {{ color: {TEXT_SUB}; }}
QLabel#SectionLabel {{
    font-size: 11px;
    color: {TEXT_WEAK};
    font-weight: bold;
}}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; border: none; }}
QFrame#HLine {{ background: {BORDER}; max-height: 1px; border: none; }}

/* 用户中心：账号概况小块 + 设备行（都是"卡内次级块"，靠底色分层） */
QFrame#MiniStat {{
    background: {CARD_SUB};
    border: 1px solid {BORDER};
    border-radius: 9px;
}}
QLabel#MiniStatValue {{
    color: {TEXT};
    font-size: 15px;
    font-weight: 600;
}}
QFrame#DeviceRow {{
    background: {CARD_SUB};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

/* 卡片头：左侧色块图标 + 标题 + 说明 */
QLabel#CardIcon {{
    border-radius: 9px;
    font-size: 15px;
    font-weight: bold;
}}

/* ==================== 按钮 ==================== */
QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: {R_CTRL}px;
    padding: 7px 15px;
    min-height: 20px;
    color: {TEXT};
}}
QPushButton:hover {{ background: {CARD_SUB}; border-color: {TEXT_FAINT}; }}
QPushButton:focus {{ border-color: {PRIMARY}; }}
QPushButton:pressed {{ background: {BG_DEEP}; }}
QPushButton:disabled {{ color: {TEXT_DISABLED}; background: {DISABLED_BG}; border-color: {DISABLED_LINE}; }}

QPushButton[variant="primary"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #345FE5, stop:1 {PRIMARY});
    border: 1px solid {PRIMARY};
    color: #FFFFFF;
    font-weight: bold;
}}
QPushButton[variant="primary"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #294DC2, stop:1 {PRIMARY_HOVER});
    border-color: {PRIMARY_HOVER};
}}
QPushButton[variant="primary"]:pressed {{ background: {PRIMARY_PRESS}; border-color: {PRIMARY_PRESS}; }}
QPushButton[variant="primary"]:disabled {{ background: {DISABLED_BG}; border-color: {DISABLED_LINE}; color: {TEXT_DISABLED}; }}

QPushButton[variant="danger"] {{
    background: {CARD};
    border: 1px solid {DANGER_LINE};
    color: {DANGER};
    font-weight: bold;
}}
QPushButton[variant="danger"]:hover {{ background: {DANGER_SOFT}; border-color: {DANGER}; }}
QPushButton[variant="danger"]:disabled {{ color: {TEXT_DISABLED}; border-color: {DISABLED_LINE}; background: {DISABLED_BG}; }}

QPushButton[variant="soft"] {{
    background: {PRIMARY_SOFT};
    border: 1px solid transparent;
    color: {PRIMARY};
    font-weight: bold;
}}
QPushButton[variant="soft"]:hover {{ background: {PRIMARY_SOFT_2}; }}

QPushButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {TEXT_SUB};
    padding: 5px 10px;
}}
QPushButton[variant="ghost"]:hover {{ background: {CARD_SUB_2}; color: {PRIMARY}; }}

/* ---- U校园（v1.5.2b）：主按钮 / Hero 品牌条 / 任务面板。
   v1.6.8 起品牌色并回主色蓝色，与 primary 同一套渐变（保留变体名，别的地方按名引用）---- */
QPushButton[variant="unipus"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #345FE5, stop:1 {UNIPUS});
    border: 1px solid {UNIPUS};
    color: #FFFFFF;
    font-weight: bold;
}}
QPushButton[variant="unipus"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #294DC2, stop:1 {UNIPUS_HOVER});
    border-color: {UNIPUS_HOVER};
}}
QPushButton[variant="unipus"]:pressed {{ background: {UNIPUS_PRESS}; border-color: {UNIPUS_PRESS}; }}
QPushButton[variant="unipus"]:disabled {{ background: {DISABLED_BG}; border-color: {DISABLED_LINE}; color: {TEXT_DISABLED}; }}

QFrame#UnipusHero {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 {UNIPUS_HERO_A}, stop:1 {UNIPUS_HERO_B});
    border: 1px solid {UNIPUS_LINE};
    border-radius: 14px;
}}
QFrame#UnipusHeroMark {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #345FE5, stop:1 {PRIMARY});
    border: none;
    border-radius: 11px;
}}
QLabel#UnipusHeroTitle {{ font-size: 17px; font-weight: bold; color: {UNIPUS_HERO_TEXT}; }}

/* ---- 雨课堂（v1.5.4）：主按钮 / Hero 品牌条（v1.6.8 并回主色蓝色）---- */
QPushButton[variant="yuketang"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #345FE5, stop:1 {YUKETANG});
    border: 1px solid {YUKETANG};
    color: #FFFFFF;
    font-weight: bold;
}}
QPushButton[variant="yuketang"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #294DC2, stop:1 {YUKETANG_HOVER});
    border-color: {YUKETANG_HOVER};
}}
QPushButton[variant="yuketang"]:pressed {{
    background: {YUKETANG_PRESS};
    border-color: {YUKETANG_PRESS};
}}
QPushButton[variant="yuketang"]:disabled {{
    background: {DISABLED_BG};
    border-color: {DISABLED_LINE};
    color: {TEXT_DISABLED};
}}

QFrame#YuketangHero {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 {YUKETANG_HERO_A}, stop:1 {YUKETANG_HERO_B});
    border: 1px solid {YUKETANG_LINE};
    border-radius: 14px;
}}
QFrame#YuketangHeroMark {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #345FE5, stop:1 {YUKETANG});
    border: none;
    border-radius: 11px;
}}
QLabel#YuketangHeroTitle {{ font-size: 17px; font-weight: bold; color: {YUKETANG_HERO_TEXT}; }}
QFrame#YuketangQRBox {{
    background: {CARD};
    border: 1px solid {YUKETANG_LINE};
    border-radius: 12px;
}}

/* ---- 智慧树：主按钮 / Hero 品牌条 / 输入面板（v1.6.8 并回主色蓝色）---- */
QPushButton[variant="zhihuishu"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #345FE5, stop:1 {ZHIHUISHU});
    border: 1px solid {ZHIHUISHU};
    color: #FFFFFF;
    font-weight: bold;
}}
QPushButton[variant="zhihuishu"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #294DC2, stop:1 {ZHIHUISHU_HOVER});
    border-color: {ZHIHUISHU_HOVER};
}}
QPushButton[variant="zhihuishu"]:pressed {{
    background: {ZHIHUISHU_PRESS};
    border-color: {ZHIHUISHU_PRESS};
}}
QPushButton[variant="zhihuishu"]:disabled {{
    background: {DISABLED_BG};
    border-color: {DISABLED_LINE};
    color: {TEXT_DISABLED};
}}
QFrame#ZhihuishuHero {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 {ZHIHUISHU_HERO_A}, stop:1 {ZHIHUISHU_HERO_B});
    border: 1px solid {ZHIHUISHU_LINE};
    border-radius: 14px;
}}
QFrame#ZhihuishuHeroMark {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #345FE5, stop:1 {ZHIHUISHU});
    border: none;
    border-radius: 11px;
}}
QLabel#ZhihuishuHeroTitle {{ font-size: 17px; font-weight: bold; color: {ZHIHUISHU_HERO_TEXT}; }}
QFrame#ZhihuishuBox {{
    background: {CARD};
    border: 1px solid {ZHIHUISHU_LINE};
    border-radius: 12px;
}}
QFrame#TaskPanel {{
    background: {CARD_SUB};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

/* ==================== 输入控件 ==================== */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: {R_CTRL}px;
    padding: 7px 11px;
    selection-background-color: {PRIMARY};
    selection-color: #FFFFFF;
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {TEXT_FAINT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {PRIMARY};
    background: {CARD};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {DISABLED_BG};
    color: {TEXT_DISABLED};
    border-color: {DISABLED_LINE};
}}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: url("__ARROW_ICON__");
    width: 10px;
    height: 6px;
    margin-right: 9px;
}}
QComboBox::down-arrow:disabled {{
    image: url("__ARROW_DISABLED__");
}}
QComboBox QAbstractItemView {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 5px;
    selection-background-color: {PRIMARY_SOFT};
    selection-color: {PRIMARY};
    outline: none;
}}
QComboBox QAbstractItemView::item {{ padding: 6px 10px; border-radius: 6px; min-height: 20px; }}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

/* ==================== 勾选 ==================== */
QCheckBox, QRadioButton {{ spacing: 8px; color: {TEXT}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {TEXT_DISABLED}; }}
/* 勾选框描边单独用 INDICATOR_LINE：BORDER_STRONG(#D3D9E3) 在白底上
   对比度只有 1.38:1，未勾选的方框几乎看不见（无障可访问性硬伤）。
   这里取 #8791A2 ≈ 3.1:1，只改勾选框，不动卡片/输入框那套"极淡描边"的层次。 */
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {INDICATOR_LINE};
    background: {CARD};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {PRIMARY}; }}
QCheckBox:focus, QRadioButton:focus {{ color: {PRIMARY}; }}
QCheckBox:focus::indicator, QRadioButton:focus::indicator {{ border-color: {PRIMARY}; }}
QCheckBox::indicator:checked {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    image: url("__CHECK_ICON__");
}}
QCheckBox::indicator:checked:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QRadioButton::indicator:checked {{
    background: {CARD};
    border: 5px solid {PRIMARY};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {CARD_SUB}; border-color: {BORDER};
}}

/* ==================== 表格 ==================== */
QTableWidget {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: {R_CARD}px;
    gridline-color: transparent;
    selection-background-color: {PRIMARY_SOFT};
    selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 8px; border-bottom: 1px solid {BORDER}; }}
QTableWidget::item:selected {{ background: {PRIMARY_SOFT}; color: {TEXT}; }}
QHeaderView::section {{
    background: {CARD_SUB};
    color: {TEXT_WEAK};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 10px 8px;
    font-weight: bold;
    font-size: 12px;
}}
QHeaderView::section:first {{ border-top-left-radius: {R_CARD}px; }}
QHeaderView::section:last {{ border-top-right-radius: {R_CARD}px; }}
QTableCornerButton::section {{ background: {CARD_SUB}; border: none; }}

/* ==================== 进度条 ==================== */
/* 全项目不再使用 QProgressBar：它的 chunk 由样式引擎按整数像素绘制，刷课场景
   下一个 32 分钟的视频每秒只前进 0.4px，看起来就是「卡住 → 跳一格」。
   进度条一律用 widgets.SmoothBar（QPainter 浮点几何 + 抗锯齿自绘），
   表格里的进度列由 widgets.ProgressDelegate 自绘。这里不再需要 QSS。 */

/* ==================== 平台账号条（U校园 / 雨课堂） ==================== */
QFrame#AccountBar {{
    background: {CARD_SUB};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QPushButton#AccChip {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 15px;
    padding: 4px 14px;
    color: {TEXT_SUB};
    font-size: 12px;
    text-align: center;
}}
QPushButton#AccChip:hover {{ border-color: {PRIMARY}; color: {TEXT}; }}
QPushButton#AccChip:checked {{
    background: {PRIMARY_SOFT};
    border-color: {PRIMARY};
    color: {PRIMARY};
    font-weight: bold;
}}
/* 正在刷课的账号：chip 上带实心圆点，颜色走绿色，和"选中态蓝"区分开 */
QPushButton#AccChip[busy="true"] {{ color: {OK}; }}
QPushButton#AccChip[busy="true"]:checked {{
    background: {OK_SOFT};
    border-color: {OK};
    color: {OK};
}}
/* 平台配色刻意做在 **chip 自己** 的属性上，而不是祖先选择器
   （QFrame#AccountBar[brand=…] QPushButton#AccChip）：
   祖先属性变了 Qt 不会重算子控件的匹配，得手工连子控件一起 repolish，
   是个反复踩的坑（见项目硬规则 13）。放 chip 上就没这问题。 */
QPushButton#AccChip[brand="unipus"]:checked {{
    background: {UNIPUS_SOFT};
    border-color: {UNIPUS};
    color: {UNIPUS};
}}
QPushButton#AccChip[brand="yuketang"]:checked {{
    background: {YUKETANG_SOFT};
    border-color: {YUKETANG};
    color: {YUKETANG};
}}

/* ==================== 运行页 ==================== */
QLabel#PassTag {{
    background: {PRIMARY_SOFT};
    color: {PRIMARY};
    border-radius: 9px;
    padding: 3px 11px;
    font-size: 12px;
    font-weight: bold;
}}
QFrame#NowBox {{
    background: {CARD_SUB};
    border: 1px solid {BORDER};
    border-left: 3px solid {BORDER_STRONG};
    border-radius: 10px;
}}
QFrame#NowBox[state="run"] {{
    background: {PRIMARY_SOFT};
    border-color: {PRIMARY_LINE};
    border-left-color: {PRIMARY};
}}
QLabel#NowLabel {{ font-size: 11px; color: {TEXT_WEAK}; }}
QLabel#NowCourse {{ font-size: 12px; color: {TEXT_SUB}; }}
QLabel#NowChapter {{ font-size: 14px; font-weight: bold; }}
QLabel#NowJob {{ font-size: 14px; font-weight: bold; color: {PRIMARY}; }}
QLabel#NowMeta {{ font-size: 12px; color: {TEXT_WEAK}; }}
QLabel#NowEmpty {{ font-size: 12px; color: {TEXT_WEAK}; padding: 3px 0; }}
QLabel#JobChip {{
    background: {CARD_SUB_2};
    color: {TEXT_SUB};
    border-radius: 8px;
    padding: 2px 9px;
    font-size: 11px;
}}
QFrame#NowBox[state="run"] QLabel#JobChip {{ background: {PRIMARY_SOFT_2}; color: {PRIMARY}; }}

/* 并行任务点槽位（v1.5.1f）：每个正在处理的任务点一行，多章节并行各占一行 */
QFrame#SlotRow {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#SlotRow[state="run"] {{
    border-color: {PRIMARY_LINE};
}}
QFrame#SlotRow[state="stale"] {{
    border-color: {BORDER};
}}
QFrame#SlotRow[state="stale"] QLabel#SlotTitle,
QFrame#SlotRow[state="stale"] QLabel#SlotMeta,
QFrame#SlotRow[state="stale"] QLabel#JobChip {{
    color: {TEXT_DISABLED};
    background: {CARD_SUB};
}}
QLabel#SlotTitle {{ font-size: 12px; font-weight: bold; }}
QLabel#SlotMeta {{ font-size: 11px; color: {TEXT_WEAK}; }}

/* ==================== 课程页：章节按钮与选择对话框 ==================== */
QPushButton#ChapterBtn {{
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 8px;
}}
QLabel#ChapterState {{ font-size: 11px; color: {TEXT_WEAK}; }}
QLabel#ChapterState[state="done"] {{ color: {OK}; background: {OK_SOFT};
    border-radius: 8px; padding: 2px 8px; font-weight: bold; }}
QLabel#DialogTitle {{ font-size: 15px; font-weight: bold; }}

/* ==================== 账号列表（账号页） ==================== */
QListWidget#AccList {{
    background: transparent;
    border: none;
    outline: none;
    padding: 0;
}}
QListWidget#AccList::item {{
    background: {CARD_SUB};
    border: 1px solid transparent;
    border-radius: 10px;
    margin: 2px 0;
}}
QListWidget#AccList::item:hover {{ background: {CARD_SUB_2}; }}
QListWidget#AccList::item:selected {{
    background: {PRIMARY_SOFT};
    border-color: {PRIMARY_LINE};
}}
QListWidget#AccList:focus::item:selected {{ border-color: {PRIMARY}; }}
QLabel#AccName {{ font-size: 13px; font-weight: bold; }}
QLabel#AccSub {{ font-size: 11px; color: {TEXT_WEAK}; }}

/* ==================== 账号进度卡（运行页） ==================== */
QFrame#AccCard {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#AccCard[state="running"] {{ border-color: {PRIMARY_LINE}; background: #F8F9FF; }}
QFrame#AccCard[state="ok"] {{ border-color: {OK_LINE}; }}
QFrame#AccCard[state="stopped"] {{ border-color: {WARN_LINE}; }}
QFrame#AccCard[state="error"] {{ border-color: {DANGER_LINE}; }}
QLabel#AccCardName {{ font-size: 14px; font-weight: bold; }}
QLabel#AccTime {{ font-family: {MONO_STACK}; font-size: 12px; color: {TEXT_WEAK}; }}
QLabel#AccDot {{ background: {BORDER_STRONG}; border-radius: 5px; }}
QFrame#AccCard[state="running"] QLabel#AccDot {{ background: {PRIMARY}; }}
QFrame#AccCard[state="ok"] QLabel#AccDot {{ background: {OK}; }}
QFrame#AccCard[state="stopped"] QLabel#AccDot {{ background: {WARN}; }}
QFrame#AccCard[state="error"] QLabel#AccDot {{ background: {DANGER}; }}

/* ==================== 日志区 ==================== */
QPlainTextEdit#LogView {{
    background: #FBFCFC;
    border: 1px solid {BORDER};
    border-radius: 10px;
    font-family: {MONO_STACK};
    font-size: 12px;
    padding: 10px;
}}

/* ==================== 滚动条 ==================== */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 3px 2px; }}
QScrollBar::handle:vertical {{ background: #C7D0CF; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: #ACB7B6; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px 3px; }}
QScrollBar::handle:horizontal {{ background: #C7D0CF; border-radius: 4px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: #ACB7B6; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ==================== 徽标 ==================== */
QLabel#BadgeOk, QLabel#BadgeWarn, QLabel#BadgeError, QLabel#BadgeIdle, QLabel#BadgeInfo {{
    border-radius: 9px;
    padding: 3px 11px;
    font-size: 12px;
}}
QLabel#BadgeOk {{ background: {OK_SOFT}; color: {OK}; }}
QLabel#BadgeWarn {{ background: {WARN_SOFT}; color: {WARN}; }}
QLabel#BadgeError {{ background: {DANGER_SOFT}; color: {DANGER}; }}
QLabel#BadgeIdle {{ background: {CARD_SUB_2}; color: {TEXT_SUB}; }}
QLabel#BadgeInfo {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; }}

/* ==================== 指标卡 ==================== */
QFrame#MetricCard {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: {R_CARD}px;
}}
QLabel#BigNum {{ font-size: 21px; font-weight: bold; }}
QLabel#MetricLabel {{ font-size: 12px; color: {TEXT_WEAK}; }}
QLabel#MetricIcon {{
    border-radius: 8px;
    font-size: 13px;
    font-weight: bold;
}}

/* ==================== 空态 ==================== */
QLabel#EmptyIcon {{
    color: {TEXT_FAINT};
    font-size: 30px;
    font-weight: bold;
}}
QLabel#EmptyTitle {{ font-size: 14px; font-weight: bold; color: {TEXT_SUB}; }}
QLabel#EmptyHint {{ font-size: 12px; color: {TEXT_WEAK}; }}

/* ==================== 菜单 ==================== */
QMenu {{
    background: {CARD};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; color: {TEXT}; }}
QMenu::item:selected {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

/* ==================== 对话框 ==================== */
QMessageBox {{ background: {CARD}; }}
QMessageBox QLabel {{ font-size: 13px; }}
QMessageBox QPushButton {{ min-width: 84px; }}

/* ==================== AI 配置面板 ==================== */
QLineEdit#SearchBox {{
    border: 1px solid {BORDER};
    border-radius: {R_CTRL}px;
    padding: 7px 11px;
    background: {CARD_SUB};
    color: {TEXT};
}}
QLineEdit#SearchBox:hover {{ border-color: {BORDER_STRONG}; }}
QLineEdit#SearchBox:focus {{
    border-color: {PRIMARY};
    background: {CARD};
}}

QFrame#ApiHero {{
    background: {CARD_SUB};
    border: 1px solid {BORDER};
    border-left: 3px solid {BORDER_STRONG};
    border-radius: 10px;
}}
QFrame#ApiHero[state="on"] {{
    background: {OK_SOFT};
    border-color: {OK_LINE};
    border-left-color: {OK};
}}
QFrame#ApiHero[state="warn"] {{
    background: {WARN_SOFT};
    border-color: {WARN_LINE};
    border-left-color: {WARN};
}}
QLabel#HeroModel {{ font-size: 15px; font-weight: bold; }}
QLabel#HeroMeta {{ font-size: 12px; color: {TEXT_WEAK}; }}
QLabel#HeroStatus {{
    padding: 4px 12px;
    border-radius: 11px;
    background: {CARD_SUB_2};
    color: {TEXT_SUB};
    font-size: 12px;
}}
QFrame#ApiHero[state="on"] QLabel#HeroStatus {{ background: #D2F0E1; color: {OK}; }}
QFrame#ApiHero[state="warn"] QLabel#HeroStatus {{ background: #F7E7CB; color: {WARN}; }}

QLabel#StepTitle {{ font-size: 13px; font-weight: bold; }}
QLabel#StepHint {{ font-size: 12px; color: {TEXT_WEAK}; }}
QLabel#StepTail {{ font-size: 12px; color: {TEXT_WEAK}; }}

QFrame#ProviderTile {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#ProviderTile:hover {{ border-color: {BORDER_STRONG}; background: {CARD_SUB}; }}
QFrame#ProviderTile[sel="true"] {{
    border-color: {PRIMARY};
    background: {PRIMARY_SOFT};
}}
QLabel#TileName {{ font-weight: bold; }}
QLabel#TileSub {{ font-size: 12px; color: {TEXT_WEAK}; }}
QLabel#TileCheck {{ color: {PRIMARY}; font-weight: bold; }}
QLabel#TileRibbon {{
    background: {OK_SOFT};
    color: {OK};
    border-radius: 8px;
    padding: 1px 7px;
    font-size: 11px;
}}

QFrame#ResultBar {{
    border-radius: 8px;
    background: {CARD_SUB};
    border: 1px solid {BORDER};
}}
QFrame#ResultBar[kind="ok"] {{ background: {OK_SOFT}; border-color: {OK_LINE}; }}
QFrame#ResultBar[kind="bad"] {{ background: {DANGER_SOFT}; border-color: {DANGER_LINE}; }}
QFrame#ResultBar[kind="wait"] {{ background: {PRIMARY_SOFT}; border-color: {PRIMARY_LINE}; }}
QLabel#ResultIcon {{ font-weight: bold; color: {TEXT_SUB}; }}
QFrame#ResultBar[kind="ok"] QLabel#ResultIcon {{ color: {OK}; }}
QFrame#ResultBar[kind="bad"] QLabel#ResultIcon {{ color: {DANGER}; }}
QFrame#ResultBar[kind="wait"] QLabel#ResultIcon {{ color: {PRIMARY}; }}
QLabel#ResultText {{ font-size: 12px; }}

QFrame#ModelBox {{
    background: {CARD_SUB};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QPushButton#ModelChip {{
    text-align: left;
    padding: 5px 11px;
    border: 1px solid {BORDER};
    border-radius: 7px;
    background: {CARD};
    color: {TEXT_SUB};
    font-size: 12px;
}}
QPushButton#ModelChip:hover {{ border-color: {BORDER_STRONG}; color: {TEXT}; }}
QPushButton#ModelChip:checked {{
    border-color: {PRIMARY};
    background: {PRIMARY_SOFT};
    color: {PRIMARY};
    font-weight: bold;
}}

QPushButton#PrimaryButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #345FE5, stop:1 {PRIMARY});
    color: #FFFFFF;
    border: 1px solid {PRIMARY};
    border-radius: {R_CTRL}px;
    padding: 8px 18px;
    font-weight: bold;
}}
QPushButton#PrimaryButton:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QPushButton#PrimaryButton:pressed {{ background: {PRIMARY_PRESS}; }}
QPushButton#PrimaryButton:disabled {{ background: {DISABLED_BG}; border-color: {DISABLED_LINE}; color: {TEXT_DISABLED}; }}
QPushButton#GhostButton {{
    background: {CARD};
    color: {TEXT_SUB};
    border: 1px solid {BORDER_STRONG};
    border-radius: {R_CTRL}px;
    padding: 8px 16px;
}}
QPushButton#GhostButton:hover {{ border-color: {PRIMARY}; color: {PRIMARY}; background: {PRIMARY_SOFT}; }}
QPushButton#GhostButton:checked {{ border-color: {PRIMARY}; color: {PRIMARY}; background: {PRIMARY_SOFT}; }}
QPushButton#LinkButton {{
    background: transparent;
    border: none;
    color: {PRIMARY};
    padding: 0;
    text-align: left;
    font-size: 12px;
}}
QPushButton#LinkButton:hover {{ color: {PRIMARY_HOVER}; }}

QLabel#FootTip, QLabel#FieldHelp {{ font-size: 12px; color: {TEXT_WEAK}; }}
QFrame#AdvBox {{ background: {CARD_SUB}; border: 1px solid {BORDER}; border-radius: 9px; }}
QFrame#SavedRow {{ background: {CARD_SUB}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#SavedModel {{ font-weight: bold; }}
QLabel#SavedMeta {{ font-size: 12px; color: {TEXT_WEAK}; }}

/* ==================== 提示气泡 ==================== */
QToolTip {{
    background: #1F2A28;
    color: #FFFFFF;
    border: none;
    padding: 6px 9px;
    border-radius: 6px;
    font-size: 12px;
}}
"""

CHECK_ICON_PLACEHOLDER = "__CHECK_ICON__"
ARROW_ICON_PLACEHOLDER = "__ARROW_ICON__"
ARROW_DISABLED_PLACEHOLDER = "__ARROW_DISABLED__"


def _make_arrow_icon(path: str, color: str, w: int = 20, h: int = 12) -> bool:
    """画一个向下的 V 形下拉箭头 PNG。

    QSS 里一旦自定义了 ::drop-down，Qt 就不再画默认的 ::down-arrow，
    必须显式给 image 一个图片文件（不支持 data URI），否则下拉框看起来像填空框。
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

        scale = 4  # 4 倍超采样再缩小，边缘更干净
        W, H = w * scale, h * scale
        pm = QPixmap(W, H)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(color))
        pen.setWidthF(H * 0.17)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        path_ = QPainterPath()
        path_.moveTo(W * 0.24, H * 0.36)
        path_.lineTo(W * 0.50, H * 0.64)
        path_.lineTo(W * 0.76, H * 0.36)
        p.drawPath(path_)
        p.end()
        return bool(pm.scaled(w, h, Qt.IgnoreAspectRatio,
                              Qt.SmoothTransformation).save(path, "PNG"))
    except Exception:
        return False


def _make_check_icon(path: str, size: int = 16) -> bool:
    """画一个白色对勾 PNG。

    QSS 的 image 属性只认图片文件，不支持 data URI，所以必须落地一个文件；
    用矢量绘制而不是内嵌 base64，是为了让不同 DPI 下都能重生成清晰版本。
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

        scale = 4  # 先按 4 倍画再缩小，边缘更干净
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor("#FFFFFF"))
        pen.setWidthF(size * scale * 0.15)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        s = size * scale
        path_ = QPainterPath()
        path_.moveTo(s * 0.26, s * 0.53)
        path_.lineTo(s * 0.43, s * 0.70)
        path_.lineTo(s * 0.76, s * 0.31)
        p.drawPath(path_)
        p.end()
        return bool(pm.scaled(size, size, Qt.IgnoreAspectRatio,
                              Qt.SmoothTransformation).save(path, "PNG"))
    except Exception:
        return False


def build_qss(asset_dir: str) -> str:
    """生成（或复用）图标，并返回可用的 QSS。生成失败则退回纯色方块 / 无箭头。"""
    import os

    qss = QSS
    try:
        os.makedirs(asset_dir, exist_ok=True)
        # --- 勾选图标 ---
        icon = os.path.join(asset_dir, "check.png")
        if not os.path.exists(icon):
            if not _make_check_icon(icon):
                raise RuntimeError("check icon generation failed")
        qss = qss.replace(CHECK_ICON_PLACEHOLDER, icon.replace("\\", "/"))
        # --- 下拉箭头 ---
        arrow = os.path.join(asset_dir, "arrow.png")
        if not os.path.exists(arrow):
            _make_arrow_icon(arrow, TEXT_SUB)
        arrow_off = os.path.join(asset_dir, "arrow_disabled.png")
        if not os.path.exists(arrow_off):
            _make_arrow_icon(arrow_off, TEXT_FAINT)
        if os.path.exists(arrow):
            qss = qss.replace(ARROW_ICON_PLACEHOLDER, arrow.replace("\\", "/"))
        else:
            qss = qss.replace(f'    image: url("{ARROW_ICON_PLACEHOLDER}");\n', "")
        if os.path.exists(arrow_off):
            qss = qss.replace(ARROW_DISABLED_PLACEHOLDER, arrow_off.replace("\\", "/"))
        else:
            qss = qss.replace(f'    image: url("{ARROW_DISABLED_PLACEHOLDER}");\n', "")
        # 没生成出来就把整条 down-arrow 规则删掉
        if ARROW_ICON_PLACEHOLDER in qss:
            qss = qss.replace(f'    image: url("{ARROW_ICON_PLACEHOLDER}");\n', "")
    except Exception:
        qss = qss.replace(f'    image: url("{CHECK_ICON_PLACEHOLDER}");\n', "")
        qss = qss.replace(f'    image: url("{ARROW_ICON_PLACEHOLDER}");\n', "")
        qss = qss.replace(f'    image: url("{ARROW_DISABLED_PLACEHOLDER}");\n', "")
    return qss

# Light course desk: neutral surfaces; blue is reserved for actions and selection.
QSS += f"""
QWidget#Sidebar {{ background: #FFFFFF; border-right: 1px solid {BORDER}; }}
QLabel#RailBrand {{ color: {TEXT}; font-size: 23px; font-weight: bold; padding-left: 10px; }}
QLabel#RailCaption {{ color: {TEXT_WEAK}; font-size: 12px; padding-left: 10px; }}
QPushButton#RailTab {{ background: transparent; color: {TEXT_SUB}; text-align: left;
    padding: 9px 14px; border: 1px solid transparent; border-radius: 7px; font-size: 14px; }}
QPushButton#RailTab:hover {{ background: {CARD_SUB}; color: {TEXT}; }}
QPushButton#RailTab:checked {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; font-weight: bold; }}
QPushButton#RailTab:focus {{ border: 1px solid {PRIMARY_LINE}; }}
QListWidget#Nav {{ padding: 0; }}
QListWidget#Nav::item {{ color: {TEXT_SUB}; padding-left: 10px; margin: 2px 0; height: 34px; }}
QListWidget#Nav::item:hover {{ background: {CARD_SUB}; color: {TEXT}; }}
QListWidget#Nav::item:selected {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; border-left: 3px solid {PRIMARY}; }}
QWidget#TopBar {{ background: #FFFFFF; border: none; }}
QFrame#TopAccent, QFrame#TopAccent[brand] {{ background: {BORDER}; }}
QLabel#PageTitle {{ font-size: 19px; font-weight: bold; color: {TEXT}; }}
QFrame#UnipusHero, QFrame#YuketangHero, QFrame#ZhihuishuHero {{ background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 9px; }}
QLabel#UnipusHeroTitle, QLabel#YuketangHeroTitle, QLabel#ZhihuishuHeroTitle {{ color: {TEXT}; font-size: 17px; }}
QLabel#WorkspaceCaption {{ color: {TEXT_SUB}; font-size: 12px; }}
QFrame#BrowserCanvas {{ background: #FFFFFF; border: 1px solid {BORDER_STRONG}; border-radius: 7px; }}
QLabel#BrowserEmpty {{ color: {TEXT_SUB}; font-size: 14px; background: #FFFFFF; }}
QTabWidget#WorkspaceInspector::pane {{ border: none; background: transparent; }}
QTabBar::tab {{ padding: 10px 14px; background: transparent; color: {TEXT_WEAK}; border: none; }}
QTabBar::tab:selected {{ background: #FFFFFF; color: {PRIMARY}; font-weight: bold; border-bottom: 2px solid {PRIMARY}; }}
QTabBar::tab:focus {{ border: 1px solid {PRIMARY_LINE}; }}
QSplitter::handle {{ background: transparent; width: 12px; }}
"""
for _variant in ("primary", "unipus", "yuketang", "zhihuishu"):
    QSS += f"""
QPushButton[variant="{_variant}"] {{ background: {PRIMARY}; border-color: {PRIMARY}; color: #FFFFFF; }}
QPushButton[variant="{_variant}"]:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QPushButton[variant="{_variant}"]:pressed {{ background: {PRIMARY_PRESS}; border-color: {PRIMARY_PRESS}; }}
QPushButton[variant="{_variant}"]:disabled {{ background: {DISABLED_BG}; border-color: {DISABLED_LINE}; color: {TEXT_DISABLED}; }}
"""

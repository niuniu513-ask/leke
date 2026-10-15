# 雨课堂（yuketang / 学堂在线）引擎 —— vendor 说明

本目录是乐课（原学习通助手）桌面客户端接入的第三个平台引擎。
引擎代码 vendor 自上游开源脚本，只做「脚本 → 库」的重构，不改变刷课协议本身。

## 一、来源与署名

**刷课引擎（client.py）**

- 主来源：MuWinds / yuketangHelperBUU
  https://github.com/MuWinds/yuketangHelperBUU
  上游自述 refactor from Cat1007/yuketangHelperSCUTLite
  https://github.com/Cat1007/yuketangHelperSCUTLite
- 思路源头：heyblackC / yuketangHelper（最早实现「伪造心跳包」刷课）
  https://github.com/heyblackC/yuketangHelper
- 上游文件头部的作者署名一律保留：
  videoHelper.py 头部为 "version 4 / developed by zk chen / developed by MuWinds"，
  get_info.py、get_websockets.py 头部为 "version 1 / developed by MuWinds"。

上游实际引用到的文件与用途：

| 上游文件 | 用途 | 移植去向 |
| --- | --- | --- |
| helper_main.py | 主流程、各接口调用顺序与参数 | YuketangClient 的各方法 |
| videoHelper.py | 刷视频核心（心跳包伪造、进度轮询、阻塞退避） | list_videos / get_video_progress / _build_heartbeat / watch_video |
| get_info.py | 取 university_id、扫码登录、verify-origin-system-bind、cookie 提取 | get_university_id / login_by_qrcode / _verify_login |
| get_websockets.py | wss://{domain}/wsapp/ 扫码登录与二维码渲染 | login_by_qrcode（二维码改为回调 PNG 字节） |
| homeworkHelper.py | 作业 / 章节测验答题（leaf_type==6 筛题、get_exercise_list 拉题、problem_apply 提交） | list_exercises / get_exercise_problems / submit_answer / solve_exercise（v1.6.6 起） |

**答题能力的额外参考**（2026-10 调研，见下表；用于交叉验证接口与答案格式）：
同样只做「脚本 → 库」的重构，接口路径与请求体格式与上游一致。

| 参考项目 | 借鉴内容 |
| --- | --- |
| heyblackC/yuketangHelper（homeworkHelper.py） | 作业链路三步：chapter(leaf_type==6) → leaf_info.leaf_type_id → get_exercise_list → problem_apply；填空用 `answers` 字段 |
| aglorice/Raincourse（api/api.py） | `get_exercise_list(endpoint)`、`problem_apply` 的 body 结构 |
| Zachary709/YuKeTang（src/core/exercise_solver.py） | 题型归一（SingleChoice/MultipleChoice/FillBlank/TrueFalse）、答案 → 选项 key 的映射、提交前三类 payload |

**未移植**：homework_Helpers.py、decrypt_problem.py、
create_mapping_file.py、mapping_file.json、openai_ask.py。
其中「题目字体解密」（decrypt_problem / font_decode）**未实现** —— 部分学校开启
字体混淆（接口 ``data.font`` 非空）时题干会被替换成私用区字符，此时题库 / AI
拿到的文本是乱码，只能依赖「接口自带答案」；已上线学校若没开混淆则不受影响。

**协议**：上游仓库（yuketangHelperBUU / yuketangHelper / yuketangHelperSCUTLite）
均**没有 LICENSE 文件**。本项目按「个人学习用途、非商业、注明出处」处理：
保留全部作者署名与仓库链接，不对外分发、不用于任何商业用途。
如上游日后补充许可证，以许可证为准。

## 二、二维码：vendor 的 python-qrcode

venv 里没有 qrcode 包，且实测 PyPI / 清华镜像都连不上，pip 装不了；
websocket-client 与 Pillow 已装好，所以把纯 Python 的 qrcode 源码 vendor 进来。

- 来源：lincolnloop / python-qrcode ，默认分支 main，
  提交 f92c007498d796e4825638b7d50c14ea51e5457a
  https://github.com/lincolnloop/python-qrcode
- 获取方式：raw.githubusercontent.com 不通，改用 GitHub contents API
  （https://api.github.com/repos/lincolnloop/python-qrcode/contents/<path>）逐文件下载 base64。
- 协议：BSD-3-Clause（LICENSE 原样保留在同目录），
  其中另含 pyqrnative / Kazuhiko Arase 的 MIT 溯源声明。

保留的文件：

    _vendor_qrcode/__init__.py
    _vendor_qrcode/constants.py
    _vendor_qrcode/exceptions.py
    _vendor_qrcode/LUT.py
    _vendor_qrcode/base.py
    _vendor_qrcode/util.py
    _vendor_qrcode/main.py
    _vendor_qrcode/image/__init__.py
    _vendor_qrcode/image/base.py
    _vendor_qrcode/image/pil.py
    _vendor_qrcode/image/styles/__init__.py
    _vendor_qrcode/image/styles/moduledrawers/__init__.py
    _vendor_qrcode/image/styles/moduledrawers/base.py
    _vendor_qrcode/LICENSE

相对上游的裁剪与改动（只保留 PIL 渲染路径）：

1. 删除 PyPNG 渲染：image/pure.py、compat/png.py。
2. 删除 SVG 渲染：image/svg.py、compat/etree.py。
3. 删除 styledpil.py、image/styles/colormasks.py、moduledrawers/pil.py、
   moduledrawers/svg.py、console_scripts.py、__main__.py。
4. 所有绝对 import（from qrcode.xxx import ...）改为包内相对 import；
   因此不需要往 sys.modules 里塞 qrcode 别名，也不会和真 qrcode 包冲突。
5. main.py 删除 PyPNGImage 兜底：默认渲染器只认 PilImage，Pillow 缺失时抛
   ImportError（提示只保留了 PIL 路径）。
6. image/pil.py 的 Pillow import 改为 try/except 可选（Image = None），
   与上游 new_image 里的 "PIL library not found" 守卫保持一致。
7. image/styles/moduledrawers/__init__.py 置空，不再 import pil / svg 具体绘制器；
   image/base.py 只从 moduledrawers/base 取 QRModuleDrawer 抽象基类。

**三层兜底**（见 client.py 的 _load_qrcode / render_qrcode_png）：

1. 先 import 已安装的 qrcode；
2. 失败再 import 本目录下的 _vendor_qrcode；
3. 都失败才抛 YuketangError("二维码渲染不可用：..."）。
   该调用链全部在函数内部延迟导入，模块顶层不依赖 qrcode / PIL。

## 三、乐课适配改动（相对上游脚本）

上游是 CLI 脚本，domain / cookies / headers 都是模块级全局、在 __main__ 里赋值，
并且大量 print 调试。移植时做了以下改动：

1. **脚本 → 库**：新增 YuketangClient 类；构造函数签名与对外方法按乐课 GUI 适配层约定固定。
2. **全局收进实例**：上游模块级 domain / cookies / headers / user_id / university_id
   全部变成实例属性；没有「只有 __main__ 才赋值」的模块级名字。
3. **依赖延迟 / 可选导入**：requests、websocket、qrcode、PIL 都不在模块顶层硬导入；
   缺 websocket-client 时扫码登录抛清晰错误，缺二维码库时抛「二维码渲染不可用」。
   import api.yuketang.client 在任何缺库环境下都不会炸。
4. **日志**：去掉全部 print，改为延迟导入 api.logger.logger（缺 loguru 时静默降级），
   避免 GUI 下 stdout 被 StreamProxy 接管后污染输出。
5. **回调化 / 可中断**：one_video_watcher 的打印进度改为 on_progress 回调；
   新增 on_video_start 回调（GUI 建进度槽）与 should_stop 中断；
   multiple_watch_video 的固定 4 线程改为 max_workers 参数（默认 1，顺序执行）。
6. **返回值**：watch_video 返回 bool（True=完成/已跳过，False=放弃），
   watch_course_videos 返回 {total, done, skipped, failed}。
7. **健壮性**：get_video_progress 字段缺失时给安全默认值 {completed: False, rate: 0.0,
   watch_length: 0}，绝不抛（上游用正则 + 裸下标取值，缺字段直接 IndexError）。
8. **新增停滞保护**：若进度连续 max_stall（默认 10）轮不增长就放弃该视频并计入 failed，
   防止服务器不涨进度时死循环。这是乐课新增行为，上游没有。
9. **会话复用**：用 thread-local 的 requests.Session（多线程时每线程一个），
   close() 统一释放；上游是每次 requests.request 直连。
10. **登录**：verify-origin-system-bind 之后把全部 Set-Cookie 汇总成
    "k=v; k=v" 形式的 cookie 字符串（上游只取第一条 Set-Cookie 且按逗号硬切），
    并顺手 GET get_user_basic_info 补上 realname。
11. **保留未改的协议细节**：所有接口 URL 与查询参数、心跳包字段含义、
    sq 取 1/2/3、cp 每包递增 learning_rate、0.95 完成阈值、
    以及「Expected available in N second.」的退避逻辑
    （睡 N+0.5 秒后把同一批心跳再 POST 给 /mooc-api/v1/lms/exercise/problem_apply/）。
    这条退避链路与上游逐行对应，属于雨课堂反作弊的关键行为，不做任何"优化"。

## 四、对外接口

    DEFAULT_DOMAIN = ""  # 自动扫码从统一官方入口开始；学校网址由实际登录识别
    class YuketangError(Exception)
    class YuketangClient(domain, cookie="", user_id="", university_id="",
                         learning_rate=12, timeout=10)
        get_university_id(domain)                # staticmethod
        login_by_qrcode(domain, on_qrcode, should_stop=None, timeout=300)  # staticmethod
        fetch_identity()
        list_courses()
        list_videos(classroom_id, course_sign)
        get_video_progress(cid, classroom_id, video_id)
        watch_video(video_id, video_name, cid, classroom_id, skuid,
                    on_progress=None, should_stop=None)
        watch_course_videos(classroom_id, course_sign, cid, skuid,
                            on_progress=None, should_stop=None,
                            on_video_start=None, max_workers=1)
        close()

模块级附加工具：parse_cookie_string、qrcode_available、render_qrcode_png。

## 五、依赖

运行时必需（项目已有）：requests；扫码登录需要 websocket-client（已装）。
二维码：优先已安装的 qrcode；没装则用本目录 vendor 副本，因此不强制要求 qrcode。
Pillow 已装，且是 vendor 二维码渲染的唯一路径依赖。

## 六、已知不确定点

- 各校雨课堂域名 / 接口字段可能与本仓库（BUU）略有差异，
  域名可由界面填写，但 list_videos 的章节字段结构按上游 BUU 版本解析。
- login_by_qrcode 的 verify-origin-system-bind 返回体格式未联网实测，
  目前是「Set-Cookie 汇总 + 可选 user_id 回退 origin_user_id」的兼容写法。
- watch_video 的 max_stall 停滞保护阈值（10 轮）是经验值，未在真实长视频上验证。
- 上游 AI 作业 / 字体解密相关文件未移植，NOTICE 已说明。

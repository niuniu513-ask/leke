# U校园第三方组件与改动

引擎来源：https://github.com/YSJohnson/UnipusAI-Helper ，AGPL-3.0，许可证见 LICENSE。2026-10-12 在线对照当前 AudioRecognizer.py、requirements.txt 与 README。

乐课保留并修改其题目解析、交互与语音上下文流程。改动包括可选 UI 依赖、账号隔离、认证检查、题库先查与大模型后补、无有效答案不猜题、提交失败处理，以及本地 CPU 转录、携带登录态下载媒体、多音频源、短转录和失败重试。audio_recognizer.py 已重新实现为 faster-whisper 与 PyAV，不再要求旧 openai-whisper 或系统 FFmpeg。

教材参考库来自：https://github.com/Han-maker-wp/unipus-agent ，MIT。许可证和固定提交、逐文件校验值见 resource/unipus-reference。32 份教材文档与 1 份索引；部分内容是公开文章/OCR 整理，仅作匹配后的参考，不代表出版社授权答案或平台核验答案。参考资料不会仅按题号自动套用，未匹配教材版本、Unit 和题面时不使用。

语音引擎：https://github.com/SYSTRAN/faster-whisper ，MIT，许可证见 licenses/faster-whisper-MIT.txt。

内置英语模型：https://huggingface.co/Systran/faster-whisper-base.en ，MIT；模型源于 OpenAI Whisper，经 CTranslate2 转换，许可证和逐文件哈希见 resource/whisper-base.en。CPU int8 懒加载，无需联网下载模型。CTranslate2、PyAV、tokenizers 等依赖的许可证随运行依赖保留；PyAV 包含 FFmpeg 库。

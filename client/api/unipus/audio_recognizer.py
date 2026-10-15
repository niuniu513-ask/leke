"""Local CPU speech transcription with authenticated, bounded media downloads."""
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

# Compatibility for callers that inspect the legacy optional dependency.
try:
    import whisper
except ImportError:
    whisper = None

_MODEL_LOCK = threading.Lock()
_MODEL = None
MAX_MEDIA_BYTES = 200 * 1024 * 1024


class AudioTranscriber:
    def __init__(self, driver=None, stop_requested=None):
        self.driver = driver
        self.stop_requested = stop_requested or threading.Event()
        self.local_model = None
        self._transcript_cache = {}
        self.last_error = ''

    def _init_local_model(self):
        global _MODEL
        from faster_whisper import WhisperModel
        import sys
        bundled = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2])) / 'resource' / 'whisper-base.en'
        with _MODEL_LOCK:
            if _MODEL is None:
                if not (bundled / 'model.bin').is_file():
                    raise RuntimeError('语音模型未安装，请使用包含语音模型的新版完整客户端')
                _MODEL = WhisperModel(str(bundled), device='cpu', compute_type='int8',
                                     cpu_threads=min(os.cpu_count() or 2, 4), local_files_only=True)
        self.local_model = _MODEL

    def _download(self, audio_url, target):
        from api._net import new_session
        parsed = urlsplit(audio_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise RuntimeError('媒体地址不支持直接下载，请在浏览器重新打开课程媒体')
        with new_session() as session:
            session.headers['User-Agent'] = 'Mozilla/5.0'
            if self.driver is not None:
                session.headers['Referer'] = self.driver.current_url
                for cookie in self.driver.get_cookies():
                    # requests applies domain/path matching on every redirect.
                    session.cookies.set(cookie['name'], cookie['value'],
                        domain=cookie.get('domain') or urlsplit(self.driver.current_url).hostname,
                        path=cookie.get('path', '/'), secure=cookie.get('secure', False))
            with session.get(audio_url, stream=True, timeout=(10, 30)) as response:
                response.raise_for_status()
                if 'text/html' in response.headers.get('Content-Type', '').lower():
                    raise RuntimeError('媒体返回登录页面，请重新登录课程')
                if int(response.headers.get('Content-Length') or 0) > MAX_MEDIA_BYTES:
                    raise RuntimeError('媒体文件超过 200 MiB，停止下载')
                size = 0
                with open(target, 'wb') as output:
                    for chunk in response.iter_content(128 * 1024):
                        if self.stop_requested.is_set():
                            raise RuntimeError('已停止语音识别')
                        size += len(chunk)
                        if size > MAX_MEDIA_BYTES:
                            raise RuntimeError('媒体文件超过 200 MiB，停止下载')
                        output.write(chunk)
                if not size:
                    raise RuntimeError('媒体文件为空')

    def transcribe(self, audio_url, language='en'):
        self.last_error = ''
        key = (audio_url, language)
        if self.stop_requested.is_set():
            return ''
        if key in self._transcript_cache:
            return self._transcript_cache[key]
        try:
            suffix = Path(urlsplit(audio_url).path).suffix.lower()
            if suffix not in {'.mp3', '.wav', '.mp4', '.m4a', '.ogg', '.webm', '.aac'}:
                suffix = '.media'
            with tempfile.TemporaryDirectory(prefix='leke_audio_') as folder:
                target = Path(folder) / ('media' + suffix)
                self._download(audio_url, target)
                if self.stop_requested.is_set():
                    return ''
                text = self._transcribe_local(str(target), language)
            if text and not self.stop_requested.is_set():
                self._transcript_cache[key] = text
                print(f'       本地语音识别完成（{len(text)} 字符）')
                return text
        except Exception as exc:
            # Never emit signed media URLs, cookies or transcripts into errors.
            self.last_error = str(exc) if isinstance(exc, RuntimeError) else '媒体下载或语音识别失败（' + type(exc).__name__ + '）'
            print('       ' + self.last_error)
        return ''

    def _transcribe_local(self, audio_path, language):
        if self.local_model is None:
            self._init_local_model()
        segments, _ = self.local_model.transcribe(audio_path, language=language,
            beam_size=5, vad_filter=True, condition_on_previous_text=False)
        text = []
        for segment in segments:
            if self.stop_requested.is_set():
                return ''
            if segment.text.strip():
                text.append(segment.text.strip())
        return ' '.join(text)

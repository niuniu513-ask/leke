"""Best-effort log redaction; users must still review logs before sharing."""
import re
from urllib.parse import urlsplit


def redact_message(message: str) -> str:
    if re.search(r'<(?:html|form)\b', message, re.I):
        return '[HTML response omitted from log]'
    message = re.sub(r'<RequestsCookieJar\[.*?\]>', '[cookies redacted]', message, flags=re.S)
    message = re.sub(r'Bearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [redacted]', message, flags=re.I)
    def origin(match):
        try:
            url = urlsplit(match[0])
            return f'{url.scheme}://{url.hostname}/[redacted]'
        except ValueError:
            return '[URL redacted]'
    message = re.sub(r'https?://[^\s\'"<>]+', origin, message)
    # Quoted dictionary/JSON values, then unquoted key=value fields.
    names = r'(?:password|passwd|pwd|cookie|cookies|authorization|token|access_token|refresh_token|dtoken|enc|key|secret|令牌)'
    message = re.sub(r'([\'"]' + names + r'[\'"]\s*:\s*)([\'"])(.*?)\2',
                     lambda m: m[1] + m[2] + '[redacted]' + m[2], message, flags=re.I | re.S)
    message = re.sub(r'(?<!\w)(' + names + r'\s*[=:：]\s*)([^\s,;\}\]\'"]+)',
                     r'\1[redacted]', message, flags=re.I)
    return message


def redact_record(record):
    record['message'] = redact_message(record['message'])

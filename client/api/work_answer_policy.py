"""Strictly map explicit answers to platform fields; never infer an option."""
import re


def parse_model_answer(payload):
    """Accept explicit text or a list of text without splitting scalar strings."""
    if not isinstance(payload, dict):
        return ''
    value = payload.get('Answer')
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value):
        return '\n'.join(v.strip() for v in value)
    return ''


def classify_work_receipt(response):
    """An HTTP success or a truthy string alone cannot confirm a submission."""
    if response.status_code != 200:
        return 'unknown' if response.status_code >= 500 else 'rejected'
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return 'unknown'
    if not isinstance(payload, dict):
        return 'unknown'
    status = payload.get('status')
    if status is True or type(status) is int and status == 1:
        return 'accepted'
    if isinstance(status, str) and status.strip().lower() == 'true':
        return 'accepted'
    if status is False or type(status) is int and status == 0:
        return 'rejected'
    if isinstance(status, str) and status.strip().lower() == 'false':
        return 'rejected'
    return 'unknown'


def map_answer(question, result, tiku):
    if not isinstance(result, str) or not result.strip():
        return ''
    result = result.strip()
    kind = question['type']
    if kind == 'judgement':
        value = tiku.judgement_select(result)
        return 'true' if value is True else ('false' if value is False else '')
    if kind in {'completion', 'shortanswer'}:
        return result
    if kind not in {'single', 'multiple'}:
        return ''
    options = {}
    for line in question.get('options', '').splitlines():
        match = re.match(r'^\s*([A-Z])[.、:：)）\s]*(.*)$', line, re.I)
        if match:
            options[match[1].upper()] = match[2].strip()
    if not options:
        return ''
    explicit = result.upper().rstrip('.。')
    if re.fullmatch(r'[A-Z](?:[\s,，、;|]*[A-Z])*', explicit):
        letters = list(dict.fromkeys(re.findall('[A-Z]', explicit)))
        if all(letter in options for letter in letters) and (kind == 'multiple' or len(letters) == 1):
            return ''.join(sorted(letters))
    def normalized(text):
        return re.sub(r'\s+', ' ', text).strip().casefold()
    parts = [result] if kind == 'single' else re.split(r'[\n|#、]', result)
    letters = []
    for part in parts:
        found = [letter for letter, text in options.items() if normalized(part) == normalized(text)]
        if len(found) != 1:
            return ''
        letters.append(found[0])
    return ''.join(sorted(set(letters)))

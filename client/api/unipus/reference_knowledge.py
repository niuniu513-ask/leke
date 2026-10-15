"""Retrieve attributed textbook notes as evidence, never as executable instructions."""
from functools import lru_cache
from pathlib import Path
import re
import sys


def normalize(text):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', text.casefold())


@lru_cache(maxsize=1)
def documents():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2])) / 'resource/unipus-reference/knowledge'
    rows = []
    for path in root.rglob('*.md'):
        if path.name == 'INDEX.md':
            continue
        text = path.read_text(encoding='utf-8')
        book = normalize(path.stem)
        unit = ''
        for block in re.split(r'(?m)(?=^#{1,2} )', text):
            title = block.split('\n', 1)[0]
            if title.startswith('# ') and re.search(r'\bUnit\s*\d+', title, re.I):
                unit = re.search(r'\bUnit\s*(\d+)', title, re.I)[1]
            if len(block) > 80:
                rows.append((book, path.parent.name, path.name, unit, title, block))
    return rows


def retrieve(page_text, questions, limit=14000):
    """Require an explicit book and unit, plus a distinctive question phrase.

    Multiple editions require an edition match. Notes are OCR-derived references
    for the model to verify against current options, not ready-to-submit answers.
    """
    page = normalize(re.sub(r'第[^版\n]{1,8}版|教师用书|思政智慧版|智慧版', '', page_text))
    edition_page = normalize(page_text)
    units = set(re.findall(r'\bunit\s*(\d+)\b', page_text, re.I))
    phrases = [normalize(q.text) for q in questions if len(normalize(q.text)) >= 24]
    if not units or not phrases:
        return ''
    candidates = []
    for book, edition, filename, unit, title, block in documents():
        clean_book = normalize(re.sub(r'教师用书|第[^版\n]{1,8}版', '', filename[:-3]))
        if clean_book not in page or unit not in units:
            continue
        edition_match = re.search(r'第[^版）]+版', edition)
        if edition_match and normalize(edition_match[0]) not in edition_page:
            continue
        normalized = normalize(block)
        if not any(phrase in normalized for phrase in phrases):
            continue
        candidates.append(f'来源：unipus-agent / {edition}/{filename} / {title}\n{block}')
    if not candidates:
        return ''
    # Include adjacent Scripts/Keys only within the matched named section.
    return '\n\n'.join(candidates)[:limit]

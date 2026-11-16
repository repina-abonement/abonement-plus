"""Поиск номера/фрагмента без изменения канонического телефона и связей клиентов."""
import re
import unicodedata

_RUN=re.compile(r'[+＋]?\d(?:[\d\s\u00a0\u202f().\-–—]*\d)?')


def digits(value):
    return ''.join(str(unicodedata.decimal(ch)) for ch in str(value or '') if ch.isdecimal())


def matches(value,query):
    needle=digits(query)
    if not needle:return 0
    variants={needle}
    # Ведущая 8 может быть междугородним префиксом даже у неполного номера.
    if len(needle)>=3 and needle.startswith('8'):variants.add('7'+needle[1:])
    for run in _RUN.findall(str(value or '')):
        number=digits(run);candidates={number}
        if len(number)==11 and number[0] in '78':candidates.add('7'+number[1:])
        elif len(number)==10:candidates.add('7'+number)
        if any(part in candidate for candidate in candidates for part in variants):return 1
    return 0

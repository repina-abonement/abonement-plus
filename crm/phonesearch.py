"""Поиск номера/фрагмента без изменения канонического телефона и связей клиентов."""
import re
import unicodedata

_RUN=re.compile(r'[+＋]?\d(?:[\d\s\u00a0\u202f().\-–—]*\d)?')


def digits(value):
    return ''.join(str(unicodedata.decimal(ch)) for ch in str(value or '') if ch.isdecimal())


def matches(value,query,*,min_digits=0):
    needle=digits(query)
    if not needle:return 0
    # Префикс 8/7 эквивалентен только в начале номера, не в произвольном фрагменте.
    prefix='7'+needle[1:] if len(needle)>=2 and needle.startswith('8') else None
    for run in _RUN.findall(str(value or '')):
        number=digits(run)
        if len(number)<min_digits:continue
        candidates={number}
        if len(number)==11 and number[0] in '78':candidates.add('7'+number[1:])
        elif len(number)==10:candidates.add('7'+number)
        if any(needle in candidate for candidate in candidates):return 1
        if prefix and any(candidate.startswith(prefix) for candidate in candidates):return 1
    return 0


def message_matches(value,query):
    # Короткий запрос ищется в полном телефоне, а не в дате или сумме сообщения.
    return matches(value,query,min_digits=10)

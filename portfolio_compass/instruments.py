"""Security identities and verified aliases; fuzzy matches are suggestions only."""

import re
import unicodedata
from difflib import SequenceMatcher

from .models import Instrument

CATALOG = (
    Instrument('AAPL', 'Apple', aliases=('Apple Inc.', 'Apple Inc'), sector='Technology'),
    Instrument('MSFT', 'Microsoft', aliases=('Microsoft Corporation', 'Microsoft Corp'), sector='Technology'),
    Instrument('TSLA', 'Tesla', aliases=('Tesla Inc.', 'Tesla Inc'), sector='Consumer cyclical'),
    Instrument('SPY', 'SPDR S&P 500 ETF Trust', 'ETF', 'NYSE Arca', aliases=('SPY',)),
)


def key(text):
    return ' '.join(unicodedata.normalize('NFKC', str(text)).casefold().split())


def aliases(instrument):
    return {key(x) for x in (instrument.symbol, instrument.name, *instrument.aliases)}


def valid_query(text):
    return bool(re.fullmatch(r"[\w .&'()/-]{1,100}", text, re.UNICODE)) and not any(ord(c) < 32 for c in text)


def catalog_candidates(text):
    if not valid_query(text):
        return []
    query = key(text)
    exact = [item for item in CATALOG if query in aliases(item)]
    if exact:
        return exact
    return [item for item in CATALOG if any(query in a or SequenceMatcher(None, query, a).ratio() > .72 for a in aliases(item))]


def verified_candidate(info, symbol):
    if not re.fullmatch(r'[A-Z0-9^][A-Z0-9.\-^=]{0,19}', symbol):
        return None
    kinds = {'EQUITY': 'Stock', 'ETF': 'ETF', 'MUTUALFUND': 'Fund'}
    kind = kinds.get(info.get('quoteType'))
    exchange = info.get('exchange', '')
    if not kind or info.get('currency') != 'USD' or exchange not in {'NMS', 'NGM', 'NCM', 'NYQ', 'ASE', 'PCX', 'BTS', 'BATS', 'NYS', 'NAS', 'PNK', 'OEM', 'OBB', 'OQB', 'OQX', 'NMF'}:
        return None
    name = info.get('longName') or info.get('shortName')
    if not name or len(name) > 200 or any(ord(c) < 32 for c in name):
        return None
    known = next((x for x in CATALOG if x.symbol == symbol), None)
    extra = tuple({str(info.get('shortName') or name), *(known.aliases if known else ()), *( (known.name,) if known else ())})
    return Instrument(symbol, name, kind, info.get('fullExchangeName', exchange), 'USD', extra, info.get('sector', 'Unknown'), 'Yahoo metadata')

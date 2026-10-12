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


def resolve_batch(inputs, provider_lookup):
    """One batch: auto-resolve codes/clear names; return only uncertain names for review."""
    resolved, ambiguous, errors = {}, {}, {}
    for raw in dict.fromkeys(inputs):
        local = catalog_candidates(raw)
        exact = [item for item in local if key(raw) in aliases(item)]
        if len(exact) == 1:
            resolved[raw] = exact[0]
            continue
        candidates, error = provider_lookup(raw)
        candidates = list({item.symbol: item for item in candidates}.values())
        codes = [item for item in candidates if key(raw).replace('.', '-') == key(item.symbol).replace('.', '-')]
        if len(codes) == 1:
            resolved[raw] = codes[0]
        elif re.fullmatch(r'[A-Z]{1,6}(?:[.-][A-Z])?', raw):
            errors[raw] = error or 'Code could not be verified. Correct the code or retry validation.'
        elif len(candidates) == 1:
            resolved[raw] = candidates[0]
        elif candidates:
            ambiguous[raw] = candidates
        else:
            errors[raw] = error or 'No supported investment matched this name. Correct it and validate again.'
    return resolved, ambiguous, errors

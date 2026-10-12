"""Bounded public-data adapters. Private transactions are never cached here."""

import math
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from .instruments import valid_query, verified_candidate
from .models import Action, Issue, MarketData


@lru_cache(maxsize=8)
def calendar(start, end):
    return xcals.get_calendar('XNYS', start=str(start), end=str(end))


def session_dates(start, end):
    return set(calendar(start - timedelta(days=10), end + timedelta(days=10)).sessions.date)


def last_completed_session():
    now = datetime.now(timezone.utc)
    cal = calendar(now.date() - timedelta(days=15), now.date() + timedelta(days=5))
    return max(s.date() for s in cal.sessions if cal.session_close(s).to_pydatetime() + timedelta(minutes=20) <= now)


def _yf():
    import yfinance as yf
    # This contains public quote-provider cookies/timezone metadata, not portfolios.
    cache = Path(__file__).resolve().parents[1] / 'artifacts' / 'market-cache'
    cache.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache))
    return yf


def lookup(query):
    if not valid_query(query):
        return [], 'Use a short company/fund name or ticker without control characters.'
    return _lookup(query, int(time.time() // 3600))


@lru_cache(maxsize=400)
def _lookup(query, bucket):
    try:
        yf = _yf()
        search = yf.Search(query, max_results=6, news_count=0, enable_fuzzy_query=False, timeout=12)
        symbols = list(dict.fromkeys(q.get('symbol', '') for q in search.quotes))[:6]
        # Search results alone do not authorize an instrument.
        result = []
        for symbol in symbols:
            if not valid_query(symbol):
                continue
            info = yf.Ticker(symbol).get_info()
            item = verified_candidate(info, symbol)
            if item:
                result.append(item)
        return result, '' if result else 'No supported US/USD security matched. Try an exact ticker.'
    except Exception:
        return [], 'Security lookup is unavailable. Retry later; no identity was guessed.'


@lru_cache(maxsize=400)
def _history(symbol, start, end, bucket):
    return _yf().Ticker(symbol).history(start=start, end=end, auto_adjust=False,
                                       actions=True, repair=False, timeout=15, raise_errors=True)


def normalize_history(symbol, frame):
    """Yahoo closes/dividends are split-adjusted. Undo future splits for event-date units."""
    if frame.empty or 'Close' not in frame:
        raise ValueError('No price history.')
    prices, actions, benchmark = {}, [], {}
    future_factor = Decimal('1')
    for timestamp, row in frame.sort_index(ascending=False).iterrows():
        day = timestamp.date()
        close = row.get('Close')
        if pd.notna(close) and math.isfinite(float(close)) and close > 0:
            prices[day] = Decimal(str(close)) * future_factor
        adjusted = row.get('Adj Close')
        if pd.notna(adjusted) and math.isfinite(float(adjusted)) and adjusted > 0:
            benchmark[day] = Decimal(str(adjusted))
        dividend = row.get('Dividends', 0)
        if pd.notna(dividend) and dividend > 0:
            actions.append(Action(f'{symbol}:{day}:div', day, symbol, 'DIVIDEND', Decimal(str(dividend)) * future_factor))
        ratio = row.get('Stock Splits', 0)
        if pd.notna(ratio) and ratio > 0:
            actions.append(Action(f'{symbol}:{day}:split', day, symbol, 'SPLIT', Decimal(str(ratio))))
            future_factor *= Decimal(str(ratio))
    return prices, sorted(actions, key=lambda x: x.day), benchmark


def fill_calendar_gaps(prices, start, end, sessions):
    """Fill weekends/holidays only. A missing exchange session remains missing."""
    output, previous = {}, None
    for day in pd.date_range(start, end).date:
        if day in prices:
            previous = prices[day]
        elif day in sessions:
            previous = None
        if previous is not None:
            output[day] = previous
    return output


def fetch_market(symbols, start, end, refresh=False):
    if len(symbols) > 100:
        raise ValueError('Maximum 100 portfolio securities.')
    result = MarketData(as_of=end)
    bucket = int(time.time() // 900)
    if refresh:
        _history.cache_clear()
    fetch_start = start - timedelta(days=10)
    sessions = session_dates(fetch_start, end)
    for symbol in dict.fromkeys([*symbols, 'SPY']):
        try:
            frame = _history(symbol, str(fetch_start), str(end + timedelta(days=1)), bucket)
            prices, actions, adjusted = normalize_history(symbol, frame)
            if symbol in symbols:
                result.prices[symbol] = prices
                result.actions.extend(actions)
                if not {'Dividends', 'Stock Splits'}.issubset(frame.columns):
                    result.income_complete = False
                    result.issues.append(Issue('warning', f'{symbol}: corporate-action coverage is unknown.'))
                if 'Capital Gains' in frame and frame['Capital Gains'].fillna(0).ne(0).any():
                    result.income_complete = False
                    result.issues.append(Issue('warning', f'{symbol}: capital-gain distributions require manual records; total-return comparison disabled.'))
            if symbol == 'SPY':
                result.benchmark = fill_calendar_gaps(adjusted, fetch_start, end, sessions)
        except Exception:
            result.issues.append(Issue('warning', f'{symbol}: market history is unavailable. Retry later.'))
            if symbol in symbols:
                result.income_complete = False
    return result


def demo_market(start=date(2024, 1, 3), end=date(2026, 10, 9)):
    """Reproducible synthetic fixture, deliberately not historical market claims."""
    result = MarketData(demo=True, as_of=end)
    sessions = sorted(d for d in session_dates(start - timedelta(days=10), end) if start - timedelta(days=10) <= d <= end)
    parameters = {'AAPL': (180, .00032, 7), 'MSFT': (365, .00028, 16), 'TSLA': (245, -.0001, 39), 'SPY': (470, .0003, 10)}
    for symbol, (base, slope, wave) in parameters.items():
        prices = {day: Decimal(str(round(base * math.exp(slope * n) + wave * math.sin(n / 35), 4))) for n, day in enumerate(sessions)}
        if symbol != 'SPY':
            result.prices[symbol] = prices
        else:
            result.benchmark = fill_calendar_gaps(prices, sessions[0], end, set(sessions))
    for year in (2024, 2025, 2026):
        for month in (2, 5, 8, 11):
            proposed = date(year, month, 15)
            dates = [d for d in sessions if d >= proposed]
            if not dates:
                continue
            for symbol, amount in [('AAPL', '.25'), ('MSFT', '.80')]:
                day = dates[0]
                result.actions.append(Action(f'{symbol}:{day}:div', day, symbol, 'DIVIDEND', Decimal(amount)))
    result.issues.append(Issue('info', 'Synthetic offline prices and distributions through October 9, 2026. Not actual market performance.'))
    return result

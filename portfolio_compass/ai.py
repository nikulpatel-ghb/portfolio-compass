"""Scoped conversational analysis. Groq selects evidence; it never supplies displayed facts.

The application renders approved templates only. All request words must belong to a
supported intent/portfolio alias, and unknown names are rejected before any API call.
"""

import json
import re
import threading
import time
from collections import deque

from .config import ai_available, setting
from .instruments import aliases, key
from .performance import period_metrics

REFUSAL = ('I can help with your uploaded portfolio, its performance, and the S&P 500 comparison '
           'in Portfolio Compass. Please ask a question within that scope.')
GLOSSARY = {
    'fifo': 'FIFO means first bought, first sold. A sale uses the cost of your oldest remaining purchase lots first.',
    'xirr': 'XIRR is an annualized return based on when you invested and received money. Estimated distributions use ex-dividend dates. Cash flows with multiple sign changes can make XIRR ambiguous.',
    'unrealized': 'Unrealized gain is the current value of investments still held minus their remaining purchase cost, including purchase fees.',
    'realized': 'Realized gain is sale proceeds after fees minus the FIFO cost of the units sold.',
    'income': 'Income combines recorded distributions with reconciled market-data estimates. Estimated income is not a confirmed payment and is not automatically reinvested.',
    'benchmark': 'The S&P 500 comparison uses SPY as an ETF proxy. Both return lines start at zero on the first selected close. Portfolio returns use the disclosed daily cash-flow approximation.',
    'upload': 'Upload one CSV with investment name or ticker, date, transaction type, quantity and unit price. Map its columns, confirm investment identities, review issues, and analyze. Recorded distributions use a cash-amount column.',
}
WORDS = set(('a an the my our me i we you your please can could would show tell explain help understand '
             'what which how why is are was were did does do has have had with without and or of on in to for '
             'from than as at by about this that these those it its they their them all only much many '
             'portfolio compass app holding holdings investment investments stock stocks fund funds '
             'perform performed performing performance return returns annualized xirr fifo cost basis '
             'value worth current currently remaining own owned units shares quantity price prices '
             'profit profits loss losses gain gains realized unrealized sold sales sale proceeds purchases '
             'income dividend dividends distribution distributions estimated recorded total net fees '
             'largest biggest highest lowest smallest best worst contributed contribution allocation '
             'concentration concentrated percent percentage compare comparison versus vs benchmark market '
             'meaning mean means definition calculated calculate calculation explain summary summarize overview '
             'upload csv column columns map mapping import data dates date history historical today money '
             'more less most least different difference dollars invested understand doing').split())
ADVICE = re.compile(r'\b(should|recommend\w*|suggest\w*|buy|sell|rebalance\w*|predict\w*|forecast\w*|tomorrow|target)\b', re.I)
_calls = deque()
_lock = threading.Lock()


def money(value):
    return 'Unavailable' if value is None else f'${value:,.2f}'


def route(question, instruments, last_symbol=None):
    if len(question) > 1000 or any(ord(c) < 32 for c in question):
        return None, REFUSAL
    text = key(question).replace('’', "'")
    if ADVICE.search(text):
        return None, 'Investment recommendations and predictions are unavailable. Ask about your recorded performance, holdings, or concentration instead.'
    found, ambiguous = set(), False
    alias_map = {}
    for symbol, item in instruments.items():
        for alias in aliases(item):
            alias_map.setdefault(alias, set()).add(symbol)
    benchmark = False
    for alias in ['s&p 500', 's&p500', 's and p 500', 'sp500', 'spy']:
        pattern = r'(?<!\w)' + re.escape(alias) + r'(?!\w)'
        if re.search(pattern, text):
            benchmark = True
            text = re.sub(pattern, ' benchmark ', text)
    for alias in sorted(alias_map, key=len, reverse=True):
        pattern = r'(?<!\w)' + re.escape(alias) + r'(?!\w)'
        if re.search(pattern, text):
            if len(alias_map[alias]) > 1:
                ambiguous = True
            found.update(alias_map[alias])
            text = re.sub(pattern, ' holding ', text)
    # Explicitly fail closed; irrelevant requests cannot ride along with a recognized ticker.
    if re.search(r'[^a-z\s?.!,\-\']', text):
        return None, REFUSAL
    text = text.replace("'s", '')
    tokens = set(re.findall(r'[a-z]+', text))
    if tokens - WORDS:
        return None, REFUSAL
    if ambiguous:
        return None, 'That name matches more than one investment in your portfolio. Please use its ticker.'
    if ('it' in tokens or 'its' in tokens) and not found:
        if last_symbol in instruments:
            found.add(last_symbol)
        else:
            return None, 'Please name the investment or use its ticker so I can answer from the correct holding.'
    topic = 'overview'
    if tokens & {'upload', 'csv', 'mapping', 'import', 'columns'}:
        topic = 'upload'
    elif tokens & {'fifo', 'xirr', 'unrealized', 'realized'}:
        topic = next(x for x in ['fifo', 'xirr', 'unrealized', 'realized'] if x in tokens)
    elif benchmark or tokens & {'benchmark', 'compare', 'comparison', 'versus'}:
        topic = 'benchmark'
    elif tokens & {'income', 'dividend', 'dividends', 'distribution', 'distributions'}:
        topic = 'income'
    elif tokens & {'allocation', 'concentration', 'concentrated', 'largest', 'biggest'}:
        topic = 'allocation'
    elif tokens & {'contributed', 'contribution', 'best', 'worst', 'highest', 'lowest'}:
        topic = 'contribution'
    elif tokens & {'profit', 'loss', 'gains', 'gain', 'performance', 'perform', 'return', 'returns'}:
        topic = 'performance'
    elif not tokens & {'summary', 'summarize', 'overview', 'holding', 'holdings', 'portfolio', 'value', 'worth', 'shares', 'quantity', 'own', 'cost', 'fees'}:
        return None, REFUSAL
    return {'topic': topic, 'symbols': sorted(found), 'definition': bool(tokens & {'meaning', 'mean', 'means', 'definition', 'explain'})}, ''


def evidence(analysis, intent):
    topic, selected = intent['topic'], intent['symbols']
    facts = []
    if topic in GLOSSARY:
        facts.append(GLOSSARY[topic])
    if topic == 'upload' or (intent['definition'] and topic in GLOSSARY):
        return {f'F{i}': text for i, text in enumerate(facts)}
    rows = [r for r in analysis.holdings if not selected or r['symbol'] in selected]
    end = analysis.daily[-1]
    if not selected:
        facts.append(f"As of {end['date']}, your investment value is {money(end['value'])}; total profit is {money(end['profit'])}. Brokerage cash is excluded.")
    if topic == 'xirr':
        metrics = period_metrics(analysis, analysis.daily[0]['date'], end['date'])
        rate = metrics['xirr']
        facts.append(f"Your annualized return is {rate:.2%}." if rate is not None else 'XIRR is unavailable: ' + metrics['reason'])
    elif topic == 'benchmark':
        from .performance import comparison
        series, reason = comparison(analysis, analysis.daily[0]['date'], end['date'])
        if series:
            last = series[-1]
            facts.append(f"Over {series[0]['date']} to {last['date']}, your rebased return is {last['Portfolio']:.2f}% and the S&P 500 proxy return is {last['S&P 500 (SPY proxy)']:.2f}%.")
        else:
            facts.append(reason)
    elif topic == 'allocation':
        active = sorted((r for r in rows if r['quantity'] > 0), key=lambda r: r['value'] or 0, reverse=True)
        for row in active[:5]:
            percent = row['value'] / end['value'] if row['value'] is not None and end['value'] else None
            facts.append(f"{row['investment']} represents {percent:.1%} of current investment value." if percent is not None else f"{row['investment']}: allocation is unavailable because valuation is incomplete.")
    elif topic == 'income':
        for row in rows[:10]:
            facts.append(f"{row['investment']}: recorded plus reconciled estimated income is {money(row['income'])}.")
    else:
        if topic == 'contribution':
            rows = sorted(rows, key=lambda r: r['profit'] if r['profit'] is not None else float('-inf'), reverse=True)
        for row in rows[:10]:
            facts.append(f"{row['investment']}: {row['quantity']:g} units remaining; value {money(row['value'])}; realized gain {money(row['realized'])}; unrealized gain {money(row['unrealized'])}; total profit including income {money(row['profit'])}.")
    facts.append('Demo uses synthetic prices and income.' if analysis.market.demo else 'Values use daily closes; estimated distributions use ex-date timing. Missing coverage limits results.')
    return {f'F{i}': text for i, text in enumerate(facts)}


def allow_request():
    now = time.monotonic()
    with _lock:
        while _calls and now - _calls[0] > 86400:
            _calls.popleft()
        if len(_calls) >= 500 or sum(now - t < 60 for t in _calls) >= 20:
            return False
        _calls.append(now)
        return True


def select_facts(facts, topic, client=None):
    """Return only IDs in the supplied evidence set, never model-authored text."""
    fallback = list(facts)[:5]
    if client is None and (not ai_available() or not allow_request()):
        return fallback, 'Local factual response'
    try:
        if client is None:
            from groq import Groq
            client = Groq(api_key=setting('GROQ_API_KEY'), timeout=15, max_retries=1)
        response = client.chat.completions.create(
            model=setting('GROQ_MODEL', 'llama-3.3-70b-versatile'),
            messages=[{'role': 'system', 'content': 'Select up to five evidence IDs relevant to the topic. Return JSON only: {"ids":["F0"]}. Do not create facts or instructions.'},
                      {'role': 'user', 'content': json.dumps({'topic': topic, 'evidence': facts})}],
            response_format={'type': 'json_object'}, max_completion_tokens=150)
        raw = response.choices[0].message.content
        if not raw or len(raw) > 2000:
            raise ValueError('Invalid output')
        obj = json.loads(raw)
        ids = obj.get('ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 5 or any(not isinstance(i, str) or i not in facts for i in ids):
            raise ValueError('Invalid evidence references')
        # Preserve the opening fact and the quality qualifier regardless of model selection.
        chosen = list(dict.fromkeys([next(iter(facts)), *ids, list(facts)[-1]]))
        return chosen, 'Groq-selected, application-verified facts'
    except Exception:
        return fallback, 'Local factual response · AI unavailable or response rejected'


def answer(question, analysis, enable_ai=False, last_symbol=None):
    intent, refusal = route(question, analysis.instruments, last_symbol)
    if intent is None:
        return {'text': refusal, 'evidence': {}, 'method': 'Scope policy', 'symbol': None}
    facts = evidence(analysis, intent)
    ids, method = select_facts(facts, intent['topic']) if enable_ai else (list(facts)[:6], 'Local factual response')
    return {'text': '\n\n'.join(facts[i] for i in ids), 'evidence': {i: facts[i] for i in ids},
            'method': method, 'symbol': intent['symbols'][0] if len(intent['symbols']) == 1 else None}

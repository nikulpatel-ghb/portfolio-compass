"""FIFO accounting with exact ledger arithmetic and explicit distribution reconciliation."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from .models import ZERO, Analysis, Issue


@dataclass
class Lot:
    quantity: Decimal
    cost: Decimal


class Book:
    def __init__(self):
        self.lots = defaultdict(deque)
        self.quantity = defaultdict(lambda: ZERO)
        self.cost = defaultdict(lambda: ZERO)
        self.realized = defaultdict(lambda: ZERO)
        self.purchases = defaultdict(lambda: ZERO)
        self.sales = defaultdict(lambda: ZERO)

    def split(self, symbol, ratio):
        if ratio <= 0 or not ratio.is_finite():
            raise ValueError(f'{symbol}: invalid split ratio.')
        for lot in self.lots[symbol]:
            lot.quantity *= ratio
        self.quantity[symbol] *= ratio

    def trade(self, tx):
        if tx.kind == 'DISTRIBUTION':
            return
        symbol, qty = tx.symbol, tx.quantity
        if tx.kind == 'BUY':
            amount = qty * tx.price + tx.fees
            self.lots[symbol].append(Lot(qty, amount))
            self.quantity[symbol] += qty
            self.cost[symbol] += amount
            self.purchases[symbol] += amount
            return
        if qty > self.quantity[symbol]:
            raise ValueError(f'Row {tx.row}: {symbol} sale exceeds units owned on {tx.day}. Check history, order, or split basis.')
        consumed, remaining = ZERO, qty
        while remaining > 0:
            lot = self.lots[symbol][0]
            take = min(remaining, lot.quantity)
            allocation = lot.cost if take == lot.quantity else lot.cost * take / lot.quantity
            consumed += allocation
            lot.cost -= allocation
            lot.quantity -= take
            remaining -= take
            if not lot.quantity:
                self.lots[symbol].popleft()
        proceeds = qty * tx.price - tx.fees
        self.quantity[symbol] -= qty
        self.cost[symbol] -= consumed
        if not self.quantity[symbol]:
            self.cost[symbol] = ZERO
        self.realized[symbol] += proceeds - consumed
        self.sales[symbol] += proceeds


def estimate_income(transactions, actions, end):
    """Replay split and trade events; dividend entitlement precedes same-day trades."""
    events = defaultdict(list)
    for tx in transactions:
        if tx.day <= end:
            events[tx.day].append((2, tx))
    for action in actions:
        if action.day <= end:
            events[action.day].append((0 if action.kind == 'SPLIT' else 1, action))
    book, result = Book(), []
    for day in sorted(events):
        for priority, event in sorted(events[day], key=lambda x: x[0]):
            if priority == 0:
                book.split(event.symbol, event.value)
            elif priority == 1:
                units = book.quantity[event.symbol]
                if units > 0:
                    result.append({'id': event.id, 'date': day, 'symbol': event.symbol,
                                   'amount': units * event.value, 'units': units,
                                   'source': 'Estimated (ex-date)', 'event': event.id})
            else:
                book.trade(event)
    return result


def reconcile_income(transactions, estimates, decisions=None):
    decisions = decisions or {}
    actuals = [t for t in transactions if t.kind == 'DISTRIBUTION']
    candidates = {tx.id: [e for e in estimates if e['symbol'] == tx.symbol and abs((tx.day - e['date']).days) <= 45] for tx in actuals}
    claimed, conflicts, records = set(), [], []
    for tx in actuals:
        nearby = candidates[tx.id]
        choice = decisions.get(tx.id)
        if choice is None:
            exact = [e for e in nearby if abs(tx.amount - e['amount']) <= Decimal('.02')]
            if len(exact) == 1 and sum(exact[0] in candidates[t.id] for t in actuals) == 1:
                choice = exact[0]['id']
            elif nearby:
                conflicts.append({'id': tx.id, 'row': tx.row, 'symbol': tx.symbol, 'date': tx.day,
                                  'amount': float(tx.amount), 'candidates': nearby})
        if choice and choice != 'independent':
            if choice not in {e['id'] for e in nearby} or choice in claimed:
                raise ValueError('Each uploaded distribution must match at most one unclaimed provider event.')
            claimed.add(choice)
        records.append({'id': tx.id, 'date': tx.day, 'symbol': tx.symbol, 'amount': tx.amount,
                        'units': None, 'source': 'Recorded', 'event': choice or ''})
    records.extend(e for e in estimates if e['id'] not in claimed)
    return sorted(records, key=lambda x: x['date']), conflicts


def analyze(transactions, instruments, market, sessions, decisions=None, estimate=True):
    if not transactions:
        raise ValueError('No transactions to analyze.')
    end = market.as_of
    if end is None or end < transactions[0].day:
        raise ValueError('The valuation date must not precede the first transaction.')
    if transactions[-1].day > end:
        raise ValueError('Transactions after the last completed market session cannot be valued yet.')
    estimates = estimate_income(transactions, market.actions, end) if estimate else []
    income, conflicts = reconcile_income(transactions, estimates, decisions)
    by_day, actions, payouts = defaultdict(list), defaultdict(list), defaultdict(list)
    for tx in transactions:
        by_day[tx.day].append(tx)
    for event in market.actions:
        if event.kind == 'SPLIT':
            actions[event.day].append(event)
    for item in income:
        payouts[item['date']].append(item)
    issues = list(market.issues)
    nontrading = sorted({t.day for t in transactions if t.day not in sessions})
    if nontrading:
        issues.append(Issue('warning', f'{len(nontrading)} transaction date(s) fall outside exchange sessions. Original dates retained; prior closes used.'))
    book, daily, flows = Book(), [], []
    income_totals = defaultdict(lambda: ZERO)
    latest, price_day = {}, {}
    symbols = list(instruments)
    start = transactions[0].day
    # Seed only prior quotes, never future ones.
    for symbol in symbols:
        earlier = [d for d in market.prices.get(symbol, {}) if d < start]
        if earlier:
            seed = max(earlier)
            latest[symbol] = market.prices[symbol][seed]
            price_day[symbol] = seed
    cum_buys = cum_sales = cum_income = ZERO
    previous_value, last_session = ZERO, max((d for d in sessions if d < start), default=None)
    day = start
    complete_previous = True
    final_values = {}
    while day <= end:
        for action in actions[day]:
            book.split(action.symbol, action.value)
        buys, sales, paid = ZERO, ZERO, ZERO
        for tx in by_day[day]:
            book.trade(tx)
            if tx.kind == 'BUY':
                value = tx.quantity * tx.price + tx.fees
                buys += value
                flows.append((day, -float(value)))
            elif tx.kind == 'SELL':
                value = tx.quantity * tx.price - tx.fees
                sales += value
                flows.append((day, float(value)))
        for item in payouts[day]:
            paid += item['amount']
            income_totals[item['symbol']] += item['amount']
            flows.append((day, float(item['amount'])))
        if day in sessions:
            last_session = day
        values, complete = {}, True
        for symbol in symbols:
            quote = market.prices.get(symbol, {}).get(day)
            if quote is not None and quote > 0 and quote.is_finite():
                latest[symbol], price_day[symbol] = quote, day
            if not book.quantity[symbol]:
                values[symbol] = ZERO
            elif symbol in latest and price_day[symbol] == last_session:
                values[symbol] = book.quantity[symbol] * latest[symbol]
            else:
                values[symbol] = None
                complete = False
        subtotal = sum((v for v in values.values() if v is not None), ZERO)
        value = subtotal if complete else None
        cum_buys += buys
        cum_sales += sales
        cum_income += paid
        denominator = previous_value + buys if previous_value is not None else ZERO
        daily_return = None
        if complete and complete_previous and denominator > 0:
            daily_return = float((value + sales + paid) / denominator - 1)
        daily.append({'date': day, 'value': float(value) if value is not None else None,
                      'priced_subtotal': float(subtotal), 'purchases': float(cum_buys),
                      'sale_proceeds': float(cum_sales), 'income': float(cum_income),
                      'profit': float(value + cum_sales + cum_income - cum_buys) if value is not None else None,
                      'daily_return': daily_return, 'complete': complete,
                      'buy_flow': float(buys), 'sale_flow': float(sales), 'income_flow': float(paid)})
        previous_value, complete_previous, final_values = value, complete, values
        day += timedelta(days=1)
    holdings = []
    for symbol, instrument in instruments.items():
        value, cost = final_values[symbol], book.cost[symbol]
        unrealized = value - cost if value is not None else None
        holdings.append({'symbol': symbol, 'investment': instrument.label, 'type': instrument.kind,
                         'sector': instrument.sector, 'quantity': float(book.quantity[symbol]),
                         'price': float(latest[symbol]) if symbol in latest else None,
                         'price_date': price_day.get(symbol), 'cost': float(cost),
                         'value': float(value) if value is not None else None,
                         'realized': float(book.realized[symbol]),
                         'unrealized': float(unrealized) if unrealized is not None else None,
                         'income': float(income_totals[symbol]),
                         'profit': float(book.realized[symbol] + unrealized + income_totals[symbol]) if unrealized is not None else None})
    if any(not d['complete'] for d in daily):
        issues.append(Issue('warning', 'Missing trading-day prices interrupt valuations and returns. Missing values are not zero.'))
    if estimates:
        issues.append(Issue('info', 'Estimated income uses eligible units and ex-dividend dates, not confirmed payment dates. XIRR timing is approximate.'))
    if not estimate:
        issues.append(Issue('warning', 'Online income estimation is off. Results exclude unrecorded income.'))
    return Analysis(transactions, instruments, holdings, daily, income, issues, conflicts, flows, market)

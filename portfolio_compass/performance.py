"""Investor return solving and selected-period comparisons."""

from collections import defaultdict
from datetime import timedelta

import numpy as np
from scipy.optimize import brentq


def xirr(cashflows):
    """Find numerically isolated ACT/365 roots over log(1+r) in [-20, 20].

    Multiple cash-flow sign changes only imply possible multiple roots. Scan
    both NPV and its derivative to split intervals at extrema, including
    touching roots and close pairs that a sign-only grid could overlook.
    This is a bounded numerical search, not a proof of global uniqueness.
    """
    merged = defaultdict(float)
    for day, amount in cashflows:
        if not np.isfinite(amount):
            return None, 'Cash flows must contain finite amounts.'
        merged[day] += amount
    ordered = sorted((d, a) for d, a in merged.items() if abs(a) > 1e-10)
    if len(ordered) < 2 or not any(a < 0 for _, a in ordered) or not any(a > 0 for _, a in ordered):
        return None, 'Needs dated investments and proceeds or a positive ending value.'
    amounts = np.array([a for _, a in ordered], dtype=float)
    years = np.array([(d - ordered[0][0]).days / 365 for d, _ in ordered])
    scale = float(np.max(np.abs(amounts)))
    amounts /= scale

    def evaluate(log_rate, derivative=False):
        exponent = -log_rate * years
        weights = np.exp(exponent - max(float(exponent.max()), 0))
        coefficients = -years * amounts if derivative else amounts
        denominator = float(np.dot(np.abs(coefficients), weights))
        # Relative residual avoids accepting underflowed raw NPV as a root.
        return float(np.dot(coefficients, weights)) / denominator if denominator else np.nan

    def npv(log_rate):
        return evaluate(log_rate)

    try:
        grid = np.linspace(-20, 20, 4097)
        derivative = np.array([evaluate(x, True) for x in grid])
        extrema = []
        for index in range(len(grid) - 1):
            left, right = derivative[index:index + 2]
            if left == 0:
                extrema.append(float(grid[index]))
            elif np.isfinite(left) and np.isfinite(right) and np.signbit(left) != np.signbit(right):
                extrema.append(brentq(lambda x: evaluate(x, True), grid[index], grid[index + 1], xtol=1e-13))
        points = sorted(set([*grid, *extrema]))
        roots = []

        def add_root(root):
            if abs(npv(root)) < 1e-10 and not any(abs(root - old) < 1e-8 for old in roots):
                roots.append(root)

        for point in extrema:
            if abs(npv(point)) < 1e-12:
                add_root(point)
        values = [npv(x) for x in points]
        for index, (left, right) in enumerate(zip(values, values[1:])):
            if left == 0:
                add_root(points[index])
            elif np.isfinite(left) and np.isfinite(right) and np.signbit(left) != np.signbit(right):
                add_root(brentq(npv, points[index], points[index + 1], xtol=1e-13))
        if values[-1] == 0:
            add_root(points[-1])
        if len(roots) > 1:
            return None, 'Multiple annualized returns were detected; XIRR is ambiguous for these cash flows.'
        if not roots:
            return None, 'No reliable annualized return was found in the supported numerical range.'
        return float(np.expm1(roots[0])), ('Annualized, ACT/365. One root detected in the bounded numerical search; '
                                         'estimated distributions use ex-date timing.')
    except (ValueError, OverflowError):
        return None, 'No reliable annualized return in the supported numerical range.'


def period_metrics(analysis, start, end):
    rows = [d for d in analysis.daily if start <= d['date'] <= end]
    if not rows:
        return {'xirr': None, 'reason': 'No dates in range.', 'profit': None, 'rows': []}
    before = next((d for d in analysis.daily if d['date'] == start - timedelta(days=1)), None)
    opening = before['value'] if before else 0.0
    ending = rows[-1]['value']
    if opening is None or ending is None or analysis.conflicts:
        return {'xirr': None, 'reason': 'Opening/closing valuation or income reconciliation is incomplete.', 'profit': None, 'rows': rows}
    flows = [(d, amount) for d, amount in analysis.cashflows if start <= d <= end]
    if opening:
        flows.insert(0, (start - timedelta(days=1), -opening))
    flows.append((end, ending))
    rate, reason = xirr(flows)
    profit = ending - opening + sum(amount for d, amount in analysis.cashflows if start <= d <= end)
    return {'xirr': rate, 'reason': reason, 'profit': profit, 'rows': rows, 'opening': opening,
            'purchases': sum(r['buy_flow'] for r in rows), 'sales': sum(r['sale_flow'] for r in rows),
            'income': sum(r['income_flow'] for r in rows)}


def comparison(analysis, start, end):
    rows = [d for d in analysis.daily if start <= d['date'] <= end]
    if not rows or analysis.conflicts or not analysis.market.income_complete:
        return [], 'Comparison requires complete income coverage and reconciled distributions.'
    if any(r['daily_return'] is None for r in rows):
        return [], 'Choose a continuous invested period with complete prices; gaps and zero capital break the series.'
    # Both series begin at zero on the first selected date; growth after that date is compared.
    dates = [r['date'] for r in rows]
    available = analysis.market.benchmark
    values, previous = {}, None
    for day in sorted(set(available) | set(dates)):
        if day in available:
            previous = available[day]
        if day in dates:
            values[day] = previous
    if not values or any(values[d] is None for d in dates):
        return [], 'S&P 500 proxy prices are unavailable for this period.'
    # Provider adapter supplies only calendar-gap fill; missing sessions are not backfilled here.
    if any(d not in available for d in dates):
        return [], 'S&P 500 proxy coverage contains a gap.'
    base = float(values[dates[0]])
    growth, result = 1.0, []
    for index, row in enumerate(rows):
        if index:
            growth *= 1 + row['daily_return']
        result.append({'date': row['date'], 'Portfolio': (growth - 1) * 100,
                       'S&P 500 (SPY proxy)': (float(values[row['date']]) / base - 1) * 100})
    return result, 'Both lines start at 0% at the first selected close; subsequent daily returns are compounded.'

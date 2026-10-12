"""Independent return oracles and regression coverage for interleaved cash flows."""

import csv
from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from portfolio_compass.performance import xirr

ROOT = Path(__file__).resolve().parents[1]
START = date(2021, 1, 1)


def annual_flows(amounts):
    return [(START + timedelta(days=365 * i), amount) for i, amount in enumerate(amounts)]


@pytest.mark.parametrize('amounts,expected', [([-100, 110], .1), ([-100, 80], -.2),
                                            ([-100, 100], 0), ([-100, 20, -30, 150], None)])
def test_conventional_and_interleaved(amounts, expected):
    flows = annual_flows(amounts)
    rate, reason = xirr(flows)
    assert rate is not None, reason
    if expected is not None:
        assert rate == pytest.approx(expected, abs=1e-9)
    residual = sum(a / (1 + rate) ** i for i, a in enumerate(amounts))
    assert residual == pytest.approx(0, abs=1e-8)


@pytest.mark.parametrize('amounts', [[-100, 230, -132], [-100, 220.01, -121.011]])
def test_multiple_roots_including_close_pair(amounts):
    rate, reason = xirr(annual_flows(amounts))
    assert rate is None
    assert 'Multiple' in reason


def test_tangent_root():
    rate, reason = xirr(annual_flows([-100, 220, -121]))
    assert rate == pytest.approx(.1, abs=1e-7), reason


@pytest.mark.parametrize('flows', [[], annual_flows([-100, -10]), annual_flows([100, 10]),
                                   [(START, -100), (START, 110)], annual_flows([-100, 100, -100]),
                                   annual_flows([-100, float('nan')])])
def test_unavailable_cases(flows):
    assert xirr(flows)[0] is None


@pytest.mark.parametrize('filename', ['portfolio_compass_canonical_1.csv', 'portfolio_compass_canonical_2.csv'])
def test_canonical_csv_cashflows_with_independent_terminal_value(filename):
    """Use real uploaded cash flows, but a synthetic terminal value with a known 12% IRR."""
    path = ROOT / filename
    with path.open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    flows = []
    for row in rows:
        day = date.fromisoformat(row['date'])
        if row['transaction_type'] == 'DISTRIBUTION':
            amount = float(row['amount'])
        else:
            gross = float(row['quantity']) * float(row['price'])
            amount = (-gross if row['transaction_type'] == 'BUY' else gross) - float(row['fees'] or 0)
        flows.append((day, amount))
    end, expected = date(2026, 10, 9), .12
    terminal = -sum(amount * (1 + expected) ** ((end - day).days / 365) for day, amount in flows)
    assert terminal > 0
    rate, reason = xirr([*flows, (end, terminal)])
    assert rate == pytest.approx(expected, abs=1e-8), reason


def test_sample_dashboard_displays_xirr():
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
    sample = next(button for button in app.button if button.label == 'Explore sample portfolio')
    sample.click().run()
    assert not app.exception
    tile = next(metric for metric in app.metric if 'XIRR' in metric.label)
    assert tile.value != 'Unavailable'
    assert tile.value.endswith('%')
    overview_rate = tile.value
    app.radio(key='page').set_value('Performance').run()
    assert not app.exception
    performance_tile = next(metric for metric in app.metric if 'XIRR' in metric.label)
    assert performance_tile.value == overview_rate


def test_selected_period_xirr_includes_opening_value():
    from types import SimpleNamespace
    from portfolio_compass.performance import period_metrics
    opening, closing = date(2021, 12, 31), date(2022, 12, 31)
    analysis = SimpleNamespace(
        daily=[{'date': opening, 'value': 100},
               {'date': closing, 'value': 110, 'buy_flow': 0, 'sale_flow': 0, 'income_flow': 0}],
        conflicts=[], cashflows=[])
    metrics = period_metrics(analysis, date(2022, 1, 1), closing)
    assert metrics['xirr'] == pytest.approx(.1, abs=1e-9)
    assert metrics['profit'] == 10

from unittest.mock import Mock

from streamlit.testing.v1 import AppTest

from portfolio_compass.importing import suggest_mapping
from portfolio_compass.instruments import resolve_batch
from portfolio_compass.models import Instrument


def test_code_and_name_headers():
    assert suggest_mapping(['Code', 'date', 'type'])['instrument'] == 'Code'
    assert suggest_mapping(['Name', 'date', 'type'])['instrument'] == 'Name'


def test_codes_and_clear_names_are_automatic():
    pfe = Instrument('PFE', 'Pfizer')
    provider = Mock(return_value=([pfe], ''))
    resolved, ambiguous, errors = resolve_batch(['AAPL', 'Apple', 'PFE', 'Pfizer', 'PFE'], provider)
    assert {raw: item.symbol for raw, item in resolved.items()} == {'AAPL': 'AAPL', 'Apple': 'AAPL', 'PFE': 'PFE', 'Pfizer': 'PFE'}
    assert not ambiguous and not errors
    assert provider.call_count == 2


def test_ambiguous_names_grouped_and_invalid_codes_not_substituted():
    candidates = [Instrument('GOOG', 'Alphabet C'), Instrument('GOOGL', 'Alphabet A')]
    provider = Mock(return_value=(candidates, ''))
    resolved, ambiguous, errors = resolve_batch(['Alphabet', 'Wrong code', 'ZZZZ'], provider)
    assert not resolved
    assert set(ambiguous) == {'Alphabet', 'Wrong code'}
    assert 'ZZZZ' in errors


def test_share_class_code_punctuation():
    provider = Mock(return_value=([Instrument('BRK-B', 'Berkshire Hathaway B')], ''))
    resolved, ambiguous, errors = resolve_batch(['BRK.B'], provider)
    assert resolved['BRK.B'].symbol == 'BRK-B'
    assert not ambiguous and not errors


def validation_screen():
    from unittest.mock import patch
    import streamlit as st
    from portfolio_compass.models import Instrument
    from portfolio_compass.ui import validate_investments
    candidates = [Instrument('GOOG', 'Alphabet C'), Instrument('GOOGL', 'Alphabet A')]
    with patch('portfolio_compass.ui.lookup', return_value=(candidates, '')):
        resolved = validate_investments(['AAPL', 'Apple', 'Alphabet'], 'test')
    st.session_state['test_result'] = resolved


def test_single_button_only_ambiguous_name_needs_choice():
    app = AppTest.from_function(validation_screen).run()
    assert [b.label for b in app.button] == ['Validate inputs']
    assert not app.selectbox
    app.button[0].click().run()
    assert not app.exception
    assert len(app.selectbox) == 1
    assert 'Alphabet' in app.selectbox[0].label
    app.selectbox[0].select_index(1).run()
    result = app.session_state['test_result']
    assert result['AAPL'].symbol == 'AAPL'
    assert result['Alphabet'].symbol == 'GOOGL'
    assert len(app.button) == 1
    # Revalidation must clear stale manual choices and old portfolio results.
    app.session_state['analysis'] = 'old portfolio'
    app.button[0].click().run()
    assert app.selectbox[0].value is None
    assert app.session_state['test_result'] is None

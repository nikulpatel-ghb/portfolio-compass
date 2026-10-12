"""Streamlit presentation. Portfolio state stays inside the current session."""

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from .accounting import analyze
from .ai import GLOSSARY, answer, money
from .config import ai_available
from .importing import ALIASES, export_csv, normalize, read_csv, suggest_mapping
from .instruments import CATALOG, resolve_batch
from .market_data import demo_market, fetch_market, last_completed_session, lookup, session_dates
from .performance import comparison, period_metrics

ROOT = Path(__file__).resolve().parents[1]
PAGES = ['Get started', 'My portfolio', 'Performance', 'Ask Compass']


def clear_portfolio():
    for name in ['analysis', 'chat', 'last_symbol', 'summary', 'pending', 'transactions', 'resolved_instruments']:
        st.session_state.pop(name, None)


def reset():
    for name in list(st.session_state):
        del st.session_state[name]


def load_demo():
    clear_portfolio()
    headers, rows = read_csv((ROOT / 'Sample Data - Sheet1.csv').read_bytes())
    resolved = {item.symbol: item for item in CATALOG if item.symbol != 'SPY'}
    transactions, issues = normalize(rows, suggest_mapping(headers), resolved)
    market = demo_market()
    result = analyze(transactions, resolved, market, session_dates(transactions[0].day, market.as_of))
    result.issues.extend(issues)
    st.session_state.analysis = result
    st.session_state.page = 'My portfolio'


def theme():
    dark = st.session_state.get('theme') == 'Dark'
    bg, surface, text, muted, border = ('#0E1726', '#182538', '#F0F5FA', '#B9C9DB', '#35475D') if dark else ('#F5F7FA', '#FFFFFF', '#172B3A', '#53697B', '#DCE5EB')
    st.markdown(f'''<style>
    .stApp {{ background:{bg}; color:{text}; }}
    [data-testid="stSidebar"] {{ background:{surface}; border-right:1px solid {border}; }}
    [data-testid="stSidebar"] * {{ color:{text}; }}
    .block-container {{ max-width:1280px; padding-top:2rem; padding-bottom:3rem; }}
    h1,h2,h3,p,label {{ color:{text}; }}
    [data-testid="stMetric"] {{ background:{surface}; border:1px solid {border}; border-radius:16px; padding:20px; }}
    [data-testid="stMetricValue"] {{ color:{text}; font-weight:650; }}
    [data-testid="stCaptionContainer"] p {{ color:{muted}; }}
    [data-testid="stVerticalBlockBorderWrapper"] {{ border-radius:16px; }}
    .compass-brand {{ font-size:22px; font-weight:750; letter-spacing:-.6px; color:{text}; }}
    .compass-kicker {{ color:#25A8A0; letter-spacing:2px; font-size:12px; font-weight:700; margin-bottom:8px; }}
    .compass-hero {{ background:{surface}; border:1px solid {border}; border-radius:20px; padding:30px; margin-bottom:24px; }}
    .stButton button[kind="primary"] {{ background:#087F8C; border-color:#087F8C; color:white; }}
    @media(max-width:700px) {{ .block-container {{padding-left:1rem;padding-right:1rem;}} }}
    </style>''', unsafe_allow_html=True)  # Only application-authored constants; no user/model interpolation.
    return dark


def chart(fig, dark):
    fig.update_layout(template='plotly_dark' if dark else 'plotly_white', paper_bgcolor='rgba(0,0,0,0)',
                      plot_bgcolor='rgba(0,0,0,0)', margin=dict(l=12, r=12, t=25, b=16),
                      font=dict(family='Arial', size=13), legend=dict(orientation='h', y=-.2))
    st.plotly_chart(fig, width='stretch', config={'displaylogo': False})


def issues_view(issues):
    if not issues:
        return
    frame = pd.DataFrame([asdict(i) for i in issues])
    with st.expander(f'Data quality & assumptions · {len(issues)} notices'):
        st.dataframe(frame, hide_index=True, width='stretch')
        st.download_button('Download validation report', export_csv(frame.to_dict('records')), 'validation.csv', 'text/csv', key='issue-download')


def new_signature(signature):
    if st.session_state.get('import_signature') != signature:
        clear_portfolio()
        st.session_state.import_signature = signature


def get_started():
    st.markdown('<div class="compass-kicker">YOUR MONEY, MADE UNDERSTANDABLE</div>', unsafe_allow_html=True)
    st.title('A clearer view of your investments.')
    st.write('Turn a transaction spreadsheet into holdings, returns, and explanations you can follow.')
    left, right = st.columns([1, 1])
    with left, st.container(border=True):
        st.subheader('Take a guided first look')
        st.write('Explore Apple, Microsoft, and Tesla with a ready-to-use sample portfolio.')
        st.caption('Synthetic prices and distributions. Works offline. No account or API key needed.')
        st.button('Explore sample portfolio', type='primary', on_click=load_demo, width='stretch')
    with right, st.container(border=True):
        st.subheader('Bring your own history')
        st.write('Use tickers or common names. Codes and clear matches resolve automatically; only ambiguous names need your input.')
        st.download_button('Download CSV template', b'company,date,transaction_type,quantity,price,fees,cash_amount\nApple,2024-01-03,BUY,10,185.07,0,\n', 'portfolio-template.csv', 'text/csv', width='stretch')
        st.caption('USD · US investments · purchases, sales, distributions · complete history')
    st.divider()
    st.subheader('Upload your portfolio')
    st.caption('One CSV, up to 10 MB / 10,000 rows / 100 investments. Uploaded portfolios stay in this session.')
    upload = st.file_uploader('Transaction CSV', type=['csv'], key='upload')
    if upload is None:
        return
    data = upload.getvalue()
    fingerprint = hashlib.sha256(data).hexdigest()[:16]
    if st.session_state.get('upload_fingerprint') != fingerprint:
        clear_portfolio()
        st.session_state.upload_fingerprint = fingerprint
        st.session_state.lookup_results = {}
    with st.expander('File format', expanded=False):
        encoding = st.selectbox('Encoding', ['utf-8-sig', 'cp1252'])
        delimiter = st.selectbox('Delimiter', ['Auto', ',', ';', '\t', '|'])
        date_format = st.selectbox('Date format', ['ISO (YYYY-MM-DD)', 'US (MM/DD/YYYY)', 'Day first (DD/MM/YYYY)'])
        convention = st.selectbox('Number format', ['US', 'European'])
    try:
        headers, rows = read_csv(data, encoding, delimiter)
    except ValueError as exc:
        clear_portfolio()
        st.error(str(exc))
        return
    suggested = suggest_mapping(headers)
    st.markdown('**1 · Match your columns**')
    st.caption('Optional fields can remain unmapped. A name column can cross-check a separate ticker column.')
    mapping = {}
    columns = st.columns(3)
    for index, field in enumerate(ALIASES):
        options = ['— Not mapped —', *headers]
        default = options.index(suggested[field]) if suggested[field] else 0
        with columns[index % 3]:
            selected = st.selectbox(field.replace('_', ' ').title(), options, index=default, key=f'map-{fingerprint}-{field}')
            mapping[field] = '' if selected == options[0] else selected
    if any(not mapping[k] for k in ['instrument', 'date', 'type']):
        clear_portfolio()
        st.info('Map the investment, date and type fields to continue.')
        return
    raw_names = sorted({row[mapping[field]] for row in rows for field in ['instrument', 'name'] if mapping[field] and row[mapping[field]]})
    if len(raw_names) > 200:
        st.error('Use at most 200 unique raw names/tickers, resolving to at most 100 investments.')
        return
    context = hashlib.sha256(json.dumps([fingerprint, mapping, date_format, convention], sort_keys=True).encode()).hexdigest()
    if st.session_state.get('validation_context') != context:
        clear_portfolio()
        st.session_state.validation_context = context
        st.session_state.pop('identity_batch', None)
    resolved = validate_investments(raw_names, context)
    if resolved is None:
        return
    signature = hashlib.sha256(json.dumps([fingerprint, mapping, date_format, convention, {k: v.symbol for k, v in resolved.items()}], sort_keys=True).encode()).hexdigest()
    new_signature(signature)
    st.markdown('**3 · Review and analyze**')
    keep_duplicates = st.checkbox('Identical transaction rows are intentional (keep all of them)')
    transactions, issues = normalize(rows, mapping, resolved, date_format, convention, keep_duplicates)
    issues_view(issues)
    with st.expander('Preview normalized transactions', expanded=True):
        st.dataframe(pd.DataFrame([asdict(t) for t in transactions]).head(100).astype(str), hide_index=True, width='stretch')
    estimate = st.checkbox('Estimate missing distributions from market data', value=True)
    original_units = st.checkbox('Quantities and prices are original trade-date units (not retroactively split-adjusted).')
    confirmed = st.checkbox('I reviewed the mappings and warnings; this is my complete investment history.')
    errors = any(i.severity == 'error' for i in issues)
    if st.button('Analyze portfolio', type='primary', disabled=errors or not transactions or not original_units or not confirmed):
        clear_portfolio()
        instruments = {t.symbol: resolved[t.original] for t in transactions}
        try:
            with st.spinner('Fetching daily prices and reconciling your investment history…'):
                end = last_completed_session()
                market = fetch_market(list(instruments), transactions[0].day, end)
                if not estimate:
                    market.income_complete = False
                result = analyze(transactions, instruments, market, session_dates(transactions[0].day, end), estimate=estimate)
                result.issues.extend(issues)
            if result.conflicts:
                st.session_state.pending = (result, estimate)
            else:
                st.session_state.analysis = result
                st.success('Portfolio ready. Open My portfolio or Performance in the sidebar.')
        except ValueError as exc:
            st.error(str(exc))
    if 'pending' in st.session_state:
        income_review()


def validate_investments(raw_names, context):
    """Single validation action; only ambiguous names produce selection controls."""
    st.markdown('**2 · Validate investments**')
    st.caption('Validate all inputs together. Verified codes and clear names are accepted automatically.')
    if st.button('Validate inputs', type='primary'):
        clear_portfolio()
        for name in list(st.session_state):
            if name.startswith('ambiguous-'):
                del st.session_state[name]
        with st.spinner('Validating all investment codes and names…'):
            st.session_state.identity_batch = resolve_batch(raw_names, lookup)
    batch = st.session_state.get('identity_batch')
    if batch is None:
        st.info('Click Validate inputs once to check every investment in the file.')
        return None
    automatic, ambiguous, errors = batch
    resolved = dict(automatic)
    if ambiguous:
        st.warning(f'{len(ambiguous)} ambiguous name(s) need a selection. All are listed below.')
        for raw, candidates in ambiguous.items():
            labels = {f'{item.label} · {item.exchange} · {item.kind}': item for item in candidates}
            widget_key = 'ambiguous-' + hashlib.sha256((context + raw).encode()).hexdigest()[:16]
            selected = st.selectbox(f'Choose the investment for {raw}', list(labels), index=None, key=widget_key)
            if selected:
                resolved[raw] = labels[selected]
    for raw, message in errors.items():
        st.error(f'{raw}: {message}')
    if resolved:
        with st.expander(f'{len(resolved)} resolved inputs · view matches'):
            st.dataframe([{'Input': raw, 'Code': item.symbol, 'Investment': item.name,
                           'Status': 'Automatic' if raw in automatic else 'Selected'} for raw, item in resolved.items()],
                         hide_index=True, width='stretch')
    if errors or len(resolved) != len(raw_names):
        clear_portfolio()
        return None
    st.success('All investments resolved. No further identity confirmation is needed.')
    return resolved


def income_review():
    result, estimate = st.session_state.pending
    st.warning('Some uploaded distributions may overlap with online estimates. Resolve them before viewing results.')
    decisions = {}
    for conflict in result.conflicts:
        options = {'Separate income; keep provider estimates': 'independent'}
        options.update({f"Replace estimated event on {e['date']} ({money(float(e['amount']))})": e['id'] for e in conflict['candidates']})
        selection = st.selectbox(f"Row {conflict['row']} · {conflict['symbol']} · {conflict['date']} · {money(conflict['amount'])}", list(options), index=None, key=f"income-{conflict['id']}")
        if selection:
            decisions[conflict['id']] = options[selection]
    if st.button('Confirm income reconciliation', disabled=len(decisions) != len(result.conflicts)):
        try:
            result = analyze(result.transactions, result.instruments, result.market,
                             session_dates(result.transactions[0].day, result.market.as_of), decisions, estimate)
            st.session_state.analysis = result
            st.session_state.pop('pending')
            st.session_state.chat = []
            st.success('Income reconciled. Your portfolio is ready in the sidebar.')
        except ValueError as exc:
            st.error(str(exc))


def status(result):
    st.caption(f"{'SYNTHETIC DEMO' if result.market.demo else 'DAILY MARKET PRICES'} · USD · As of {result.market.as_of} · FIFO · Investment-only accounting")
    if not result.complete:
        st.warning('Valuation is incomplete. Priced subtotals exclude missing investments; complete portfolio metrics are unavailable.')
    if not result.market.income_complete:
        st.warning('Income coverage is incomplete. Results exclude unrecorded income; total-return benchmark comparison is unavailable.')


def overview(result, dark):
    st.title('Your portfolio, at a glance')
    status(result)
    end = result.daily[-1]
    metrics = period_metrics(result, result.daily[0]['date'], end['date'])
    cols = st.columns(4)
    cols[0].metric('Current investment value', money(end['value']), help='Value of the units still held. Brokerage cash is excluded.')
    cols[1].metric('Total profit / loss', money(end['profit']), help='Value + net sales + income − purchases including fees.')
    cols[2].metric('Annualized return · XIRR', f"{metrics['xirr']:.2%}" if metrics['xirr'] is not None else 'Unavailable', help=metrics['reason'])
    cols[3].metric('Income · recorded + estimated', money(end['income']), help=GLOSSARY['income'])
    if metrics['xirr'] is None:
        st.caption('XIRR: ' + metrics['reason'])
    left, right = st.columns([1.65, 1])
    with left, st.container(border=True):
        st.subheader('Your investment journey')
        frame = pd.DataFrame(result.daily)
        fig = px.line(frame, x='date', y='value', color_discrete_sequence=['#159B93'], labels={'date': '', 'value': 'Investment value (USD)'})
        fig.update_traces(connectgaps=False, fill='tozeroy', fillcolor='rgba(21,155,147,.08)')
        chart(fig, dark)
    with right, st.container(border=True):
        st.subheader('Where your money is')
        dimension = st.selectbox('Allocation view', ['Investment', 'Asset type', 'Sector'])
        active = pd.DataFrame([r for r in result.holdings if r['quantity'] > 0 and r['value'] is not None])
        if result.complete and not active.empty and active['value'].sum() > 0:
            field = {'Investment': 'investment', 'Asset type': 'type', 'Sector': 'sector'}[dimension]
            fig = px.pie(active, values='value', names=field, hole=.67,
                         color_discrete_sequence=['#159B93', '#407FB8', '#E5A54B', '#9B8ACB', '#D7767C'])
            chart(fig, dark)
            largest = active.loc[active['value'].idxmax()]
            st.caption(f"Largest holding: {largest['investment']} · {largest['value'] / end['value']:.1%}")
        else:
            st.info('Allocation requires complete prices and positive remaining holdings.')
    st.subheader('Holdings & gains')
    show_closed = st.checkbox('Include fully sold investments', value=True)
    frame = pd.DataFrame([r for r in result.holdings if show_closed or r['quantity'] > 0])
    st.dataframe(frame, hide_index=True, width='stretch', column_config={k: st.column_config.NumberColumn(k.replace('_', ' ').title(), format='$%.2f') for k in ['value', 'cost', 'price', 'realized', 'unrealized', 'income', 'profit']})
    st.download_button('Download holdings', export_csv(frame.to_dict('records')), 'holdings.csv', 'text/csv')
    with st.container(border=True):
        st.subheader('Compass brief')
        st.caption('Application-verified facts. Enable Groq on Ask Compass to let AI select the most relevant evidence.')
        if st.button('Generate portfolio summary'):
            st.session_state.summary = answer('Summarize my portfolio', result, st.session_state.get('ai_consent', False))
        brief = st.session_state.get('summary') or answer('Summarize my portfolio', result)
        st.text(brief['text'])
        st.caption(brief['method'])
    issues_view(result.issues)


def performance_view(result, dark):
    st.title('See the whole journey')
    status(result)
    selection = st.date_input('Reporting period', value=(result.daily[0]['date'], result.market.as_of), min_value=result.daily[0]['date'], max_value=result.market.as_of)
    if len(selection) != 2:
        st.info('Select a start and end date.')
        return
    start, end = selection
    metrics = period_metrics(result, start, end)
    cols = st.columns(4)
    for column, label, value in zip(cols, ['Purchases', 'Net sale proceeds', 'Income', 'Period profit'], [metrics.get('purchases'), metrics.get('sales'), metrics.get('income'), metrics['profit']]):
        column.metric(label, money(value))
    rate = metrics['xirr']
    st.metric('Annualized return · XIRR', f'{rate:.2%}' if rate is not None else 'Unavailable',
              help='Return for the selected reporting period, using dated cash flows and opening/closing investment values. ' + metrics['reason'])
    st.caption(metrics['reason'])
    st.subheader('Performance, on the same scale')
    benchmark = st.checkbox('Compare with S&P 500 (SPY proxy)', value=False)
    series, reason = comparison(result, start, end)
    if series:
        frame = pd.DataFrame(series)
        cols = ['Portfolio', 'S&P 500 (SPY proxy)'] if benchmark else ['Portfolio']
        chart(px.line(frame, x='date', y=cols, color_discrete_sequence=['#159B93', '#E5A54B'], labels={'date': '', 'value': 'Cumulative return (%)', 'variable': ''}), dark)
    else:
        st.info(reason)
    st.caption('Daily approximation: purchases at the start of the day, sales and income at the end. Empty-capital periods and missing data break comparisons.')
    if series:
        st.caption(reason)
    st.subheader('Value and cumulative money flows')
    history = pd.DataFrame(metrics['rows'])
    chosen = st.multiselect('Show series', ['value', 'purchases', 'sale_proceeds', 'income', 'profit'], default=['value', 'purchases', 'sale_proceeds'])
    if chosen:
        chart(px.line(history, x='date', y=chosen, labels={'date': '', 'value': 'USD', 'variable': ''}), dark)
    st.caption('Cumulative flow series run from the first transaction; period cards above use only the selected dates.')
    with st.expander('Accessible chart data & downloads'):
        st.dataframe(history, hide_index=True, width='stretch')
        st.download_button('Download performance', export_csv(metrics['rows']), 'performance.csv', 'text/csv')
        st.download_button('Download normalized transactions', export_csv([asdict(t) for t in result.transactions]), 'transactions.csv', 'text/csv')
    with st.expander('Income ledger · recorded and estimated'):
        records = [{**r, 'amount': float(r['amount']), 'units': float(r['units']) if r['units'] is not None else None} for r in result.income]
        st.dataframe(pd.DataFrame(records), hide_index=True, width='stretch')
        st.download_button('Download income ledger', export_csv(records), 'income.csv', 'text/csv')
    with st.expander('How these numbers work'):
        for name, definition in GLOSSARY.items():
            if name != 'upload':
                st.write(f'**{name.upper()}**')
                st.write(definition)


def chat_view(result):
    st.title('Ask Compass')
    st.write('Explore your holdings and performance in plain language.')
    status(result)
    st.info('Scope: your uploaded investments and the S&P 500 comparison only. No trade recommendations, forecasts, or unrelated answers.')
    available = ai_available()
    st.checkbox('Enable Groq: send only relevant portfolio facts to the AI service', key='ai_consent', disabled=not available)
    st.caption('No raw CSV or API key is sent in prompts. Responses display verified fact templates only. ' + ('Groq is configured.' if available else 'No Groq key configured. Local factual answers are available.'))
    suggestions = ['Summarize my portfolio', 'Which holding contributed the most profit?', 'How concentrated is my portfolio?', 'Compare my return with the S&P 500', 'Explain XIRR']
    suggested = st.selectbox('Try a question', ['Choose a question…', *suggestions])
    question = None
    if st.button('Ask selected question', disabled=suggested == 'Choose a question…'):
        question = suggested
    history = st.session_state.setdefault('chat', [])
    for entry in history:
        with st.chat_message(entry['role']):
            st.text(entry['text'])
            if entry.get('method'):
                st.caption(entry['method'])
    typed = st.chat_input('Ask about an investment, a metric, or your portfolio', max_chars=1000)
    question = typed or question
    if question:
        now = time.monotonic()
        recent = [t for t in st.session_state.get('chat_times', []) if now - t < 60]
        if len(recent) >= 10:
            st.warning('Please wait a minute before asking more questions.')
            return
        st.session_state.chat_times = [*recent, now]
        response = answer(question, result, bool(st.session_state.get('ai_consent')) and available, st.session_state.get('last_symbol'))
        st.session_state.last_symbol = response['symbol']
        history.extend([{'role': 'user', 'text': question}, {'role': 'assistant', **response}])
        st.session_state.chat = history[-20:]
        st.rerun()
    if history and history[-1].get('evidence'):
        with st.expander('Evidence behind the latest answer'):
            st.json(history[-1]['evidence'])
    st.button('Clear this conversation', on_click=lambda: [st.session_state.pop(k, None) for k in ['chat', 'last_symbol']])


def main():
    st.set_page_config(page_title='Portfolio Compass', page_icon='🧭', layout='wide')
    with st.sidebar:
        st.markdown('<div class="compass-brand">◈ Portfolio Compass</div>', unsafe_allow_html=True)
        st.caption('Understand your investments.')
        st.divider()
        page = st.radio('Navigate', PAGES, key='page', label_visibility='collapsed')
        st.divider()
        st.selectbox('Appearance', ['Light', 'Dark'], key='theme')
        st.caption('USD · US markets · FIFO')
        st.button('Reset portfolio & session', on_click=reset, width='stretch')
        st.caption('Your uploads are not saved to a database. Re-upload after a new session.')
    dark = theme()
    result = st.session_state.get('analysis')
    if page == 'Get started':
        get_started()
    elif result is None:
        st.title('Your portfolio starts here')
        st.info('Upload your transactions on Get started, or explore the sample.')
        st.button('Explore sample portfolio', type='primary', on_click=load_demo)
    elif page == 'My portfolio':
        overview(result, dark)
    elif page == 'Performance':
        performance_view(result, dark)
    else:
        chat_view(result)
    st.divider()
    st.caption('Portfolio Compass · Clear numbers. Visible assumptions. · Educational portfolio analytics, not trading recommendations.')

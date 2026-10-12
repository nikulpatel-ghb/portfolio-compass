"""Secret values are read locally, never rendered or logged."""

import os


def setting(name, default=''):
    value = os.environ.get(name)
    if value is not None:
        return value
    try:
        import streamlit as st
        return str(st.secrets.get(name, default))
    except (FileNotFoundError, KeyError):
        return default


def ai_available():
    return setting('COMPASS_AI_ENABLED', 'true').lower() == 'true' and bool(setting('GROQ_API_KEY'))

"""Collect the active implementation's tests, excluding historical V02 snapshots.

The V02 files target a separate, unwired fulltext report implementation and
duplicate API fixtures against the same module globals. Keep them for reference.
"""
import os

collect_ignore_glob = ['*_V02.py']

# Keep local deployment choices out of deterministic, isolated model tests.
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['RAG_QA_VERIFICATION_MODE'] = 'independent'
os.environ['RAG_QA_HEDGE_DELAY_SECONDS'] = '0'
os.environ['RAG_MIN_SIMILARITY'] = '.3'

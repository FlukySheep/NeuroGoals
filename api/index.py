"""Vercel entry point: every route is rewritten here (see vercel.json)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neurofinance.web import app  # noqa: E402,F401

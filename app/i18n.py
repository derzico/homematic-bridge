# SPDX-License-Identifier: Apache-2.0

"""Flask-Babel-Setup mit Cookie-basierter Sprachauswahl und Auto-Compile.

Sprachauswahl-Reihenfolge:
1. Cookie ``lang`` (gesetzt durch ``GET /lang/<code>``)
2. ``Accept-Language``-Header des Browsers
3. Default ``LANGUAGES[0]`` (de)

Damit kein separater ``pybabel compile``-Schritt nötig ist, werden
beim App-Start alle ``translations/*/LC_MESSAGES/messages.po`` in
``messages.mo`` kompiliert (nur wenn die ``.po`` neuer ist).
"""

from __future__ import annotations

import logging
import os
from typing import List

from flask import Flask, request
from flask_babel import Babel

LANGUAGES: List[str] = ["de", "en"]
COOKIE_NAME = "lang"

log = logging.getLogger("bridge-ws")
babel = Babel()


def _select_locale() -> str:
    """Bestimmt die aktive Sprache pro Request."""
    cookie_lang = request.cookies.get(COOKIE_NAME)
    if cookie_lang in LANGUAGES:
        return cookie_lang
    return request.accept_languages.best_match(LANGUAGES) or LANGUAGES[0]


def _compile_translations(translations_dir: str) -> None:
    """Kompiliert .po → .mo (nur wenn .po neuer ist)."""
    try:
        from babel.messages.mofile import write_mo
        from babel.messages.pofile import read_po
    except ImportError:
        log.warning("Babel nicht installiert – Übersetzungen werden nicht kompiliert.")
        return

    if not os.path.isdir(translations_dir):
        return

    for lang in os.listdir(translations_dir):
        po_path = os.path.join(translations_dir, lang, "LC_MESSAGES", "messages.po")
        mo_path = os.path.join(translations_dir, lang, "LC_MESSAGES", "messages.mo")
        if not os.path.isfile(po_path):
            continue
        if os.path.isfile(mo_path) and os.path.getmtime(mo_path) >= os.path.getmtime(po_path):
            continue
        try:
            with open(po_path, "rb") as f:
                catalog = read_po(f, locale=lang)
            with open(mo_path, "wb") as f:
                write_mo(f, catalog)
            log.info("Übersetzungen kompiliert: %s", mo_path)
        except Exception:
            log.exception("Fehler beim Kompilieren von %s", po_path)


def init_app(app: Flask) -> None:
    """Initialisiert Flask-Babel an der übergebenen App."""
    # Flask resolves a relative ``BABEL_TRANSLATION_DIRECTORIES`` against
    # ``app.root_path``. Setting an absolute path keeps it robust regardless
    # of where the Flask app object was created.
    translations_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "translations")
    )
    app.config.setdefault("BABEL_DEFAULT_LOCALE", LANGUAGES[0])
    app.config.setdefault("BABEL_TRANSLATION_DIRECTORIES", translations_dir)
    babel.init_app(app, locale_selector=_select_locale)

    _compile_translations(translations_dir)

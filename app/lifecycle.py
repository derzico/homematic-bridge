# SPDX-License-Identifier: Apache-2.0
# lifecycle.py – Kontrollierter Prozess-Shutdown

import logging
import signal
from typing import Callable

import app.state as state

log = logging.getLogger("bridge-ws")


def install_shutdown_handlers(registry) -> Callable:
    """Installiert SIGINT/SIGTERM-Handler und gibt den Handler für Tests zurück."""
    def _shutdown(signum, _frame) -> None:
        log.info("Shutdown-Signal %s empfangen", signum)
        state.stop_event.set()
        if state.runtime_services is not None:
            state.runtime_services.stop()
        registry.stop_all()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    return _shutdown

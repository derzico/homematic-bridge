# SPDX-License-Identifier: Apache-2.0
"""Schutz vor Funküberlastung für HmIP-Steuerbefehle.

- Doppelte Befehle (gleiches Ziel, gleicher Befehl, gleiche Werte) innerhalb von
  dedupe_window_ms werden verworfen und als erledigt gemeldet.
- Pro Ziel (Gerät/Gruppe) liegt mindestens min_interval_ms zwischen zwei Befehlen.
  Kommt während der Wartezeit ein neuerer Befehl desselben Typs für dasselbe Ziel,
  gewinnt der neuere; der ältere wird als "superseded" beendet (z.B. Dimm-Rampen).
- Ab duty_cycle_limit_percent (Home-Duty-Cycle der HCU, Prozent) werden nur noch
  wichtige Befehle (Alarm/Sirene) gesendet, alle anderen mit 429 abgelehnt.

Keine Netzwerkzugriffe hier; Zeitquelle und Schlafen sind für Tests austauschbar.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger("bridge-ws")

PRIORITY_PATHS = frozenset({"/hmip/group/switching/setState"})  # ALARM_SWITCHING (Sirene)


@dataclass(frozen=True)
class GuardSettings:
    dedupe_window_ms: int = 500
    min_interval_ms: int = 150
    duty_cycle_limit_percent: int = 80  # 0 = aus

    @classmethod
    def from_config(cls, cfg: Any) -> "GuardSettings":
        cfg = cfg if isinstance(cfg, dict) else {}
        return cls(
            dedupe_window_ms=int(cfg.get("dedupe_window_ms", cls.dedupe_window_ms)),
            min_interval_ms=int(cfg.get("min_interval_ms", cls.min_interval_ms)),
            duty_cycle_limit_percent=int(cfg.get("duty_cycle_limit_percent", cls.duty_cycle_limit_percent)),
        )


@dataclass(frozen=True)
class Decision:
    """send=False: nicht senden; outcome = "duplicate" | "superseded" | "duty_cycle"."""
    send: bool
    outcome: str = "send"
    reference: Optional[str] = None  # request_id des Originals bei duplicate


class CommandGuard:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_sent: Dict[str, float] = {}                         # target -> Zeitpunkt
        self._recent: Dict[Tuple[str, str], Tuple[float, str]] = {}   # (target, sig) -> (t, rid)
        self._latest_seq: Dict[Tuple[str, str, str], int] = {}        # (target, path, kanal) -> seq
        self._seq = 0

    @staticmethod
    def signature(path: str, args: Tuple[Any, ...]) -> str:
        return path + "|" + json.dumps(args, sort_keys=True, default=str)

    def admit(self, settings: GuardSettings, target: str, path: str, args: Tuple[Any, ...],
              duty_cycle: Optional[float]) -> Decision:
        """Entscheidet über einen Befehl; wartet ggf. das Mindestintervall ab."""
        if (settings.duty_cycle_limit_percent and duty_cycle is not None
                and duty_cycle >= settings.duty_cycle_limit_percent and path not in PRIORITY_PATHS):
            log.warning("Duty Cycle %.1f%% ≥ %s%%: Befehl %s für %s abgelehnt",
                        duty_cycle, settings.duty_cycle_limit_percent, path, target)
            return Decision(False, "duty_cycle")
        sig = self.signature(path, args)
        with self._lock:
            now = self._clock()
            self._prune(now, settings)
            recent = self._recent.get((target, sig))
            if recent and (now - recent[0]) * 1000 < settings.dedupe_window_ms:
                return Decision(False, "duplicate", recent[1])
            self._seq += 1
            seq = self._seq
            slot = (target, path, channel_of(path, args))
            self._latest_seq[slot] = seq
            wait = 0.0
            last = self._last_sent.get(target)
            if last is not None:
                wait = max(0.0, settings.min_interval_ms / 1000 - (now - last))
            # Slot reservieren, damit parallele Befehle sich hinten anstellen
            self._last_sent[target] = now + wait
        if wait > 0:
            self._sleep(wait)
        with self._lock:
            if self._latest_seq.get(slot) != seq:
                return Decision(False, "superseded")
        return Decision(True)

    def record(self, target: str, path: str, args: Tuple[Any, ...], request_id: Optional[str]) -> None:
        """Nach erfolgreichem Senden: für Duplikaterkennung merken."""
        with self._lock:
            self._recent[(target, self.signature(path, args))] = (self._clock(), request_id or "")

    def _prune(self, now: float, settings: GuardSettings) -> None:
        horizon = max(settings.dedupe_window_ms, settings.min_interval_ms, 1000) / 1000 * 10
        for key in [k for k, (t, _) in self._recent.items() if now - t > horizon]:
            del self._recent[key]


guard = CommandGuard()


def target_of(args: Tuple[Any, ...]) -> str:
    """Ziel-ID aus den Argumenten von _send_hmip_command (Legacy: ID zuerst; generisch: Payload)."""
    for value in args:
        if isinstance(value, dict):
            return str(value.get("deviceId") or value.get("groupId") or "")
    first = next((a for a in args if isinstance(a, str) and not a.startswith("/")), "")
    return first


def channel_of(path: str, args: Tuple[Any, ...]) -> str:
    """Kanal für "neuester gewinnt": Payload-channelIndex oder letztes int-Argument (Legacy)."""
    for value in args:
        if isinstance(value, dict):
            return str(value.get("channelIndex", ""))
    if args and not is_group_command(path) and isinstance(args[-1], int) and not isinstance(args[-1], bool):
        return str(args[-1])
    return ""


def is_group_command(path: str) -> bool:
    return path.startswith("/hmip/group/")


def home_duty_cycle(snapshot: Any) -> Optional[float]:
    """Home-Duty-Cycle der HCU in Prozent (z.B. 7.0) oder None."""
    from app.controls import system_body
    home = system_body(snapshot).get("home")
    value = home.get("dutyCycle") if isinstance(home, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None

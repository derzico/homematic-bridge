import configparser
from pathlib import Path
import shlex

ROOT = Path(__file__).parents[1]


def unit(name):
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(ROOT / "ops/systemd" / name)
    return parser


def test_backup_timer_daily_explicit_timezone_persistent():
    timer = unit("homematic-bridge-backup.timer")
    assert timer["Timer"]["OnCalendar"] == "*-*-* 03:15:00 Europe/Berlin"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Timer"]["Unit"] == "homematic-bridge-backup.service"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_backup_service_keeps_fourteen_and_never_controls_devices():
    service = unit("homematic-bridge-backup.service")["Service"]
    command = shlex.split(service["ExecStart"])
    assert command[:3] == ["/usr/bin/python3", "/root/homematic-bridge/ops/backup.py", "create"]
    assert command[command.index("--keep") + 1] == "14"
    assert command[command.index("--destination") + 1] == service["ReadWritePaths"]
    assert service["UMask"] == "0077"
    assert service["NoNewPrivileges"] == "true"
    assert service["ProtectSystem"] == "strict"
    assert service["ProtectHome"] == "read-only"
    assert service["RestrictAddressFamilies"] == "AF_UNIX"
    assert not any(key.startswith("ExecStartPost") for key in service)

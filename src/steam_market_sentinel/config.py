"""Persistent non-secret settings. Secrets are delegated to OS keyring."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path


def data_directory() -> Path:
    forced = os.environ.get("SMS_DATA_DIR")
    if forced:
        path = Path(forced).expanduser()
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA", str(Path.home()))) / "SteamMarketSentinel"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "steam-market-sentinel"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    currency: str = "CNY"
    interval_seconds: int = 900
    request_gap_seconds: int = 6
    auto_start: bool = True
    minimize_to_tray: bool = True
    desktop_enabled: bool = True
    # Notify whenever a poll observes a price below the previous local sample. This
    # is independent of the observed-low baseline, so an imported official history
    # (which can lower that baseline out of reach) cannot switch it off.
    drop_alert_enabled: bool = False
    email_enabled: bool = False
    email_sender: str = ""
    email_receiver: str = ""
    onebot_enabled: bool = False
    onebot_url: str = "http://127.0.0.1:3000"
    onebot_user_id: str = ""
    # View state, so reopening lands on the item the user was looking at. Without
    # this the app always came back on the first row, which made an imported
    # official series look as though it had not been saved.
    selected_item: int | None = None

    @classmethod
    def load(cls, path: Path) -> "Settings":
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            accepted = {field.name for field in fields(cls)}
            values = {key: val for key, val in payload.items() if key in accepted}
            config = cls(**values)
            if config.currency not in {"CNY", "USD", "EUR"}:
                config.currency = "CNY"
            config.interval_seconds = max(60, min(86400, int(config.interval_seconds)))
            config.request_gap_seconds = max(3, min(120, int(config.request_gap_seconds)))
            # Tolerate hand-edited or older files without discarding the rest.
            config.selected_item = (config.selected_item
                                    if isinstance(config.selected_item, int) else None)
            return config
        except (ValueError, TypeError, OSError, AttributeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

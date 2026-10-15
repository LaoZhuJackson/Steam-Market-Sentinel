"""Persistent non-secret settings. Secrets are delegated to OS keyring."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path

# Chart time ranges offered in the UI: label shown to the user -> days kept (0 = all).
CHART_RANGES = (
    ("全部", 0),
    ("最近 7 天", 7),
    ("最近 30 天", 30),
    ("最近 90 天", 90),
    ("最近 1 年", 365),
)
_CHART_RANGE_DAYS = {days for _label, days in CHART_RANGES}

# How this process reaches Steam. "system" leaves requests' own behaviour alone (an
# enabled Windows proxy first, then HTTP(S)_PROXY); "direct" ignores both. This is a
# setting rather than automatic detection because a browser being able to open the
# market says nothing about a Python process: game accelerators frequently cover only
# the browser or the game, and `requests` cannot use a PAC script at all — so the
# user has to be able to point the app at a local proxy port themselves.
PROXY_MODES = (
    ("system", "使用系统代理 / 环境变量"),
    ("direct", "直连（不使用代理）"),
    ("custom", "手动指定代理"),
)
_PROXY_MODE_VALUES = {mode for mode, _label in PROXY_MODES}
_PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://", "socks4://", "socks4a://")


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
    # Notify when an item starts failing (usually the network cannot reach Steam)
    # and again when it recovers. Transition-based, so an outage alerts once.
    error_alert_enabled: bool = True
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
    # How much of the price history the chart shows. Narrow ranges keep recent small
    # moves readable when an old spike would otherwise flatten the whole curve.
    chart_range_days: int = 0
    # Network route to Steam. See PROXY_MODES: a blocked direct route is the most
    # common cause of "监控异常" here, and the app cannot detect it for the user.
    proxy_mode: str = "system"
    proxy_url: str = ""

    def proxy_argument(self) -> str:
        """The route to hand to the HTTP layer: ``""`` (system/env), ``direct``, or a URL.

        A "custom" mode with nothing typed in it falls back to the system setting
        rather than to a broken proxy.
        """
        if self.proxy_mode == "direct":
            return "direct"
        if self.proxy_mode == "custom" and self.proxy_url.strip():
            return self.proxy_url.strip()
        return ""

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
            config.chart_range_days = (config.chart_range_days
                                       if config.chart_range_days in _CHART_RANGE_DAYS else 0)
            if config.proxy_mode not in _PROXY_MODE_VALUES:
                config.proxy_mode = "system"
            config.proxy_url = str(config.proxy_url or "").strip()
            if config.proxy_url and not config.proxy_url.lower().startswith(_PROXY_SCHEMES):
                config.proxy_url = ""  # an unusable address must not break every request
            return config
        except (ValueError, TypeError, OSError, AttributeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

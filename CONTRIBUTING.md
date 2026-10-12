# Contributing / 贡献指南

Issues and PRs welcome. Please keep every network action off the GUI thread,
write unit tests for parsers and alert conditions, and avoid requesting Steam
passwords/session tokens. Public market endpoints are undocumented; preserve
graceful failures and conservative rate limits.

欢迎提 Issue / PR：不要在 GUI 主线程执行网络请求；金额和告警规则应有单元测试；
不要加入 Steam 密码或 Cookie 采集；新增请求要考虑限流与失败重试。

## Local checks / 本地检查

```bash
pip install -e '.[dev]'
pytest -q
ruff check src tests
```

`pytest` resolves the package through `pythonpath = ["src"]` in `pyproject.toml`
(there is no `conftest.py`). The Qt tests need a display; use
`QT_QPA_PLATFORM=offscreen` on a headless machine (CI does this).

## Project layout / 项目结构

```text
steam-market-sentinel/
├── src/steam_market_sentinel/
│   ├── models.py       # 金额解析、URL/实体
│   ├── steam_api.py    # Steam 搜索/报价/官方成交历史
│   ├── database.py     # SQLite、趋势、规则去重、导出
│   ├── monitor.py      # QThread 批量轮询
│   ├── alerts.py       # QQ SMTP / OneBot 和凭据库
│   ├── ui.py           # PySide6 GUI、图表与图片
│   ├── config.py       # 非敏感设置
│   ├── style.py        # GUI 主题与调色板
│   └── main.py         # 入口
├── tests/              # 核心逻辑单元测试
├── scripts/            # Windows 构建脚本
└── .github/workflows/  # CI 与 Windows 打包
```

## UI and theme / 界面与主题

- Every colour must come from `style.PALETTE` — `style.STYLE` is a
  `string.Template` whose `$name` placeholders are substituted from it. Write
  `background: $panel;`, **not** `#$panel`: the palette values already include
  the leading `#`, and Qt **silently ignores** malformed declarations, so a
  doubled `#` disables the whole theme with no error at all.
  `tests/test_ui.py::test_stylesheet_colours_are_valid` guards this.
- Do **not** add a universal `QWidget { background: ... }` rule. A Qt type
  selector also matches subclasses, so it paints an opaque rectangle behind every
  `QLabel`/`QCheckBox` inside the lighter panels. Plain containers are kept dark
  by the `QPalette` set in `style.apply_theme()` instead.
- Reuse the existing object names (`#hero`, `#section`, `#subtle`,
  `#metricCaption`, `#metricValue`, `#badge`, `#hint`, `#thumb`, `#panel`,
  `#card`, `#stat`, `#hline`, `#primary`, `#danger`) rather than adding inline
  `setStyleSheet` calls, which detach a widget from the shared palette.
- `style.apply_theme(app)` must set the style *before* the palette (setting the
  style replaces the application palette) and the stylesheet last.

## Packaging / 打包与发布

Run on **Windows** — a Windows executable must be built on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
# single portable exe:
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 -OneFile
# or point it at a specific interpreter (e.g. a conda env):
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 -Python "C:\...\python.exe"
```

The script checks the interpreter is 3.10+, installs dev dependencies, runs the unit
tests, then packages with PyInstaller.

- Default is `--onedir` → `dist/SteamMarketSentinel/SteamMarketSentinel.exe`. `-OneFile`
  → `dist/SteamMarketSentinel.exe`, a single portable exe that unpacks to `%TEMP%` on
  every launch (slower first window, more antivirus noise).
- Every step asserts its **exit code**: `$ErrorActionPreference` does not cover
  external programs, so without that a failing `pytest` would silently carry on into
  packaging and still report success.
- `--collect-submodules keyring.backends` is required: it makes the OS keyring backends
  discoverable so notification secrets can be stored at all.
- Smoke-test the build without touching real data:
  `$env:SMS_DATA_DIR = "<repo>\.build-test"; .\dist\SteamMarketSentinel\SteamMarketSentinel.exe`
- Distribute the whole `dist/SteamMarketSentinel/` folder (zipped), or the single exe
  when built with `-OneFile`.
- The `.github/workflows/windows-build.yml` workflow builds the same thing on
  `windows-latest` and uploads it as an artifact; it can be triggered manually
  or by pushing a `v*` tag.
- Builds are unsigned, so the first run may show a SmartScreen warning.

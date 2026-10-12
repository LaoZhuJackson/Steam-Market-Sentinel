# Steam Market Sentinel

**跨游戏 Steam 社区市场价格哨兵 | Lightweight Steam Community Market price watcher**

[English](README_EN.md) · [更新日志](CHANGELOG.md) · [参与贡献](CONTRIBUTING.md) · [安全说明](SECURITY.md)

> 本地优先的 Windows 桌面工具：从 Steam 市场搜索物品，批量监控最低挂单价，查看价格趋势，并在达到目标价或出现**本地观测新低**时发出提醒。支持 CS2、Dota 2、TF2、Rust 等所有有社区市场商品的 AppID。
>
> 当前为 **v0.1.0 项目源码**；未发布经真实 Steam 网络环境验证的 Windows 安装包。项目内包含自动构建配置。

## 功能

- **简洁 PySide6 GUI**：深色界面、可筛选监控表、商品图片、可视化趋势、系统托盘常驻。
- **通用商品搜索**：跨游戏搜索（AppID=0）或按 AppID 搜索；也能直接添加精确 `market_hash_name` 或官方市场商品链接。
- **批量监控**：逐个轮询、可设置检查周期和逐商品请求间隔；遇 429/网络错误退避重试，不会让某一件失败导致全队列中断。
- **价格趋势**：本地 SQLite 记录最低在售挂单价；变价即记录，未变价每 6 小时保留一次采样，减少数据量。
- **双重提醒**：价格 ≤ 目标价格时触发一次，离开阈值范围后再次进入才重新提醒；价格严格低于此前观测低点时提醒（第一笔作为基准不提醒）。
- **多种通知**：Windows/Qt 系统托盘、QQ 邮箱 SMTP、OneBot v11 HTTP 私聊 API（可配合适配服务）；可以同时启用。
- **数据导出**：监控清单 JSON 导入/导出、所选币种历史数据 UTF-8 BOM CSV 导出。
- **可切换报价币种**：CNY / USD / EUR，各币种样本隔离，*切换后目标价阈值不会自动换算*，请手动检查。
- **Steam 成交历史按需读取**：若 Steam 允许匿名访问，则图表临时展示官方**成交中位价**，不保存至数据库；无法访问时提示在浏览器登录查看。
- **本地密钥保管**：QQ 邮箱授权码、OneBot 访问令牌通过操作系统凭据库（`keyring`）保存，不写入 `settings.json`。
- **Windows 打包**：PowerShell PyInstaller 一键打包及 GitHub Actions Windows 构建。

## 快速开始

**前提**：Python 3.10+、Windows 10/11 推荐（Linux/macOS 理论上可运行，有些托盘/通知依赖桌面环境）。

```powershell
git clone https://github.com/YOUR_USERNAME/steam-market-sentinel.git
cd steam-market-sentinel
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

如 PowerShell 禁止执行 `.ps1`，也可以省去激活步骤：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Linux/macOS：`python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python main.py`。

启动后：点击 **＋ 添加商品** → 输入关键词并按 AppID 筛选（例如 CS2 `730`、Dota 2 `570`、TF2 `440`；`0` 搜索所有游戏）→ 选中结果 → 可选填目标价 → 添加。点击商品右侧可以调整目标价、观测新低提醒、暂停或删除。添加后程序会在监控循环中抓取价格并展示图片和本地曲线。无需 Steam 用户名、密码或 API Key。

## 设置建议

| 选项 | 默认 | 说明 |
|---|---|---|
| 币种 | CNY | 查询指定货币的最低在售挂牌价，币种分开保存 |
| 轮询间隔 | 15 分钟 | 每轮遍历所有启用物品，过短可能被限流 |
| 单品请求间隔 | 6 秒 | 防止对 Steam 产生过高请求频率 |
| 启动时自动监控 | 开 | 自动在后台线程工作 |
| 关闭时最小化到托盘 | 开 | 从托盘菜单“退出程序”才能结束进程 |
| 桌面提醒 | 开 | 需桌面系统支持托盘通知 |
| 邮箱 / 机器人 | 关 | 需要自行配置授权码或机器人服务 |

**批量监控不等于实时行情流**：例如监控 100 件、每件间隔 6 秒，一轮请求本身至少约 10 分钟，加上网络/限流时间；轮询间隔是**轮与轮之间的等待**，不是每件商品都能在该间隔内刷新。Steam 非公开文档化接口，可能随时限流或改变返回格式。

## QQ 邮箱通知

1. 登录自己的 QQ 邮箱网页版，在邮箱设置中找到并开启 SMTP 服务，获取**SMTP 授权码**（不是 QQ 账号密码）。
2. 打开应用设置 → **QQ 邮箱**，填写发件邮箱（`xxx@qq.com`）、收件邮箱（留空则发给自己）和授权码。
3. 点击“保存授权码并发送测试邮件”；成功后开启“启用 QQ 邮箱提醒”，保存设置。
4. 在 SMTP 465 SSL 端口不可达的网络环境中邮件可能发送失败，程序会在状态栏报告错误，不会在 UI 中记录密钥。

**权限说明**：`keyring` 在 Windows 使用 Windows Credential Manager；在 Linux 没有可用 Secret Service / keyring 后端时，无法保存通知密钥，不会回退到明文文件。

## QQ 机器人 / OneBot v11

若已在本机部署兼容 OneBot v11 HTTP API 的实现（例如 NapCat），在“QQ 机器人”标签页填写：

- HTTP 根地址：例如 `http://127.0.0.1:3000`（不包含 `/send_private_msg`）。
- 私聊收件人 QQ 号。
- 可选 Access Token（推荐启用）；访问令牌保存在系统凭据库。

支持 OneBot v11 的 `POST /send_private_msg`；并非自带 QQ 登录或 NoneBot 服务，需使用者自行部署兼容环境。远程公网地址**必须**使用 HTTPS，禁止 HTTP 明文传递令牌。测试后启用规则即可。请遵守相关平台使用规则。

**通知对比**：系统弹窗无需账号/配置，但离开电脑不易及时获知；QQ SMTP 较易部署、能远程收到，但邮件会有投递延迟并可能进入垃圾箱；OneBot 可投递 QQ 私聊，但需维护机器人/适配服务，复杂度最高。推荐默认桌面提醒 + QQ 邮箱。

## 数据、隐私与“史低”的定义

**这是最重要的价格口径说明。**

Steam `priceoverview` 返回的 `lowest_price` 是**当前最低卖家挂单价格**，并不包含全市场从古至今的最低挂单历史。Steam 的 `pricehistory` 通常需要 Steam 登录会话，并记录**实际成交中位价与成交量**，与前者完全不同。为避免把成交历史冒充挂单史低：

- 本程序把监控开始后获得的最低挂单价采样保存在本地 SQLite，用于本地趋势和“**观测新低**”提醒，**不声称 Steam 平台史低**。
- 官方 `pricehistory` 为独立、可选的只读展示。能匿名获取时按需画图、不缓存；不能获取时引导用户到官方商品详情页登录查看，不索取或存储 Steam Cookie。
- 每次轮询只看到查询时刻的报价，不能捕捉轮询间的所有瞬时低价。即使发送告警，商品可能在用户打开链接前售罄或变价；本应用不自动购买。
- 当前目标价比较含 Steam 展示给买家的价格/适用费用；请确认你理解报价币种与市场费用。

**本地数据路径**：

- Windows：`%APPDATA%\SteamMarketSentinel\sentinel.sqlite3` 和 `settings.json`
- Linux：`~/.local/share/steam-market-sentinel/`
- macOS：`~/.local/share/steam-market-sentinel/`（可用 `SMS_DATA_DIR` 自定义）
- 开发/便携测试：设置环境变量 `SMS_DATA_DIR` 为可写路径即可覆盖默认目录。

数据库不存密码或授权码；JSON 清单导出也不含凭据。**删除监控商品会连同该商品的本地历史一起删除**，先导出备份。

## Windows 打包 / 分享给朋友

在 **Windows** 上运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

脚本会安装开发依赖、运行单元测试并构建：

```text
dist/SteamMarketSentinel/SteamMarketSentinel.exe
```

**务必分享整个 `dist/SteamMarketSentinel/` 文件夹（建议压缩 ZIP），而不是只拷走 exe**；`--onedir` 模式比单文件启动更稳定、易调试。GitHub Actions 的 **Windows executable** 工作流可以手动触发，上传可供下载的构建产物。打包脚本必须在 Windows 上运行才能产生 Windows EXE；当前仓库提供**构建能力，不代表已发布经过签名或测试的二进制文件**。首次运行可能遇到未签名应用的 Windows SmartScreen 警告，请自行判断来源。

## 开发与测试

```bash
pip install '.[dev]'
pytest -q
ruff check src tests
```

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
│   ├── style.py        # GUI 主题
│   └── main.py         # 入口
├── tests/              # 核心逻辑单元测试
├── scripts/            # Windows 构建脚本
├── .github/workflows/  # CI 与 Windows 打包
├── README.md / README_EN.md
├── CONTRIBUTING.md / SECURITY.md / CHANGELOG.md
├── pyproject.toml / requirements.txt
├── .gitignore / .editorconfig / LICENSE
└── main.py
```

## 接口资料 / 参考项目

以下是**参考架构和数据口径**，没有复制第三方仓库受保护的代码：

- [Steam 市场公开搜索格式](https://github-wiki-see.page/m/Revadike/InternalSteamWebAPI/wiki/Search-Market)
- [Steam 市场价格数据与认证说明](https://github.com/jzimr/steam-market-api)
- [Steam 官方成交历史说明](https://github.com/HilliamT/scm-price-history)
- [steam-price-tracker：SQLite + Telegram 告警设计](https://github.com/vito-at/steam-price-tracker)
- [cs2tracker：清单、历史导出、GUI 思路](https://github.com/ashiven/cs2tracker)
- [giba-steam-market：分页结果可能少于请求数量](https://github.com/lezards/giba-steam-market)
- [Steamworks 官方货币代码表](https://partner.steamgames.com/doc/store/pricing/currencies)

本项目并非 Valve / Steam 官方产品，未获得其背书。开源代码采用 [MIT License](LICENSE)。请遵守 Steam 使用条款，并根据网络环境调整请求频率。

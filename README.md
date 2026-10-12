# Steam Market Sentinel

**跨游戏 Steam 社区市场价格哨兵** · 本地优先的桌面价格监控工具

[English](README_EN.md) · [更新日志](CHANGELOG.md) · [参与贡献](CONTRIBUTING.md)

[![Test](https://github.com/LaoZhuJackson/Steam-Market-Sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/LaoZhuJackson/Steam-Market-Sentinel/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

从 Steam 社区市场搜索物品，批量监控**最低在售挂单价**，在本地记录价格趋势，并在价格达到目标价或**刷新史低**时发出提醒。**无需 Steam 账号、密码或 API Key。**

支持 CS2、Dota 2、TF2 等所有拥有社区市场商品的 AppID。

> [!NOTE]
> 当前为 **v0.2.0**。仓库提供了 Windows 构建能力（可打包成单文件 exe），但**未发布**经过签名、在真实 Steam 网络环境中验证过的安装包。本项目非 Valve / Steam 官方产品，使用未公开文档化的公开接口，可能随时被限流或改变返回格式——请保持保守的请求频率。

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [使用指南](#使用指南)
- [通知设置](#通知设置)
- [配置项](#配置项)
- [数据、隐私与价格口径](#数据隐私与价格口径)
- [常见问题](#常见问题)
- [开发与贡献](#开发与贡献)
- [参考与致谢](#参考与致谢)

## 功能特性

- **简洁的 PySide6 深色界面** — 可筛选的监控表、商品缩略图、趋势图表、系统托盘常驻。
- **一个输入框完成添加** — 粘贴官方商品链接可即时解析；或输入关键词搜索后选择。两种方式共用同一个输入框，不再需要分别填写「关键词」和「商品名 / URL」。
- **跨游戏搜索** — 可限定 AppID（CS2 `730`、Dota 2 `570`、TF2 `440` 等），填 `0` 表示跨所有游戏搜索。
- **批量监控** — 逐个轮询，可分别设置轮询间隔与单品请求间隔；遇到限流或网络错误自动退避重试，单件失败不会中断整轮。
- **双重提醒** — ① 价格 ≤ 目标价时提醒一次，价格离开阈值范围后再次进入才会重新提醒；② 价格严格低于此前的史低时提醒（第一笔采样仅作基准，不提醒）。
- **多种通知** — 系统托盘弹窗、QQ 邮箱 SMTP、OneBot v11 私聊（可配合 NapCat 等适配服务），可以同时启用。
- **本地趋势与数据导出** — SQLite 记录最低在售挂单价；监控清单支持 JSON 导入 / 导出，历史数据支持 UTF-8 BOM 的 CSV 导出。
- **可切换报价币种** — CNY / USD / EUR，各币种的样本互相独立保存。
- **官方成交历史可导入保存** — Steam 的**成交中位价**曲线只提供给浏览器会话，程序不索取、不保存任何凭据；因此提供「导入官方历史…」：在商品页用一行脚本取一次，粘贴进来即**永久保存并并入该商品的观测曲线**（成为同一条线；二者口径不同，见下文「数据、隐私与价格口径」）。
- **密钥只进系统凭据库** — QQ 邮箱授权码、OneBot 访问令牌通过操作系统凭据库（`keyring`）保存，不写入 `settings.json`。

## 快速开始

**环境要求**：Python 3.10+；推荐 Windows 10/11。Linux / macOS 理论上可运行，但托盘与桌面通知依赖桌面环境。

### 方式一：conda

```bash
git clone https://github.com/LaoZhuJackson/Steam-Market-Sentinel.git
cd Steam-Market-Sentinel
conda create -n steam-market python=3.11 -y
conda activate steam-market
pip install -r requirements.txt   # 需在仓库根目录执行：文件末尾包含 -e .
python main.py
```

### 方式二：venv

```powershell
git clone https://github.com/LaoZhuJackson/Steam-Market-Sentinel.git
cd Steam-Market-Sentinel
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

若 PowerShell 禁止执行 `.ps1`，可以省略激活步骤，直接用解释器的绝对路径：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Linux / macOS：

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python main.py
```

`requirements.txt` 同时包含运行时依赖与开发依赖（pytest / ruff / pyinstaller），并对 conda 与 venv 环境都适用。如果只想安装运行时依赖，可以改用 `pip install .`。

启动后点击 **＋ 添加商品** 即可开始，详见下节。

## 使用指南

### 添加商品

点击 **＋ 添加商品**，在**同一个输入框**里任选一种方式：

- **粘贴官方商品链接**（最省事）：形如
  `https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline`。
  粘贴后立即解析出该商品的 AppID 与 `market_hash_name`，不需要再点搜索，也不必手动填 AppID。
- **输入关键词搜索**：例如 `AK-47`、`Revolution Case`；可先用「游戏筛选」限定 AppID（`0` 表示全部游戏）。回车或点击「搜索」后，结果列表会**自动选中第一条**。

两种方式最终都会汇总到下方「将要添加」卡片；只有选中了具体商品，「添加监控」按钮才会变为可用，因此不会再出现“两个搜索框该填哪个”的困惑。需要的话可以同时勾选「启用目标价格」并填入目标价。

添加完成后，程序会在监控循环中抓取价格，并在右侧展示缩略图与本地价格曲线。

### 监控与提醒规则

选中左侧清单中的商品，即可在右侧调整：

| 规则 | 行为 |
|---|---|
| 目标价提醒 | 价格 ≤ 目标价时提醒一次；价格回升离开阈值后**再次进入**才会重新提醒。修改目标价会立即重新武装提醒。 |
| 刷新史低时提醒 | 价格严格低于此前的史低时提醒。**第一笔采样仅作为基准，不提醒。** |
| 启用此商品监控 | 取消后该商品暂停轮询；已有的本地历史仍然保留。 |

底部按钮：**保存规则**、**在 Steam 中查看**、**移除**。移除会连同该商品的本地历史一起删除，如需保留请先导出。

另外，**设置**里有一个**全局**开关「**价格下降时提醒**」：只要这一轮的最低价低于上一轮（本地采样）就提醒一次。它与上面两条规则独立，且不受史低影响 —— 导入官方历史后，这条通常比「刷新史低」更实用。

**为什么曲线好像不怎么会动**：本地采样只在**价格发生变化**或**每 6 小时**（心跳）时才写入数据库，因此价格稳定时，曲线不会每个轮询周期都长出一个新点——这是为控制数据库体积有意设计的。右侧的「**最后检查**」每次成功轮询都会更新，可以用它确认监控确实在运行；「**最后采样**」只在真正写入新采样点时才前进。两者时间不同是正常的。

**看曲线**：鼠标**悬停在任一数据点上**会弹出该点的时间与价格。数据点很多时（例如导入几百点的官方历史）不再画圆点，避免糊成一团；点很少时保留圆点，否则单个点看不出位置。窗口不够高时右侧详情面板会自动出现滚动条，不会把图表压扁。

程序会**记住上次选中的商品**，重启后回到同一视图（导入的官方历史保存在本地数据库，不会丢）。价格趋势只此一张图：本地采样与导入的官方历史合并显示，不再是两个模式切换。

清单上方的筛选框可按名称过滤；**立即刷新**会在当前轮结束后立刻开始下一轮。

### 导入官方成交历史

除了自己轮询到的**最低挂单价**，你还可以把 Steam 官方的**成交中位价**历史导入进来（导入后会并入同一条曲线，见下）。这个数据 Steam 只发给**已登录**的会话：未登录时同一个请求直接返回 **HTTP 400**（程序请求与浏览器控制台请求都一样，均已实测）。而本程序刻意不索取、不保存任何 Steam 凭据，因此改为**由你在浏览器里取一次、导入到本地**：

1. **在浏览器里登录 Steam**，再打开该商品的市场页面（未登录必定拿到 400）；
2. 按 **F12 → Console**，粘贴下面这段脚本并回车（「导入官方历史…」对话框里的「复制浏览器脚本」按钮可直接复制）；
3. 脚本会把结果复制到剪贴板，粘贴进对话框（也可先另存为文件再「从文件读取…」），点「导入」。

登录状态只存在于你的浏览器中，脚本在同源请求里用你自己的会话取一次数据，**这串凭据全程不进入本程序**，程序也不会替你登录。

```js
(() => {
  const p = location.pathname.split('/').filter(Boolean);   // market/listings/<appid>/<name>
  if (p[0] !== 'market' || p[1] !== 'listings') { console.warn('请在 Steam 市场商品页运行'); return; }
  const url = `/market/pricehistory/?appid=${p[2]}&market_hash_name=${encodeURIComponent(decodeURIComponent(p[3]))}&currency=23&country=CN`;
  fetch(url).then(r => r.text()).then(t => {
    console.log('URL:', url); console.log('响应:', t);
    try { copy(t); console.log('已复制到剪贴板，粘贴到程序里即可'); }
    catch (e) { console.warn('剪贴板不可用，请手动复制上面的「响应」'); }
  });
})()
```

它从**当前页面 URL** 推导 AppID 与商品名，对任意商品都通用；同源 `fetch` 自动带上页面自己的会话，**程序全程不接触任何凭据**。

结尾的 `&currency=23&country=CN` 是请求 **CNY** 计价（程序会按你当前设置的币种自动填好）。实测结论：**登录后返回的币种以你的 Steam 账户为准** —— 即使浏览器挂在代理的新加坡节点、未登录时页面显示 S$，登录后拿到的仍是 ¥。程序一律按**响应里的 `price_prefix`** 标注币种，两种情况都不会标错，因此**不需要任何汇率换算**（本程序也不做汇率换算：不同币种的数字不是同一个东西，Steam 的区域定价与费用结构无法用汇率还原）。

导入的数据以**响应所用币种**为准，图表标题会标出该币种。**若与当前设置的币种不一致，导入会被拒绝并说明原因** —— 把两种货币画成一条线是错的，而现在只有一张合并图、没有别处能如实显示它，所以会让你先切换币种。重复导入会整批替换。

> [!IMPORTANT]
> **合并的含义**：本地采样是**最低挂单价**（卖家挂多少），官方数据是**成交中位价**（实际成交的中位数），二者系统性不同。合并之后：
> - 表格的「**史低**」取合并后的最小值，**可能来自官方数据**；
> - 「**刷新史低**」提醒的比较基准也随之变为合并低点。如果导入的历史里有远低于当前挂单价的点，当前价格就**再也不会**触发新低提醒（例如某件商品历史最低约 ¥1.07、而当前挂单约 ¥4）；
> - 「**现价**」列始终只由本地采样决定，不受影响；
> - 导出的 CSV 增加 `source` 列（`local` / `official`），两类数据不会被静默混在一起。
>
> **补救办法**：如果你想要的其实是"只要降价就提醒"，请在**设置**里开启「**价格下降时提醒**」—— 它比较的是**本地采样的前一个值**，完全不看史低，所以导入历史不会让它失效。
>
> 若你更希望**提醒只看本地采样**（史低列仍显示合并结果），说一声即可，这是把比较基准改回本地采样表的一处小改动。

## 通知设置

设置窗口分为「常规」「QQ 邮箱」「QQ 机器人」三个标签页。三种提醒方式可以同时启用。

| 方式 | 需要什么 | 特点 |
|---|---|---|
| 系统托盘弹窗 | 无需配置 | 最及时，但离开电脑时不易察觉 |
| QQ 邮箱 SMTP | QQ 邮箱地址 + **SMTP 授权码**（不是 QQ 密码） | 可远程收到，但可能有投递延迟或进入垃圾箱 |
| OneBot v11 私聊 | 本机部署兼容服务（例如 NapCat） | 直接投递到 QQ 私聊，但需要自行维护机器人服务 |

推荐「系统托盘弹窗 + QQ 邮箱」组合。

**QQ 邮箱**：在 QQ 邮箱网页版设置中开启 SMTP 服务并获取**授权码**，然后在设置里填写发件邮箱、收件邮箱（留空则发给自己）与授权码，点击「保存授权码并发送测试邮件」；成功后启用该通道并保存设置。程序使用 `smtp.qq.com:465`（SSL）。

**QQ 机器人 / OneBot v11**：填写 HTTP 根地址（例如 `http://127.0.0.1:3000`，**不含** `/send_private_msg`）、收件人 QQ 号，以及可选的 Access Token。程序只发送 OneBot v11 的 `POST /send_private_msg`，不包含 QQ 登录或 NoneBot 服务，需要你自行部署兼容环境。远程公网地址**必须**使用 HTTPS，以免明文传输访问令牌。

> [!IMPORTANT]
> 授权码与访问令牌仅写入操作系统凭据库（Windows 凭据管理器 / macOS 钥匙串 / Secret Service）。如果系统没有可用的安全凭据库，程序**不会**回退到明文文件，而是直接报告保存失败。

## 配置项

| 选项 | 默认 | 说明 |
|---|---|---|
| 报价币种 | CNY | 查询指定货币的最低在售挂牌价；各币种数据分开保存 |
| 每轮检查间隔 | 15 分钟 | 每轮遍历所有启用商品；可设 1–1440 分钟，过短易被限流 |
| 每件商品请求间隔 | 6 秒 | 单品之间的请求间隔；可设 3–120 秒 |
| 启动程序时自动开始监控 | 开 | 启动后在后台线程自动工作 |
| 关闭窗口时隐藏到系统托盘 | 开 | 需从托盘菜单的「退出程序」才会真正结束进程 |
| 显示系统托盘提醒 | 开 | 需要桌面环境支持系统通知 |
| 价格下降时提醒 | **关** | 只要这一轮的最低价低于**上一轮本地采样**就提醒。它比较的是本地采样的前一个值，因此**导入官方历史后依然有效**（见上文口径说明） |
| QQ 邮箱 / QQ 机器人 | 关 | 需自行配置授权码或兼容的机器人服务 |

**批量监控不等于实时行情流**：例如监控 100 件、单品间隔 6 秒，一轮请求本身就至少约 10 分钟（还不含网络与限流时间）。轮询间隔是**轮与轮之间的等待**，不是每件商品都能在该间隔内刷新。

## 数据、隐私与价格口径

这是本项目最重要的口径说明。

### 关于「史低」

Steam `priceoverview` 返回的 `lowest_price` 是**查询时刻的最低卖家挂单价格**，并不包含全市场从古至今的最低挂单历史。而 Steam 的 `pricehistory` 通常需要登录会话，记录的是**实际成交中位价与成交量**，与前者是两种完全不同的口径。为避免把成交历史冒充挂单史低，本程序：

- 把监控开始后取到的最低挂单价采样保存在本地 SQLite，用于本地趋势与「刷新史低」提醒。界面与表格里那一列叫「**史低**」，指的是**你自己观测到的最低值**（导入官方历史后，也包含导入数据中的最低值）—— 它**不等于** Steam 全平台的史低，请不要把两者混为一谈。
- 把官方成交历史作为独立、可选的展示：该图表由浏览器带着**会话 Cookie** 请求（所以你自己在浏览器里能看到它），程序**不索取、不存储** Steam Cookie，因此改由「导入官方历史…」由你在浏览器取一次、保存到本地的一张**独立表**里。导入后它会被**合并进该商品的观测曲线**（按你的选择），因此会参与「史低」与「刷新史低」的比较；「现价」始终只来自本地采样。导出 CSV 用 `source` 列区分两类数据。
- 每次轮询只能看到查询时刻的报价，无法捕捉轮询之间的瞬时低价。即使发出告警，商品也可能在你打开链接前售罄或变价——**本程序不自动购买**。
- 目标价比较基于 Steam 展示给买家的价格与适用费用，请注意确认报价币种与市场费用。

### 本地数据

| 平台 | 路径 |
|---|---|
| Windows | `%APPDATA%\SteamMarketSentinel\sentinel.sqlite3` 和 `settings.json` |
| Linux / macOS | `~/.local/share/steam-market-sentinel/` |

设置环境变量 `SMS_DATA_DIR` 为可写路径即可覆盖默认目录，便于便携使用或在开发中隔离测试数据。

数据库不保存密码或授权码，JSON 清单导出也不含凭据。**删除监控商品会连同该商品的本地历史一起删除**——请先导出备份。

## 常见问题

<details>
<summary>需要 Steam 账号、密码或 API Key 吗？</summary>

不需要。程序只访问公开的市场搜索与报价接口，没有登录流程，也不保存会话 Cookie。

</details>

<details>
<summary>监控很多商品时，为什么刷新这么慢？</summary>

轮询间隔是**轮与轮之间**的等待，而不是每件商品的刷新周期。监控 100 件、单品间隔 6 秒时，仅一轮请求本身至少约 10 分钟，再叠加网络与限流时间。两个间隔都可以在设置里调整，但设置过短容易触发 Steam 限流。

</details>

<details>
<summary>切换币种后，目标价为什么没有自动换算？</summary>

不同币种的历史样本与史低是互相独立保存的，程序不做汇率换算（汇率与市场费用都会影响判断）。请按新币种重新设定目标价阈值。

</details>

<details>
<summary>系统托盘没有弹出提醒？</summary>

需要桌面环境支持系统通知，且托盘图标可用。部分精简桌面环境或远程会话不支持弹窗，此时提醒仍会写入状态栏。

</details>

<details>
<summary>QQ 邮箱发送失败？</summary>

请依次确认：填写的是 **SMTP 授权码**而不是 QQ 登录密码；已在 QQ 邮箱网页版开启 SMTP 服务；当前网络允许访问 `smtp.qq.com:465`。程序会在状态栏报告具体错误，且不会在界面中回显任何密钥。

</details>

<details>
<summary>「官方成交历史」显示无法获取？</summary>

程序不再自动请求它了——Steam 只把成交历史发给**已登录**的会话，未登录一律返回 HTTP 400，而本程序不会索取或保存你的 Steam 凭据。所以请点「导入官方历史…」：**先在浏览器里登录** Steam 并打开该商品页，用对话框里的脚本取一次，导入后永久保存在本地，之后不必再联网。

另需注意：该曲线是**成交中位价**，与本地记录的**最低挂单价**不是同一个口径，但导入后二者会合并进同一条曲线，因此它**会影响**「史低」与新低提醒的判断（详见上文「数据、隐私与价格口径」）。

</details>

<details>
<summary>搜索不到我想要的商品？</summary>

匿名搜索的结果数量可能少于请求数量，冷门商品也可能搜不到。最稳妥的做法是在浏览器打开该商品的市场页面，复制地址栏链接，粘贴到「添加商品」的输入框。

</details>

## 开发与贡献

```bash
pip install -e '.[dev]'
pytest -q
ruff check src tests
```

欢迎提 Issue / PR，详细约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。核心要求：不要在 GUI 主线程发起网络请求；金额解析与告警规则需要有单元测试；不采集 Steam 密码或 Cookie；新增请求要考虑限流与失败重试。

<details>
<summary><b>构建 Windows 可执行文件（开发者）</b></summary>

在 **Windows** 上运行：

```powershell
# 单文件 exe（便于分享，双击即可运行）
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 -OneFile
# 或按文件夹分发（启动更快、更易排查）
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

两种方式的产物与取舍：

| 方式 | 产物 | 取舍 |
|---|---|---|
| `-OneFile` | `dist/SteamMarketSentinel.exe` | **单独一个文件**即可运行；但每次启动都要把约 200MB 解压到 `%TEMP%`，首次出现窗口会慢几秒，且未签名的单文件更容易被杀软误报 |
| 默认（`--onedir`） | `dist/SteamMarketSentinel/SteamMarketSentinel.exe` | 启动更快、更好排查；分发时需压缩并分享**整个文件夹**，不能只拷 exe |

- 需要指定解释器（例如某个 conda 环境）时加 `-Python "C:\...\python.exe"`。
- 构建后建议先自检，不会动你真实的监控数据：
  `$env:SMS_DATA_DIR = "<仓库>\.build-test"; & .\dist\SteamMarketSentinel.exe`
- 仓库的 **Windows executable** GitHub Actions 工作流也可以手动触发并下载构建产物；Windows 可执行文件必须在 Windows 上构建。
- 构建产物未签名，首次运行可能触发 Windows SmartScreen 警告，请自行判断来源。

</details>

## 参考与致谢

以下是**参考架构与数据口径**的来源，未复制第三方仓库受保护的代码：

- [Steam 市场公开搜索格式](https://github-wiki-see.page/m/Revadike/InternalSteamWebAPI/wiki/Search-Market)
- [Steam 市场价格数据与认证说明](https://github.com/jzimr/steam-market-api)
- [Steam 官方成交历史说明](https://github.com/HilliamT/scm-price-history)
- [steam-price-tracker：SQLite + Telegram 告警设计](https://github.com/vito-at/steam-price-tracker)
- [cs2tracker：清单、历史导出、GUI 思路](https://github.com/ashiven/cs2tracker)
- [giba-steam-market：分页结果可能少于请求数量](https://github.com/lezards/giba-steam-market)
- [Steamworks 官方货币代码表](https://partner.steamgames.com/doc/store/pricing/currencies)

## 许可证

[MIT License](LICENSE) · Steam Market Sentinel contributors

本项目并非 Valve / Steam 官方产品，未获得其背书。请遵守 Steam 使用条款，并根据你的网络环境调整请求频率。

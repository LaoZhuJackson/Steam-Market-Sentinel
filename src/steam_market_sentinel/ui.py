"""Compact PySide6 desktop interface. Never block the UI for HTTP or SMTP calls."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import sqlite3
from pathlib import Path
import webbrowser

import requests
from PySide6.QtCharts import QChart, QChartView, QLineSeries
from PySide6.QtCore import QDateTime, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QCursor, QFont, QIcon, QPainter, QPen,
                           QPixmap)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QTabWidget, QToolTip, QVBoxLayout, QWidget,
)
from PySide6.QtCharts import QDateTimeAxis, QValueAxis

from .alerts import SecretStore, render_alert, send_test
from .config import Settings
from .database import Database
from .models import Alert, CURRENCIES, MarketItem, cents_text, from_amount, listing_url, parse_listing_url
from .monitor import MonitorWorker
from .steam_api import SteamMarketClient, parse_imported_history
from .style import PALETTE

# Marker dots turn a dense series into a blob — an imported official history is
# routinely 300+ points. But a *sparse* series needs them: a single point draws no
# line at all, so it would render invisibly. Hover works either way.
SPARSE_SERIES_MARKERS = 40

STATE_COLORS = {
    "监控中": PALETTE["accent"],
    "异常": PALETTE["danger"],
    "暂停": PALETTE["text_disabled"],
    "待检测": PALETTE["text_muted"],
}

# Official sales history cannot be fetched by this app (Steam serves it only to a
# browser session, and we never touch cookies). So the user fetches it once from
# the item page they already have open: the snippet derives appid + name from the
# current URL, and same-origin fetch carries the page's own session. No credential
# ever reaches this program.
#
# ``__REQUESTED_CURRENCY__`` is filled in by history_snippet(): the market's quote
# endpoints honour currency/country, so asking for the configured currency may
# avoid the browser's own region currency (a proxy/VPN otherwise decides it).
# Not an f-string — the ${...} are JavaScript template literals.
_HISTORY_SNIPPET = """(() => {
  const p = location.pathname.split('/').filter(Boolean);   // market/listings/<appid>/<name>
  if (p[0] !== 'market' || p[1] !== 'listings') { console.warn('请在 Steam 市场商品页运行'); return; }
  // encodeURIComponent leaves !'()* raw, and many item names contain brackets
  // (e.g. "AK-47 | Redline (Field-Tested)") — encode them strictly.
  const enc = s => encodeURIComponent(s).replace(/[!'()*]/g, c => '%' + c.charCodeAt(0).toString(16).toUpperCase());
  const url = `/market/pricehistory/?appid=${p[2]}&market_hash_name=${enc(decodeURIComponent(p[3]))}__REQUESTED_CURRENCY__`;
  fetch(url).then(r => r.text()).then(t => {
    console.log('URL:', url); console.log('响应:', t);
    try { copy(t); console.log('已复制到剪贴板，粘贴到程序里即可'); }
    catch (e) { console.warn('剪贴板不可用，请手动复制上面的「响应」'); }
  });
})()"""


def history_snippet(currency: str) -> str:
    """The browser snippet, asking Steam for ``currency`` explicitly.

    Whether ``pricehistory`` honours ``currency`` is unverified, and if it
    ignores it the data comes back in the browser's own region currency — which is
    harmless, because the imported series is always labelled from the response's
    own ``price_prefix``, never from what we asked for.
    """
    currency_id, country, _symbol = CURRENCIES[currency]
    return _HISTORY_SNIPPET.replace("__REQUESTED_CURRENCY__",
                                    f"&currency={currency_id}&country={country}")


def _fetch_image_bytes(url: str) -> bytes:
    """Fetch an image with `requests` — never Qt's network stack (see _run_image_task)."""
    response = requests.get(url, timeout=(8, 20))
    response.raise_for_status()
    return response.content


def point_tooltip_text(symbol: str, stamp: int, price: float) -> str:
    """Hover text for one chart point: when it was observed, and the price.

    ``symbol`` is passed in rather than taken from ``Settings.currency``, because
    an imported official series is in whatever currency the *browser* used.
    """
    when = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
    return f"{when}\n{symbol}{price:,.2f}"


def app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#64e5bf"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(5, 5, 54, 54)
    p.setPen(QColor("#10252b"))
    font = QFont("Segoe UI", 28, QFont.Bold)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignCenter, "S")
    p.end()
    return QIcon(pix)


def button(text: str, *, primary: bool = False) -> QPushButton:
    result = QPushButton(text)
    if primary:
        result.setObjectName("primary")
    return result


class BackgroundCall(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def run(self):
        try:
            result = self.callback()
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class SearchAddDialog(QDialog):
    """添加商品：**一个**输入框同时承担「关键词搜索」和「粘贴 Steam 链接」。

    以前这里上下各有一个输入框（上方搜关键词、下方填完整商品名/URL），看起来都是
    “搜索框”却要求不同内容，很容易误填。现在只保留一个内容输入框：

    - 粘贴官方商品链接 → 立即解析出唯一商品，不发请求、无需再选；
    - 输入关键词 → 列出搜索结果并预选第一条。

    无论走哪条路，最终都汇总到下方「将要添加」卡片里的同一件商品，未选中时
    「添加监控」按钮保持禁用，避免出现两个含义不清的输入框。
    """

    def __init__(self, parent=None, currency="CNY"):
        super().__init__(parent)
        self.currency = currency
        self.setWindowTitle("添加 Steam 监控商品")
        self.resize(760, 600)
        self.found: list[MarketItem] = []
        self.selected_item: MarketItem | None = None
        self.task: BackgroundCall | None = None
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = QLabel("搜索市场商品")
        title.setObjectName("section")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("输入商品名称搜索，或直接粘贴 Steam 市场商品链接…")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.do_search)
        self.query.textChanged.connect(self.on_query_changed)
        self.filter_app = QSpinBox()
        self.filter_app.setRange(0, 99999999)
        self.filter_app.setValue(730)
        self.filter_app.setSpecialValueText("全部游戏")
        self.filter_app.setToolTip("搜索关键词时限定游戏；0 表示跨所有游戏搜索。粘贴链接时该筛选不生效")
        self.search_btn = button("搜索", primary=True)
        self.search_btn.clicked.connect(self.do_search)
        row.addWidget(self.query, 1)
        row.addWidget(QLabel("游戏筛选"))
        row.addWidget(self.filter_app)
        row.addWidget(self.search_btn)
        layout.addLayout(row)
        self.results = QTableWidget(0, 3)
        self.results.setHorizontalHeaderLabels(["商品名称", "游戏", "市场显示价格"])
        self.results.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.results.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.results.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.results.setSelectionBehavior(QTableWidget.SelectRows)
        self.results.setSelectionMode(QTableWidget.SingleSelection)
        self.results.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results.verticalHeader().setVisible(False)
        self.results.setAlternatingRowColors(True)
        self.results.itemSelectionChanged.connect(self.on_result_selected)
        self.results.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self.results, 1)
        self.info = QLabel("粘贴 Steam 商品链接可直接添加；或输入名称后回车搜索，再选一条结果。")
        self.info.setObjectName("hint")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        picked = QFrame()
        picked.setObjectName("card")
        pick_layout = QVBoxLayout(picked)
        pick_layout.setContentsMargins(14, 12, 14, 12)
        pick_layout.setSpacing(7)
        caption = QLabel("将要添加")
        caption.setObjectName("metricCaption")
        self.picked_name = QLabel("尚未选择商品")
        self.picked_name.setObjectName("section")
        self.picked_name.setWordWrap(True)
        self.picked_meta = QLabel("搜索后选中一条结果，或直接粘贴 Steam 商品链接")
        self.picked_meta.setObjectName("subtle")
        self.picked_meta.setWordWrap(True)
        pick_layout.addWidget(caption)
        pick_layout.addWidget(self.picked_name)
        pick_layout.addWidget(self.picked_meta)
        price_row = QHBoxLayout()
        self.target_enabled = QCheckBox("启用目标价格")
        self.target = QDoubleSpinBox()
        self.target.setRange(0.00, 999999999.00)
        self.target.setDecimals(2)
        self.target.setValue(10.00)
        self.target.setPrefix(f"{CURRENCIES[currency][2]} ")
        self.target.setToolTip(f"新增商品的目标价按 {currency} 填写")
        self.target.setEnabled(False)
        self.target_enabled.toggled.connect(self.target.setEnabled)
        price_row.addWidget(self.target_enabled)
        price_row.addWidget(self.target)
        price_row.addStretch(1)
        pick_layout.addLayout(price_row)
        layout.addWidget(picked)
        self.actions = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.actions.button(QDialogButtonBox.Ok).setText("添加监控")
        self.actions.button(QDialogButtonBox.Cancel).setText("取消")
        self.actions.button(QDialogButtonBox.Ok).setEnabled(False)
        self.actions.accepted.connect(self.accept)
        self.actions.rejected.connect(self.reject)
        layout.addWidget(self.actions)
        self.query.setFocus()

    def do_search(self):
        text = self.query.text().strip()
        if not text or (self.task and self.task.isRunning()):
            return
        listing = parse_listing_url(text)
        if listing:
            # 链接本身已经确定了唯一商品，无需请求 Steam；但仍把它作为一行结果
            # 展示，保证“结果列表 = 当前候选”这一条规则始终成立。
            self.show_results([MarketItem(listing[0], listing[1], listing[1])],
                              note="已从 Steam 链接解析出商品，可直接添加。")
            return
        self.search_btn.setEnabled(False)
        self.info.setText("正在查询 Steam 市场…")
        filter_appid = self.filter_app.value()
        currency = self.currency
        self.task = BackgroundCall(lambda: SteamMarketClient(retries=1).search(
            text, filter_appid, limit=30, currency=currency))
        self.task.succeeded.connect(self.show_results)
        self.task.failed.connect(self.search_failed)
        self.task.finished.connect(lambda: self.search_btn.setEnabled(True))
        self.task.start()

    def on_query_changed(self, text: str):
        """粘贴链接后立即解析，不必再点“搜索”；关键词则等回车或点击。"""
        if parse_listing_url(text):
            self.do_search()

    def show_results(self, records: list[MarketItem], note: str = ""):
        self.found = records
        self.results.setRowCount(len(records))
        for i, item in enumerate(records):
            for j, text in enumerate((item.display_name, item.app_name or str(item.appid),
                                      item.price_text or "—")):
                cell = QTableWidgetItem(text)
                if j == 2:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                cell.setToolTip(item.hash_name)
                self.results.setItem(i, j, cell)
        if records:
            # 预选第一条，「搜索 → 添加」两步即可完成。
            self.results.selectRow(0)
            self.info.setText(note or f"找到 {len(records)} 项（最多显示前 30 项），已选中第一条。")
        else:
            self.select(None)
            self.info.setText(note or "没有找到匹配商品。可以换个关键词，或直接粘贴 Steam 商品链接。")

    def search_failed(self, error: str):
        self.select(None)
        self.info.setText("搜索失败：" + error)

    def on_result_selected(self):
        row = self.results.currentRow()
        if 0 <= row < len(self.found):
            self.select(self.found[row])

    def select(self, item: MarketItem | None):
        """唯一的“将要添加”状态：摘要卡片与「添加监控」按钮都跟随它。"""
        self.selected_item = item
        self.actions.button(QDialogButtonBox.Ok).setEnabled(item is not None)
        if item is None:
            self.picked_name.setText("尚未选择商品")
            self.picked_meta.setText("搜索后选中一条结果，或直接粘贴 Steam 商品链接")
            return
        self.picked_name.setText(item.display_name or item.hash_name)
        meta = [f"AppID {item.appid}"]
        if item.app_name:
            meta.append(item.app_name)
        if item.price_text:
            meta.append(f"市场显示价 {item.price_text}")
        meta.append(item.hash_name)
        self.picked_meta.setText(" · ".join(meta))

    def closeEvent(self, event):
        if self.task and self.task.isRunning():
            event.ignore()
            self.info.setText("搜索还未结束，完成后即可关闭窗口。")
        else:
            event.accept()

    def chosen(self) -> tuple[MarketItem, int | None]:
        if self.selected_item is None:
            raise ValueError("请先搜索并选择一件商品，或粘贴 Steam 商品链接")
        target = from_amount(self.target.value()) if self.target_enabled.isChecked() else None
        return self.selected_item, target

    def accept(self):
        if self.task and self.task.isRunning():
            QMessageBox.information(self, "正在搜索", "Steam 搜索仍在进行中，请完成后操作。")
            return
        try:
            self.chosen()
        except ValueError as exc:
            QMessageBox.warning(self, "信息不完整", str(exc))
            return
        super().accept()

    def reject(self):
        if self.task and self.task.isRunning():
            QMessageBox.information(self, "正在搜索", "Steam 搜索仍在进行中，请完成后操作。")
            return
        super().reject()


class SettingsDialog(QDialog):
    def __init__(self, parent, original: Settings):
        super().__init__(parent)
        self.original = original
        self.task: BackgroundCall | None = None
        self.setWindowTitle("设置 · Steam Market Sentinel")
        self.resize(540, 570)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        general = QWidget()
        gf = QFormLayout(general)
        self.currency = QComboBox()
        self.currency.addItems(list(CURRENCIES))
        self.currency.setCurrentText(original.currency)
        self.interval = QSpinBox()
        self.interval.setRange(1, 1440)
        self.interval.setSuffix(" 分钟")
        self.interval.setValue(max(1, original.interval_seconds // 60))
        self.gap = QSpinBox()
        self.gap.setRange(3, 120)
        self.gap.setSuffix(" 秒")
        self.gap.setValue(original.request_gap_seconds)
        self.auto = QCheckBox("启动程序时自动开始监控")
        self.auto.setChecked(original.auto_start)
        self.tray_checkbox = QCheckBox("关闭窗口时隐藏到系统托盘")
        self.tray_checkbox.setChecked(original.minimize_to_tray)
        self.desktop = QCheckBox("显示系统托盘提醒")
        self.desktop.setChecked(original.desktop_enabled)
        self.drop_alert = QCheckBox("价格下降时提醒")
        self.drop_alert.setChecked(original.drop_alert_enabled)
        self.drop_alert.setToolTip("只要这一轮的最低价低于上一轮（本地采样）就提醒；"
                                   "不受史低影响，因此导入历史后仍然有效")
        gf.addRow("报价币种", self.currency)
        gf.addRow("每轮检查间隔", self.interval)
        gf.addRow("每件商品请求间隔", self.gap)
        gf.addRow(self.auto)
        gf.addRow(self.tray_checkbox)
        gf.addRow(self.desktop)
        gf.addRow(self.drop_alert)
        general_note = QLabel("建议轮询间隔 ≥ 15 分钟，避免 Steam 限流。\n"
                              "切换币种会分开保存趋势与史低，目标价格需按新币种重新调整。")
        general_note.setWordWrap(True)
        general_note.setObjectName("hint")
        gf.addRow(general_note)
        tabs.addTab(general, "常规")

        mail = QWidget()
        mf = QFormLayout(mail)
        self.email_enabled = QCheckBox("启用 QQ 邮箱提醒")
        self.email_enabled.setChecked(original.email_enabled)
        self.sender = QLineEdit(original.email_sender)
        self.sender.setPlaceholderText("123456@qq.com")
        self.receiver = QLineEdit(original.email_receiver)
        self.receiver.setPlaceholderText("留空则发给自己")
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.Password)
        self.smtp_password.setPlaceholderText("QQ 邮箱 SMTP 授权码（留空保留原值）")
        self.mail_test = button("保存授权码并发送测试邮件")
        self.mail_test.clicked.connect(lambda: self.test_channel("email"))
        mf.addRow(self.email_enabled)
        mf.addRow("发件 QQ 邮箱", self.sender)
        mf.addRow("收件邮箱", self.receiver)
        mf.addRow("SMTP 授权码", self.smtp_password)
        mf.addRow(self.mail_test)
        note = QLabel("QQ 邮箱需要在邮箱设置中开启 SMTP 并获取授权码（不是 QQ 登录密码）。\n"
                      "授权码仅写入操作系统凭据库，不进入 JSON、数据库、日志或导出文件。")
        note.setWordWrap(True)
        note.setObjectName("hint")
        mf.addRow(note)
        tabs.addTab(mail, "QQ 邮箱")

        bot = QWidget()
        bf = QFormLayout(bot)
        self.onebot_enabled = QCheckBox("启用 OneBot v11 私聊通知")
        self.onebot_enabled.setChecked(original.onebot_enabled)
        self.onebot_url = QLineEdit(original.onebot_url)
        self.onebot_id = QLineEdit(original.onebot_user_id)
        self.onebot_token = QLineEdit()
        self.onebot_token.setEchoMode(QLineEdit.Password)
        self.onebot_token.setPlaceholderText("可选，留空保留已存储的令牌")
        self.bot_test = button("保存令牌并发送测试消息")
        self.bot_test.clicked.connect(lambda: self.test_channel("onebot"))
        bf.addRow(self.onebot_enabled)
        bf.addRow("OneBot HTTP 地址", self.onebot_url)
        bf.addRow("接收人 QQ 号", self.onebot_id)
        bf.addRow("Access Token", self.onebot_token)
        bf.addRow(self.bot_test)
        bot_note = QLabel("适用于本地 OneBot v11 兼容服务（例如 NapCat）提供的 HTTP API。\n"
                          "NoneBot 本身需要相应的 OneBot 适配器和 HTTP 接口；"
                          "仅 localhost 可使用 HTTP，远程地址必须使用 HTTPS。")
        bot_note.setObjectName("hint")
        bot_note.setWordWrap(True)
        bf.addRow(bot_note)
        tabs.addTab(bot, "QQ 机器人")
        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        layout.addWidget(self.test_status)
        actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        actions.button(QDialogButtonBox.Save).setText("保存设置")
        actions.button(QDialogButtonBox.Cancel).setText("取消")
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def closeEvent(self, event):
        if self.task and self.task.isRunning():
            event.ignore()
            self.test_status.setText("测试消息尚未发送完成，请稍后关闭。")
        else:
            event.accept()

    def chosen(self) -> Settings:
        return replace(
            self.original,
            currency=self.currency.currentText(),
            interval_seconds=self.interval.value() * 60,
            request_gap_seconds=self.gap.value(),
            auto_start=self.auto.isChecked(),
            minimize_to_tray=self.tray_checkbox.isChecked(),
            desktop_enabled=self.desktop.isChecked(),
            drop_alert_enabled=self.drop_alert.isChecked(),
            email_enabled=self.email_enabled.isChecked(),
            email_sender=self.sender.text().strip(),
            email_receiver=self.receiver.text().strip(),
            onebot_enabled=self.onebot_enabled.isChecked(),
            onebot_url=self.onebot_url.text().strip(),
            onebot_user_id=self.onebot_id.text().strip(),
        )

    def store_secrets(self):
        if self.smtp_password.text():
            SecretStore.set("qq_smtp_authorization", self.smtp_password.text())
        if self.onebot_token.text():
            SecretStore.set("onebot_access_token", self.onebot_token.text())

    def test_channel(self, channel: str):
        if self.task and self.task.isRunning():
            return
        try:
            self.store_secrets()
        except Exception as exc:
            self.test_status.setText("保存凭据失败：" + str(exc))
            return
        self.test_status.setText("正在发送测试消息…")
        settings = self.chosen()
        self.task = BackgroundCall(lambda: send_test(settings, channel))
        self.task.succeeded.connect(lambda _: self.test_status.setText("测试消息已提交到发送服务。"))
        self.task.failed.connect(lambda error: self.test_status.setText("测试失败：" + error))
        self.task.start()

    def accept(self):
        if self.task and self.task.isRunning():
            QMessageBox.information(self, "正在发送", "请完成测试后保存设置。")
            return
        try:
            self.store_secrets()
        except Exception as exc:
            QMessageBox.critical(self, "凭据库错误", "无法安全保存密钥：" + str(exc))
            return
        super().accept()

    def reject(self):
        if self.task and self.task.isRunning():
            QMessageBox.information(self, "正在发送", "请完成测试后退出设置。")
            return
        super().reject()


class ImportHistoryDialog(QDialog):
    """把浏览器里一次性取到的官方成交历史粘贴或载入进来。

    打开商品页的动作放在这里：导入流程本来就要在浏览器里打开该商品页面，把
    「在 Steam 中查看」放在同一步更顺手。
    """

    def __init__(self, parent, item_name: str, currency: str = "CNY", item_url: str = ""):
        super().__init__(parent)
        self.currency = currency
        self.item_url = item_url
        self.setWindowTitle("导入官方成交历史")
        self.resize(660, 470)
        layout = QVBoxLayout(self)
        layout.setSpacing(11)
        title = QLabel(f"为「{item_name}」导入 Steam 官方成交历史")
        title.setObjectName("section")
        title.setWordWrap(True)
        layout.addWidget(title)
        steps = QLabel(
            "Steam 只把成交历史发给【已登录】的会话（未登录会返回 400），"
            "而本程序不会索取或保存任何 Steam 凭据，因此请在你的浏览器里取一次：\n"
            "1. 点「在 Steam 中查看」打开该商品页面（确认浏览器里已登录 Steam）；\n"
            "2. 按 F12 → Console，粘贴「复制浏览器脚本」得到的脚本并回车；\n"
            "3. 把复制到的结果粘贴到下面的框里（也可先另存为文件再「从文件读取…」）。")
        steps.setObjectName("hint")
        steps.setWordWrap(True)
        layout.addWidget(steps)
        actions = QHBoxLayout()
        open_btn = button("在 Steam 中查看")
        open_btn.setToolTip("在浏览器中打开该商品页面 —— 脚本就是要在这个页面上运行")
        open_btn.setEnabled(bool(item_url))
        open_btn.clicked.connect(self.open_market)
        copy_btn = button("复制浏览器脚本")
        copy_btn.clicked.connect(self.copy_snippet)
        read_btn = button("从文件读取…")
        read_btn.clicked.connect(self.load_file)
        actions.addWidget(open_btn)
        actions.addWidget(copy_btn)
        actions.addWidget(read_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.box = QPlainTextEdit()
        self.box.setPlaceholderText(
            '{"success":true,"prices":[["Nov 27 2013 01: +0",12.34,"7"], …]}')
        layout.addWidget(self.box, 1)
        self.status = QLabel("")
        self.status.setObjectName("subtle")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("导入")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def open_market(self):
        if self.item_url:
            webbrowser.open(self.item_url)

    def copy_snippet(self):
        QApplication.clipboard().setText(history_snippet(self.currency))
        self.status.setText(
            f"已复制脚本（会向 Steam 请求 {self.currency} 计价）。"
            "到 Steam 商品页按 F12 → Console 粘贴回车，再把结果复制回来。")

    def load_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "选择官方成交历史 JSON", "", "JSON (*.json);;文本 (*.txt);;所有文件 (*)")
        if not filename:
            return
        try:
            self.box.setPlainText(Path(filename).read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            QMessageBox.warning(self, "读取失败", str(exc))

    def parsed(self) -> tuple[list[tuple[int, int]], str]:
        return parse_imported_history(self.box.toPlainText())

    def accept(self):
        try:
            points, _label = self.parsed()
        except ValueError as exc:
            self.status.setText("无法解析：" + str(exc))
            QMessageBox.warning(self, "无法导入", str(exc))
            return
        self.status.setText(f"将导入 {len(points)} 个数据点。")
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, db: Database, settings: Settings, settings_path: Path):
        super().__init__()
        self.db = db
        self.settings = settings
        self.settings_path = settings_path
        self.worker: MonitorWorker | None = None
        self._pending_refresh = False
        self._image_cache: dict[str, QPixmap] = {}
        self._chart_key: tuple | None = None
        self._thumb_tried: set[int] = set()
        self._image_tasks: list[BackgroundCall] = []
        self._quitting = False
        self.current_item_id: int | None = None
        self.setWindowIcon(app_icon())
        self.setWindowTitle("Steam Market Sentinel · Steam 市场价格哨兵")
        self.resize(1300, 900)
        self.setMinimumSize(950, 610)
        self._make_ui()
        self._make_tray()
        # Restore the last view: without this the app always reopened on the first
        # row, which made an imported official series look as if it had been lost.
        self.current_item_id = self.settings.selected_item
        self.refresh_table()
        if self.settings.auto_start:
            QTimer.singleShot(200, self.start_monitor)

    def _make_ui(self):
        main = QWidget()
        self.setCentralWidget(main)
        outer = QVBoxLayout(main)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(13)
        heading = QHBoxLayout()
        brand = QVBoxLayout()
        hero = QLabel("Steam Market Sentinel")
        hero.setObjectName("hero")
        brand.addWidget(hero)
        desc = QLabel("批量监控 · 趋势历史 · 价格提醒")
        desc.setObjectName("subtle")
        brand.addWidget(desc)
        heading.addLayout(brand)
        heading.addStretch(1)
        self.run_button = button("▶ 开始监控", primary=True)
        self.run_button.clicked.connect(self.toggle_monitor)
        self.refresh_button = button("↻ 立即刷新")
        self.refresh_button.clicked.connect(self.refresh_now)
        settings_button = button("⚙ 设置")
        settings_button.clicked.connect(self.open_settings)
        heading.addWidget(self.run_button)
        heading.addWidget(self.refresh_button)
        heading.addWidget(settings_button)
        outer.addLayout(heading)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)
        left = QFrame()
        left.setObjectName("panel")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(13, 15, 13, 13)
        top = QHBoxLayout()
        t = QLabel("监控清单")
        t.setObjectName("section")
        self.count_label = QLabel("0 件")
        self.count_label.setObjectName("badge")
        top.addWidget(t)
        top.addWidget(self.count_label)
        top.addStretch(1)
        add = button("＋ 添加商品", primary=True)
        add.clicked.connect(self.add_item)
        top.addWidget(add)
        lv.addLayout(top)
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText("筛选监控清单…")
        self.filter_text.textChanged.connect(self.filter_rows)
        lv.addWidget(self.filter_text)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["商品", "现价", "目标", "史低", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3, 4):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        lv.addWidget(self.table, 1)
        actions = QHBoxLayout()
        import_btn = button("导入清单")
        import_btn.clicked.connect(self.import_watchlist)
        export_btn = button("导出清单")
        export_btn.clicked.connect(self.export_watchlist)
        csv_btn = button("导出历史 CSV")
        csv_btn.clicked.connect(self.export_history)
        actions.addWidget(import_btn)
        actions.addWidget(export_btn)
        actions.addWidget(csv_btn)
        lv.addLayout(actions)
        splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("panel")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(17, 16, 17, 14)
        rv.setSpacing(12)
        info = QHBoxLayout()
        self.image = QLabel("📦")
        self.image.setObjectName("thumb")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setFixedSize(94, 94)
        info.addWidget(self.image)
        name_box = QVBoxLayout()
        self.item_title = QLabel("选择一个监控商品")
        self.item_title.setObjectName("section")
        self.item_title.setWordWrap(True)
        self.item_game = QLabel("点击左侧商品查看详情")
        self.item_game.setObjectName("subtle")
        self.item_last = QLabel("")
        self.item_last.setObjectName("subtle")
        name_box.addWidget(self.item_title)
        name_box.addWidget(self.item_game)
        name_box.addWidget(self.item_last)
        info.addLayout(name_box, 1)
        rv.addLayout(info)
        rv.addWidget(self._divider())
        metrics = QGridLayout()
        self.price_metric = self._metric(metrics, 0, "当前最低挂单")
        self.low_metric = self._metric(metrics, 1, "史低")
        self.target_metric = self._metric(metrics, 2, "目标价格")
        rv.addLayout(metrics)

        chart_actions = QHBoxLayout()
        chart_label = QLabel("价格趋势")
        chart_label.setObjectName("section")
        chart_actions.addWidget(chart_label)
        chart_actions.addStretch()
        import_history = button("导入官方历史…")
        import_history.setToolTip("在 Steam 商品页用一行脚本取一次官方成交历史再粘贴进来；程序不接触任何凭据")
        import_history.clicked.connect(self.import_official_history)
        chart_actions.addWidget(import_history)
        rv.addLayout(chart_actions)
        # 这段说明放在图表“上方”：它描述的是当前图表的口径；放在下方时会挤占
        # 并遮住趋势图本体。
        self.history_hint = QLabel("")
        self.history_hint.setObjectName("subtle")
        self.history_hint.setWordWrap(True)
        rv.addWidget(self.history_hint)
        self.chart = QChartView()
        self.chart.setRenderHint(QPainter.Antialiasing)
        # Tall enough that small price moves are readable; the panel scrolls if the
        # window is shorter than this. Most of the growth comes from the stretch
        # below, so a taller window gives the chart more room automatically.
        self.chart.setMinimumHeight(260)
        self.chart.setMouseTracking(True)  # 让 QLineSeries.hovered 能触发点提示
        rv.addWidget(self.chart, 1)
        rules = QFrame()
        rules.setObjectName("card")
        rules_layout = QVBoxLayout(rules)
        rules_layout.setContentsMargins(14, 11, 14, 12)
        rules_layout.setSpacing(9)
        rules_caption = QLabel("提醒规则")
        rules_caption.setObjectName("metricCaption")
        rules_layout.addWidget(rules_caption)
        f = QGridLayout()
        self.target_check = QCheckBox("目标价提醒")
        self.target_edit = QDoubleSpinBox()
        self.target_edit.setRange(0, 999999999)
        self.target_edit.setDecimals(2)
        self.target_edit.setPrefix(f"{CURRENCIES[self.settings.currency][2]} ")
        self.target_edit.setEnabled(False)
        self.target_check.toggled.connect(self.target_edit.setEnabled)
        self.low_check = QCheckBox("刷新史低时提醒")
        self.enabled_check = QCheckBox("启用此商品监控")
        f.addWidget(self.target_check, 0, 0)
        f.addWidget(self.target_edit, 0, 1)
        f.addWidget(self.low_check, 1, 0, 1, 2)
        f.addWidget(self.enabled_check, 2, 0, 1, 2)
        rules_layout.addLayout(f)
        rv.addWidget(rules)
        item_actions = QHBoxLayout()
        save = button("保存规则", primary=True)
        save.clicked.connect(self.save_rule)
        delete = button("移除")
        delete.setObjectName("danger")
        delete.clicked.connect(self.remove_item)
        item_actions.addWidget(save)
        item_actions.addStretch()
        item_actions.addWidget(delete)
        rv.addLayout(item_actions)
        # The detail panel needs more vertical room than a short window can give
        # (the chart alone has a real minimum), so scroll it rather than letting
        # Qt squeeze or clip the widgets — which is what previously made the chart
        # look cramped on small windows.
        scroller = QScrollArea()
        scroller.setWidget(right)
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        splitter.addWidget(scroller)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([510, 670])
        self.statusBar().showMessage("已就绪 · 价格按选择的报价币种记录")

    def _metric(self, grid: QGridLayout, column: int, caption: str) -> QLabel:
        frame = QFrame()
        frame.setObjectName("stat")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(13, 10, 13, 11)
        inner.setSpacing(3)
        text = QLabel(caption)
        text.setObjectName("metricCaption")
        value = QLabel("—")
        value.setObjectName("metricValue")
        inner.addWidget(text)
        inner.addWidget(value)
        grid.addWidget(frame, 0, column)
        return value

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setObjectName("hline")
        line.setFixedHeight(1)
        return line

    @staticmethod
    def _stamp(value) -> str:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M") if value is not None else "—"

    def _make_tray(self):
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu(self)
        show = QAction("显示窗口", self)
        show.triggered.connect(self.restore_window)
        refresh = QAction("立即刷新", self)
        refresh.triggered.connect(self.refresh_now)
        quit_action = QAction("退出程序", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show)
        menu.addAction(refresh)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.setToolTip("Steam Market Sentinel")
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.restore_window()

    def restore_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def log(self, text: str):
        self.statusBar().showMessage(text[:240], 15000)

    def filter_rows(self):
        needle = self.filter_text.text().casefold().strip()
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            self.table.setRowHidden(row, bool(needle and needle not in cell.text().casefold()))

    def refresh_table(self):
        keep = self.current_item_id
        records = self.db.list_items(self.settings.currency)
        # Rebuilding every cell is simplest, but it must not flicker or make the
        # list jump back to the top while the poller keeps refreshing.
        scroll = self.table.verticalScrollBar().value()
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(records))
        keep_row = None
        for r, item in enumerate(records):
            state = "暂停" if not item["enabled"] else (
                "异常" if item["last_error"] else ("监控中" if item["current_cents"] is not None else "待检测"))
            columns = (item["display_name"], cents_text(item["current_cents"], self.settings.currency),
                       cents_text(item["target_cents"], self.settings.currency),
                       cents_text(item["low_cents"], self.settings.currency), state)
            for c, value in enumerate(columns):
                cell = QTableWidgetItem(value)
                if c in (1, 2, 3):
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 0:
                    cell.setData(Qt.UserRole, item["id"])
                    cell.setToolTip(f"{item['appid']} · {item['hash_name']}")
                if c == 4:
                    cell.setForeground(QColor(STATE_COLORS.get(state, PALETTE["text_muted"])))
                    if state == "异常":
                        cell.setToolTip(item["last_error"] or "")
                self.table.setItem(r, c, cell)
            if item["id"] == keep:
                keep_row = r
        if keep_row is None and records:
            keep_row = 0
        if keep_row is not None:
            self.table.selectRow(keep_row)
        else:
            self.current_item_id = None
            self.item_title.setText("选择一个监控商品")
            self.item_game.setText("点击左侧商品查看详情")
            self.item_last.clear()
            self.price_metric.setText("—")
            self.low_metric.setText("—")
            self.target_metric.setText("—")
            self.image.setPixmap(QPixmap())
            self.image.setText("📦")
            self._chart_key = None
            self.history_hint.setText("选择一件商品后可查看本地采样曲线与 Steam 官方成交历史。")
            self.draw_chart([], "尚无数据")
        self.table.setUpdatesEnabled(True)
        self.table.verticalScrollBar().setValue(scroll)
        self.table.blockSignals(False)
        self.count_label.setText(f"{len(records)} 件")
        self.filter_rows()
        if keep_row is not None:
            self.on_selection_changed()

    def schedule_refresh(self, item_id: int, currency: str):
        if currency != self.settings.currency:
            return
        if not self._pending_refresh:
            self._pending_refresh = True
            QTimer.singleShot(350, self.flush_refresh)

    def flush_refresh(self):
        self._pending_refresh = False
        self.refresh_table()

    def on_selection_changed(self):
        row = self.table.currentRow()
        if row < 0:
            return
        cell = self.table.item(row, 0)
        if cell is None:
            return
        item_id = cell.data(Qt.UserRole)
        if item_id is None:
            return
        changed = item_id != self.current_item_id
        self.current_item_id = item_id
        item = self.db.get_item(item_id, self.settings.currency)
        if item is None:
            return
        self.item_title.setText(item["display_name"])
        self.item_game.setText(f"{item['app_name'] or 'Steam Community Market'} · AppID {item['appid']}")
        last = item["currency_checked_at"]
        checked = item["last_checked_at"]
        # 最后检查 updates every successful poll; 最后采样 only moves when a row was
        # actually written (price change or the 6-hour heartbeat). Without the
        # former the panel looks frozen for hours on an unchanged price.
        timeline = "最后检查：" + (self._stamp(checked) if checked is not None else "尚未成功获取")
        if last is not None and last != checked:
            timeline += f"　·　最后采样：{self._stamp(last)}"
        self.item_last.setText(timeline)
        self.price_metric.setText(cents_text(item["current_cents"], self.settings.currency))
        self.low_metric.setText(cents_text(item["low_cents"], self.settings.currency))
        self.target_metric.setText(cents_text(item["target_cents"], self.settings.currency))
        self.target_check.setChecked(item["target_cents"] is not None)
        self.target_edit.setValue((item["target_cents"] or 0) / 100)
        self.low_check.setChecked(bool(item["new_low_enabled"]))
        self.enabled_check.setChecked(bool(item["enabled"]))
        if changed:
            self._load_image(item.get("image_url") or "", item_id)
            self.settings.selected_item = item_id
        self.ensure_thumbnail(item)
        self.refresh_chart()

    def ensure_thumbnail(self, item: dict) -> None:
        """Fetch a missing thumbnail once per item, off the GUI thread.

        Items added by pasting a link have no icon (search-based adds do), so the
        listing page's og:image fills it in and it is then stored in the database.
        Failures are silent: a missing thumbnail is cosmetic and must not surface as
        an error, nor be retried in a loop.
        """
        item_id = item["id"]
        if item.get("image_url") or item_id in self._thumb_tried:
            return
        self._thumb_tried.add(item_id)
        self._run_image_task(
            lambda: SteamMarketClient(retries=0).icon_url(item["appid"], item["hash_name"]),
            lambda url: self.on_thumbnail(item_id, url))

    def on_thumbnail(self, item_id: int, url: str):
        if not url:
            return
        self.db.set_item_image(item_id, url)
        if self.current_item_id == item_id:
            self._load_image(url, item_id)

    def _run_image_task(self, callback, on_success):
        """Run an image fetch off the GUI thread.

        Images deliberately do **not** go through Qt's network stack: this process
        already uses `requests` (Python's OpenSSL) for every Steam call, and mixing
        the two segfaults on Windows — QNetworkAccessManager's HTTPS crashes as soon
        as Python's ssl has been used. `requests` in a worker thread is safe.

        Finished threads are only pruned on a later call: dropping the last reference
        *inside* a `finished` slot crashes, because Qt emits that signal while the
        thread is still winding down.
        """
        self._image_tasks = [task for task in self._image_tasks if not task.isFinished()]
        task = BackgroundCall(callback)
        task.succeeded.connect(on_success)
        task.failed.connect(lambda _error: None)  # a missing image is not an error
        self._image_tasks.append(task)
        task.start()

    def _load_image(self, address: str, item_id: int):
        self.image.setPixmap(QPixmap())
        self.image.setText("📦")
        cached = self._image_cache.get(address)
        if cached is not None:
            self.image.setPixmap(cached)
            self.image.setText("")
            return
        parsed = QUrl(address)
        if not address or parsed.scheme() != "https" or not parsed.host().endswith(".steamstatic.com"):
            return
        self._run_image_task(lambda: _fetch_image_bytes(address),
                             lambda data: self.on_image_bytes(address, item_id, data))

    def on_image_bytes(self, address: str, item_id: int, data: bytes):
        pix = QPixmap()
        if not data or not pix.loadFromData(data):
            return
        scaled = pix.scaled(84, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if len(self._image_cache) > 256:  # keep the cache bounded
            self._image_cache.clear()
        self._image_cache[address] = scaled
        if self.current_item_id == item_id:
            self.image.setPixmap(scaled)
            self.image.setText("")

    def draw_chart(self, points: list[tuple[int, float]], title: str, symbol: str = ""):
        chart = QChart()
        chart.setBackgroundBrush(QBrush(QColor(PALETTE["panel"])))
        chart.setBackgroundRoundness(0)
        chart.setTitle(title)
        chart.setTitleBrush(QBrush(QColor(PALETTE["text_muted"])))
        chart.legend().hide()
        if points:
            line = QLineSeries()
            line.setPen(QPen(QColor(PALETTE["accent"]), 2))
            line.setPointsVisible(len(points) <= SPARSE_SERIES_MARKERS)
            for stamp, amount in points:
                line.append(stamp * 1000, amount)
            # 悬停在数据点上时弹出该点的时间与价格。
            line.hovered.connect(
                lambda point, state, unit=symbol: self._on_point_hovered(point, state, unit))
            chart.addSeries(line)
            x = QDateTimeAxis()
            x.setFormat("MM/dd HH:mm" if len(points) <= 15 else "yyyy/MM/dd")
            x.setLabelsColor(QColor(PALETTE["text_muted"]))
            y = QValueAxis()
            y.setLabelFormat("%.2f")
            y.setLabelsColor(QColor(PALETTE["text_muted"]))
            x_min, x_max = min(p[0] for p in points), max(p[0] for p in points)
            if x_min == x_max:
                x_min -= 3600
                x_max += 3600
            x.setRange(QDateTime.fromSecsSinceEpoch(x_min), QDateTime.fromSecsSinceEpoch(x_max))
            prices = [p[1] for p in points]
            p_min, p_max = min(prices), max(prices)
            # A tight range is what makes small moves visible; 8% headroom keeps
            # the extremes off the frame without wasting plot height.
            padding = max((p_max - p_min) * 0.08, p_max * 0.01, 0.01)
            y.setRange(max(0, p_min - padding), p_max + padding)
            chart.addAxis(x, Qt.AlignBottom)
            chart.addAxis(y, Qt.AlignLeft)
            line.attachAxis(x)
            line.attachAxis(y)
        self.chart.setChart(chart)

    @staticmethod
    def _on_point_hovered(point, state: bool, symbol: str):
        """Show/hide the tooltip for a hovered chart point."""
        if not state:
            QToolTip.hideText()
            return
        QToolTip.showText(QCursor.pos(),
                          point_tooltip_text(symbol, int(point.x() / 1000), point.y()))

    def refresh_chart(self):
        """Redraw the chart for the selected item.

        There is one view: the observed series, which already *includes* any imported
        official history (`Database.get_history`). The old local/official mode switch
        became redundant once the two were merged, so it is gone.
        """
        if self.current_item_id is None:
            return
        item = self.db.get_item(self.current_item_id, self.settings.currency)
        if item is None:
            return
        history = self.db.get_history(item["id"], self.settings.currency)
        local_count, imported_count = self.db.merged_point_counts(item["id"],
                                                                  self.settings.currency)
        # Polling refreshes this view every few hundred ms; rebuilding an identical
        # chart each time is pure flicker.
        key = (item["id"], self.settings.currency, len(history),
               history[-1][0] if history else 0, imported_count)
        if key == self._chart_key:
            return
        self._chart_key = key
        title = f"本地最低在售价格 · {self.settings.currency}"
        if imported_count:
            # Name both quantities: the line is no longer only local samples.
            title = f"最低挂单价 + 官方成交中位价 · {self.settings.currency}"
        self.draw_chart([(stamp, amount / 100) for stamp, amount in history],
                        title if history else "尚无记录",
                        CURRENCIES[self.settings.currency][2])
        if imported_count:
            hint = (f"{len(history)} 个采样点 = 本地 {local_count} + 导入的官方成交中位价 "
                    f"{imported_count}")
        elif history:
            hint = (f"{len(history)} 个采样点 · 只记录开始监控后的最低挂单价：价格变化时记录，"
                    "价格未变时每 6 小时记录一次——短时间内曲线不变是正常的。")
        else:
            hint = ("尚无采样点 · 本地采样在价格变化时记录、未变化时每 6 小时记录一次；"
                    "也可以点「导入官方历史…」从浏览器导入该商品的官方成交历史。")
        self.history_hint.setText(hint)

    def import_official_history(self):
        if self.current_item_id is None:
            QMessageBox.information(self, "请先选择商品", "请先在上方清单中选中要导入的商品。")
            return
        item = self.db.get_item(self.current_item_id, self.settings.currency)
        if item is None:
            return
        dialog = ImportHistoryDialog(self, item["display_name"], self.settings.currency,
                                     listing_url(item["appid"], item["hash_name"]))
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            points, label = dialog.parsed()
        except ValueError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        symbol = label.split("…")[0] if label else ""
        if symbol and symbol != CURRENCIES[self.settings.currency][2]:
            # Refuse rather than merge: mixing two currencies into one line would be
            # a silent lie, and with a single merged view there is nowhere else to
            # show it — so storing it would mean storing something invisible.
            QMessageBox.warning(
                self, "币种不一致",
                f"这份数据是 {label} 计价，而当前设置是 {self.settings.currency}。\n"
                "请先在设置里把报价币种切换为对应币种（或在浏览器里重新取一次该币种的数据），再导入。")
            return
        try:
            count = self.db.save_official_history(item["id"], points, label,
                                                 self.settings.currency)
        except (ValueError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        # The imported points are part of the observed line now, so just redraw it.
        self._chart_key = None
        self.refresh_chart()
        self.log(f"已导入 {count} 个官方成交历史数据点，已并入观测序列：{item['display_name']}")

    def add_item(self):
        dialog = SearchAddDialog(self, self.settings.currency)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            item, target = dialog.chosen()
            self.current_item_id = self.db.add_item(item, target)
        except sqlite3.IntegrityError:
            QMessageBox.information(self, "已存在", "该 AppID + market_hash_name 已在监控清单中。")
            return
        except ValueError as exc:
            QMessageBox.warning(self, "无法添加", str(exc))
            return
        self.refresh_table()
        self.log("已添加 " + item.display_name)
        if self.worker and self.worker.isRunning():
            self.worker.refresh_now()

    def remove_item(self):
        if self.current_item_id is None:
            return
        if QMessageBox.question(self, "移除商品", "移除该商品及其全部本地历史记录？") != QMessageBox.Yes:
            return
        self.db.delete_item(self.current_item_id)
        self.current_item_id = None
        self.refresh_table()

    def save_rule(self):
        if self.current_item_id is None:
            return
        target = from_amount(self.target_edit.value()) if self.target_check.isChecked() else None
        self.db.update_item(self.current_item_id, target, self.low_check.isChecked(),
                            self.enabled_check.isChecked())
        self.refresh_table()
        self.log("监控规则已保存；目标价按当前选择币种填写")
        if self.worker and self.worker.isRunning():
            self.worker.refresh_now()

    def start_monitor(self):
        if self.worker and self.worker.isRunning():
            return
        self.worker = MonitorWorker(self.db.path, replace(self.settings))
        self.worker.updated.connect(self.schedule_refresh)
        self.worker.alert_found.connect(self.notify_alert)
        self.worker.message.connect(self.log)
        self.worker.cycle_complete.connect(self.flush_refresh)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()
        self.run_button.setText("■ 暂停监控")

    def on_worker_finished(self):
        self.run_button.setText("▶ 开始监控")

    def stop_monitor(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.run_button.setText("正在停止…")

    def toggle_monitor(self):
        if self.worker and self.worker.isRunning():
            self.stop_monitor()
        else:
            self.start_monitor()

    def refresh_now(self):
        if self.worker and self.worker.isRunning():
            self.worker.refresh_now()
            self.log("本轮结束后将立即再次刷新")
        else:
            self.start_monitor()

    def notify_alert(self, alert: Alert):
        subject, body = render_alert(alert)
        self.log(subject)
        if self.settings.desktop_enabled:
            if self.tray and self.tray.supportsMessages():
                self.tray.showMessage(subject, body.splitlines()[0],
                                      QSystemTrayIcon.Information, 15000)
            else:
                self.log(subject + "（当前系统不支持托盘弹窗）")

    def open_settings(self):
        dialog = SettingsDialog(self, self.settings)
        if dialog.exec() != QDialog.Accepted:
            return
        new_settings = dialog.chosen()
        if new_settings.currency != self.settings.currency:
            QMessageBox.information(self, "切换币种", "不同币种的历史数据互相独立。\n"
                                    "已有目标价数值不会自动换算，请重新设定相应阈值。")
        self.settings = new_settings
        self.settings.save(self.settings_path)
        self.target_edit.setPrefix(f"{CURRENCIES[self.settings.currency][2]} ")
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            # Restart when previous worker ends; don't overlap two polling loops.
            old = self.worker
            old.finished.connect(self.start_monitor)
        self.refresh_table()
        self.log("设置已保存")

    def import_watchlist(self):
        filename, _ = QFileDialog.getOpenFileName(self, "导入 JSON 监控清单", "", "JSON (*.json)")
        if not filename:
            return
        try:
            added, skipped = self.db.import_watchlist(Path(filename))
            self.refresh_table()
            self.log(f"导入成功 {added} 件，跳过 {skipped} 件")
            if self.worker and self.worker.isRunning():
                self.worker.refresh_now()
        except (ValueError, OSError, TypeError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def export_watchlist(self):
        filename, _ = QFileDialog.getSaveFileName(self, "导出监控清单", "watchlist.json", "JSON (*.json)")
        if filename:
            try:
                count = self.db.export_watchlist(Path(filename))
                self.log(f"已导出 {count} 件监控商品（不含凭据）")
            except OSError as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def export_history(self):
        filename, _ = QFileDialog.getSaveFileName(self, "导出历史数据", "prices.csv", "CSV (*.csv)")
        if filename:
            try:
                self.db.export_history_csv(Path(filename), self.settings.currency)
                self.log("CSV 已保存（只包含当前币种的最低挂单采样）")
            except OSError as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def closeEvent(self, event):
        if not self._quitting and self.settings.minimize_to_tray and self.tray:
            event.ignore()
            self.hide()
            self.tray.showMessage("Steam Market Sentinel", "已最小化到托盘，继续监控价格。",
                                  QSystemTrayIcon.Information, 3000)
        else:
            self.quit_app()
            event.accept()

    def quit_app(self):
        self._quitting = True
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(60000)
        for task in list(self._image_tasks):
            if task.isRunning():
                task.wait(5000)
        if self.tray:
            self.tray.hide()
        try:
            self.settings.save(self.settings_path)  # remember the view state
        except OSError:
            pass
        QApplication.instance().quit()

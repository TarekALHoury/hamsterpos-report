from __future__ import annotations

import calendar
import ctypes
import json
import logging
import os
import queue
import re
import sys
import threading
import time
from ctypes import wintypes
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import font as tkfont, messagebox, ttk
from typing import Any

import customtkinter as ctk
import pymysql
import arabic_reshaper
from bidi.algorithm import get_display as bidi_display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from report_sql import (CLOSE_CASH_COLUMNS, CLOSE_CASH_SQL, PURCHASES_SQL,
                        PURCHASE_COLUMNS, SALES_SQL, SALES_COLUMNS)
from services.notification_service import NotificationService
from services.startup_service import is_frozen_executable, set_start_with_windows
from services.subscription_monitor import SubscriptionMonitor
from services.tray_service import TrayService
from storage.notification_history import NotificationHistory
from subscription_sql import SUBSCRIPTION_REPORT_COLUMNS, SUBSCRIPTION_REPORT_SQL

APP_NAME = "HamsterPOS Reports"
APP_VERSION = "6.9"
APP_DIR = Path(os.getenv("APPDATA", Path.home())) / "HamsterPOSReports"
CONFIG_FILE = APP_DIR / "settings.json"
CONFIG_LOAD_WARNING: str | None = None
MONEY_COLUMNS = {"buy_price", "sell_price", "sales", "total_buy_price", "amount",
                 "total_sell_price", "total_sold", "total_bought"}
PURCHASE_REASONS = {
    "All reasons": None,
    "Purchase - Supplier": 1,
    "Return - Supplier": -2,
    "Adjust - Add": 4,
    "Adjust - Minus": -4,
    "Subtract": -8,
    "Breakage": -3,
    "Free": -6,
    "Sample - Out": -5,
    "Used": -7,
    "Transfer": 1000,
}


def resource_path(relative_path: str) -> Path:
    """Resolve bundled PyInstaller assets and source-run assets."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect(value: str) -> str:
    """Encrypt a secret for the current Windows user using DPAPI."""
    if not value:
        return ""
    import base64
    in_blob, keepalive = _blob(value.encode("utf-8"))
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def unprotect(value: str) -> str:
    if not value:
        return ""
    import base64
    in_blob, keepalive = _blob(base64.b64decode(value))
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def load_config() -> dict[str, Any]:
    global CONFIG_LOAD_WARNING
    CONFIG_LOAD_WARNING = None
    defaults = {
        "host": "localhost", "port": 3306, "database": "", "username": "",
        "password": "", "purchase_reason": "1", "currency": "$",
        "appearance": "Dark", "subscription_notifications_enabled": True,
        "start_with_windows": False, "subscription_notify_days": 2,
        "subscription_check_minutes": 60,
    }
    if not CONFIG_FILE.exists():
        return defaults
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        saved["password"] = unprotect(saved.pop("password_protected", ""))
        return defaults | saved
    except Exception:
        CONFIG_LOAD_WARNING = (
            "Couldn't read the saved settings. The app is using defaults; "
            "please open Database settings and save the connection again."
        )
        logging.getLogger(__name__).exception("Could not load saved settings")
        return defaults


def save_config(config: dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(config)
    payload["password_protected"] = protect(payload.pop("password", ""))
    CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def db_connect(config: dict[str, Any]):
    return pymysql.connect(
        host=config["host"], port=int(config["port"]), user=config["username"],
        password=config["password"], database=config["database"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=8, read_timeout=60,
        autocommit=True,
    )


class DatePicker(ctk.CTkFrame):
    def __init__(self, parent, initial: date, callback):
        super().__init__(parent, fg_color=("#ffffff", "#172033"), corner_radius=12,
                         border_width=1, border_color=("#cbd5e1", "#40506a"))
        self.callback, self.year, self.month = callback, initial.year, initial.month
        self.selected_date = initial
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(padx=14, pady=14)
        self.build_calendar()
        self.update_calendar()
        self._outside_binding = self.winfo_toplevel().bind("<Button-1>", self.on_outside_click, add="+")

    def build_calendar(self):
        header = ctk.CTkFrame(self.body, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 8))
        ctk.CTkButton(header, text="<<", width=34, command=lambda: self.move_year(-1)).pack(side="left", padx=(0, 2))
        ctk.CTkButton(header, text="<", width=34, command=lambda: self.move(-1)).pack(side="left")
        self.month_label = ctk.CTkLabel(header, text="", font=("Segoe UI", 15, "bold"), width=150)
        self.month_label.pack(side="left")
        ctk.CTkButton(header, text=">", width=34, command=lambda: self.move(1)).pack(side="left")
        ctk.CTkButton(header, text=">>", width=34, command=lambda: self.move_year(1)).pack(side="left", padx=(2, 0))
        for col, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            ctk.CTkLabel(self.body, text=name, width=34, text_color=("#52647c", "#9fb0c8")).grid(row=1, column=col)
        self.day_buttons = []
        for calendar_row in range(2, 8):
            self.body.grid_rowconfigure(calendar_row, minsize=32)
        for index in range(42):
            row, col = 2 + index // 7, index % 7
            button = ctk.CTkButton(self.body, text="", width=34, height=30,
                                   fg_color="transparent", hover_color="#1f6aa5",
                                   text_color=("#172033", "#f1f5f9"))
            button.grid(row=row, column=col, padx=1, pady=1)
            self.day_buttons.append(button)

    def update_calendar(self):
        self.month_label.configure(text=f"{calendar.month_name[self.month]} {self.year}")
        days = [day for week in calendar.monthcalendar(self.year, self.month) for day in week]
        days.extend([0] * (42 - len(days)))
        for button, day in zip(self.day_buttons, days):
            if day:
                is_selected = (
                    self.year == self.selected_date.year
                    and self.month == self.selected_date.month
                    and day == self.selected_date.day
                )
                button.configure(
                    text=str(day),
                    command=lambda d=day: self.pick(d),
                    fg_color="#1f6aa5" if is_selected else "transparent",
                    hover_color="#2583c5" if is_selected else ("#d8e8f5", "#29405f"),
                    text_color="#ffffff" if is_selected else ("#172033", "#f1f5f9"),
                    border_width=1 if is_selected else 0,
                    border_color="#7dd3fc" if is_selected else ("#ffffff", "#172033"),
                )
                button.grid()
            else:
                button.grid_remove()

    def move(self, delta):
        month = self.month + delta
        self.year += (month - 1) // 12
        self.month = (month - 1) % 12 + 1
        self.update_calendar()

    def move_year(self, delta):
        self.year += delta
        self.update_calendar()

    def pick(self, day):
        self.callback(date(self.year, self.month, day))
        self.close()

    def on_outside_click(self, event):
        widget = event.widget
        if widget is getattr(self.owner_field, "calendar_button", None):
            return
        try:
            left, top = self.winfo_rootx(), self.winfo_rooty()
            right, bottom = left + self.winfo_width(), top + self.winfo_height()
            if left <= event.x_root <= right and top <= event.y_root <= bottom:
                return
        except Exception:
            pass
        self.close()

    def close(self):
        app = self.winfo_toplevel()
        if getattr(app, "active_calendar", None) is self:
            app.active_calendar = None
        if getattr(self, "_outside_binding", None):
            app.unbind("<Button-1>", self._outside_binding)
            self._outside_binding = None
        self.destroy()


class DateTimeField(ctk.CTkFrame):
    def __init__(self, parent, label: str, initial: datetime, on_user_change=None):
        super().__init__(parent, fg_color="transparent")
        self.on_user_change = on_user_change
        self.value_date = initial.date()
        ctk.CTkLabel(self, text=label, text_color=("#475569", "#9aa9bd")).pack(anchor="w")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack()
        self.date_var = ctk.StringVar(value=self.value_date.strftime("%m-%d-%y"))
        self.date_entry = ctk.CTkEntry(row, textvariable=self.date_var, width=100)
        self.date_entry.pack(side="left", padx=(0, 4))
        self.calendar_button = ctk.CTkButton(row, text="▦", width=36, command=self.open_picker)
        self.calendar_button.pack(side="left", padx=(0, 6))
        self.hour = ctk.CTkEntry(row, width=38); self.hour.insert(0, f"{initial.hour:02d}"); self.hour.pack(side="left")
        ctk.CTkLabel(row, text=":").pack(side="left")
        self.minute = ctk.CTkEntry(row, width=38); self.minute.insert(0, f"{initial.minute:02d}"); self.minute.pack(side="left")
        for entry in (self.date_entry, self.hour, self.minute):
            entry.bind("<KeyPress>", self.user_changed)
            entry.bind("<<Paste>>", self.user_changed)

    def user_changed(self, _event=None):
        if self.on_user_change:
            self.on_user_change()

    def open_picker(self):
        try: current = datetime.strptime(self.date_var.get(), "%m-%d-%y").date()
        except ValueError: current = self.value_date
        app = self.winfo_toplevel()
        active = getattr(app, "active_calendar", None)
        if active is not None and active.winfo_exists():
            same_field = getattr(active, "owner_field", None) is self
            active.close()
            if same_field:
                return
        picker = DatePicker(app, current, self.set_date_from_picker)
        picker.owner_field = self
        app.active_calendar = picker
        app.update_idletasks()
        x = self.calendar_button.winfo_rootx() - app.winfo_rootx()
        y = self.calendar_button.winfo_rooty() - app.winfo_rooty() + self.calendar_button.winfo_height() + 4
        picker.place(x=max(8, min(x, app.winfo_width() - 340)), y=y)
        picker.lift()

    def set_date(self, value):
        self.value_date = value
        self.date_var.set(value.strftime("%m-%d-%y"))

    def set_date_from_picker(self, value):
        self.user_changed()
        self.set_date(value)

    def set_datetime(self, value: datetime):
        self.set_date(value.date())
        self.hour.delete(0, "end"); self.hour.insert(0, f"{value.hour:02d}")
        self.minute.delete(0, "end"); self.minute.insert(0, f"{value.minute:02d}")

    def get(self) -> datetime:
        d = datetime.strptime(self.date_var.get().strip(), "%m-%d-%y")
        hour, minute = int(self.hour.get()), int(self.minute.get())
        if not 0 <= hour <= 23 or not 0 <= minute <= 59: raise ValueError("Time must be between 00:00 and 23:59")
        return d.replace(hour=hour, minute=minute, second=0)


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, config, on_saved):
        super().__init__(parent)
        self.config_data, self.on_saved = config, on_saved
        self.title("Settings")
        self.geometry("540x790")
        self.resizable(False, False)
        self.transient(parent); self.grab_set()
        ctk.CTkLabel(self, text="Database connection", font=("Segoe UI", 24, "bold")).pack(anchor="w", padx=30, pady=(28, 4))
        ctk.CTkLabel(self, text="Saved locally; the password is encrypted for your Windows account.", text_color="#8fa0b7").pack(anchor="w", padx=30, pady=(0, 18))
        form = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        form.pack(fill="both", expand=True, padx=18)
        self.entries = {}
        fields = (("host", "Host"), ("port", "Port"), ("username", "Username"), ("password", "Password"), ("database", "Database"), ("currency", "Currency symbol"), ("purchase_reason", "Purchase movement reason (advanced)"))
        for key, label in fields:
            ctk.CTkLabel(form, text=label).pack(anchor="w", padx=12, pady=(8, 3))
            if key == "database":
                database_row = ctk.CTkFrame(form, fg_color="transparent")
                database_row.pack(fill="x", padx=12)
                current_database = str(config.get(key, ""))
                entry = ctk.CTkComboBox(database_row, values=[current_database] if current_database else [""])
                entry.set(current_database)
                entry.pack(side="left", fill="x", expand=True)
                self.load_databases_btn = ctk.CTkButton(
                    database_row, text="Load databases", width=120,
                    fg_color="#334155", command=self.load_databases,
                )
                self.load_databases_btn.pack(side="left", padx=(8, 0))
            else:
                entry = ctk.CTkEntry(form, show="•" if key == "password" else "")
                entry.insert(0, str(config.get(key, ""))); entry.pack(fill="x", padx=12)
            self.entries[key] = entry
        ctk.CTkLabel(form, text="Subscription notifications",
                     font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=12, pady=(24, 8))
        self.notification_enabled_var = ctk.BooleanVar(
            value=bool(config.get("subscription_notifications_enabled", True)))
        self.start_with_windows_var = ctk.BooleanVar(
            value=bool(config.get("start_with_windows", False)))
        ctk.CTkCheckBox(
            form, text="Enable subscription expiry notifications",
            variable=self.notification_enabled_var,
        ).pack(anchor="w", padx=12, pady=5)
        ctk.CTkCheckBox(
            form, text="Start with Windows (silent in system tray)",
            variable=self.start_with_windows_var,
        ).pack(anchor="w", padx=12, pady=5)
        for key, label, default in (
            ("subscription_notify_days", "Notify before (days)", 2),
            ("subscription_check_minutes", "Check every (minutes)", 60),
        ):
            ctk.CTkLabel(form, text=label).pack(anchor="w", padx=12, pady=(10, 3))
            entry = ctk.CTkEntry(form)
            entry.insert(0, str(config.get(key, default)))
            entry.pack(fill="x", padx=12)
            self.entries[key] = entry
        self.status = ctk.CTkLabel(self, text="", text_color="#f0aa5b", height=24); self.status.pack(pady=(6, 0))
        buttons = ctk.CTkFrame(self, fg_color="transparent"); buttons.pack(fill="x", padx=30, pady=(4, 18))
        ctk.CTkButton(buttons, text="Test connection", fg_color="#334155", command=self.test).pack(side="left")
        ctk.CTkButton(buttons, text="Save settings", command=self.save).pack(side="right")
        if config.get("host") and config.get("username"):
            self.after(250, self.load_databases)

    def values(self):
        values = {key: entry.get().strip() for key, entry in self.entries.items()}
        values["port"] = int(values["port"])
        values["subscription_notify_days"] = int(values["subscription_notify_days"])
        values["subscription_check_minutes"] = int(values["subscription_check_minutes"])
        if values["subscription_notify_days"] < 0:
            raise ValueError("Notify-before days cannot be negative.")
        if values["subscription_check_minutes"] < 1:
            raise ValueError("Check interval must be at least 1 minute.")
        values["subscription_notifications_enabled"] = self.notification_enabled_var.get()
        values["start_with_windows"] = self.start_with_windows_var.get()
        return values

    def test(self):
        try:
            with db_connect(self.values()) as conn:
                with conn.cursor() as cur: cur.execute("SELECT 1")
            self.status.configure(text="Connection successful", text_color="#3ecf8e")
        except Exception as exc: self.status.configure(text=f"Connection failed: {exc}", text_color="#ff6b6b")

    def load_databases(self):
        """Load databases accessible with the entered server credentials."""
        try:
            host = self.entries["host"].get().strip()
            port = int(self.entries["port"].get().strip())
            username = self.entries["username"].get().strip()
            password = self.entries["password"].get()
        except ValueError:
            self.status.configure(text="Port must be a number", text_color="#ff6b6b")
            return

        self.load_databases_btn.configure(state="disabled", text="Loading...")
        self.status.configure(text="Connecting to server...", text_color="#f0aa5b")

        def worker():
            try:
                connection = pymysql.connect(
                    host=host, port=port, user=username, password=password,
                    charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=8, read_timeout=30, autocommit=True,
                )
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SHOW DATABASES")
                        databases = [next(iter(row.values())) for row in cursor.fetchall()]
                self.after(0, lambda: self.finish_loading_databases(databases, None))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda: self.finish_loading_databases([], error))

        threading.Thread(target=worker, daemon=True).start()

    def finish_loading_databases(self, databases, error):
        if not self.winfo_exists():
            return
        self.load_databases_btn.configure(state="normal", text="Load databases")
        if error:
            self.status.configure(text=f"Could not load databases: {error}", text_color="#ff6b6b")
            return
        current = self.entries["database"].get().strip()
        choices = databases or ([current] if current else [""])
        self.entries["database"].configure(values=choices)
        if current not in choices and choices:
            self.entries["database"].set(choices[0])
        self.status.configure(text=f"{len(databases)} databases available", text_color="#3ecf8e")

    def save(self):
        try:
            values = self.values()
            set_start_with_windows(values["start_with_windows"])
            save_config(values)
            self.config_data.clear(); self.config_data.update(values)
            self.on_saved(); self.destroy()
        except Exception as exc: messagebox.showerror(APP_NAME, str(exc), parent=self)


class ReportApp(ctk.CTk):
    def __init__(self, start_hidden=False):
        super().__init__()
        self.start_hidden = start_hidden
        self.exiting = False
        self.background_events = queue.Queue()
        if start_hidden:
            self.withdraw()
        APP_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=APP_DIR / "app.log",
            level=logging.WARNING,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        self.config_data = load_config()
        ctk.set_appearance_mode(self.config_data.get("appearance", "Dark")); ctk.set_default_color_theme("blue")
        self.title(f"{APP_NAME} — v{APP_VERSION}")
        try: self.iconbitmap(str(resource_path("assets/app_icon.ico")))
        except Exception: pass
        self.geometry("1380x820"); self.minsize(1050, 680)
        self.rows = []; self.categories = {"All categories": None}; self.cash_sequences = {}
        self.report_rows_cache = {"sales": [], "purchases": [], "cash": [], "subscriptions": []}
        self.report_has_run = {"sales": False, "purchases": False, "cash": False, "subscriptions": False}
        self.render_generation = 0
        self.active_calendar = None
        self.report_filter_states = {}
        self.end_time_live = True
        self.suspend_live_filters = False
        self.live_filter_job = None
        self.query_generation = 0
        self.report_type = ctk.StringVar(value="sales"); self.sort_mode = ctk.StringVar(value="Date: newest first")
        self.group_categories = ctk.BooleanVar(value=False)
        self.build_ui()
        if CONFIG_LOAD_WARNING:
            self.status.configure(text=CONFIG_LOAD_WARNING, text_color="#ff6b6b")
            if not start_hidden:
                self.after(100, lambda warning=CONFIG_LOAD_WARNING: messagebox.showwarning(
                    APP_NAME, warning, parent=self))
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.setup_background_services()
        self._theme_sync_job = None
        self._style_table(ctk.get_appearance_mode() == "Dark")
        if self.config_data.get("appearance") == "System":
            self._theme_sync_job = self.after(100, self.sync_system_table_theme)
        self.after(1000, self.update_live_end_time)
        if not self.config_data.get("database") and not start_hidden:
            self.after(250, lambda: self.open_settings())
        else: self.after(150, self.load_categories)

    def setup_background_services(self):
        self.notification_history = NotificationHistory(
            APP_DIR / "subscription_notifications.db")
        self.notification_service = NotificationService(
            APP_NAME, resource_path("assets/app_icon.png"))
        self.subscription_monitor = SubscriptionMonitor(
            config_provider=lambda: dict(self.config_data),
            connection_factory=db_connect,
            notifier=self.notification_service,
            history=self.notification_history,
            on_complete=lambda result: self.background_events.put(
                ("subscription_result", result)),
        )
        self.tray_service = TrayService(
            resource_path("assets/app_icon.ico"), APP_NAME,
            open_app=lambda: self.background_events.put(("action", self.show_report_window)),
            check_now=lambda: self.background_events.put(("action", self.check_subscriptions_now)),
            open_settings=lambda: self.background_events.put(("action", self.open_settings_from_tray)),
            exit_app=lambda: self.background_events.put(("action", self.exit_application)),
        )
        self.tray_service.start()
        self.subscription_monitor.start()
        self.after(150, self.process_background_events)
        if self.config_data.get("start_with_windows") and is_frozen_executable():
            try:
                set_start_with_windows(True)
            except OSError:
                pass

    def show_report_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def hide_to_tray(self):
        self.withdraw()

    def open_settings_from_tray(self):
        self.show_report_window()
        self.open_settings()

    def check_subscriptions_now(self):
        self.status.configure(text="Checking subscription expiries...", text_color="#f0aa5b")
        self.subscription_monitor.request_check()

    def subscription_check_completed(self, result):
        if self.exiting or not self.winfo_exists():
            return
        if result.get("error"):
            self.status.configure(
                text=f"Subscription check failed: {result['error']}", text_color="#ff6b6b")
        else:
            self.status.configure(
                text=(f"Subscriptions checked: {result.get('checked', 0)} · "
                      f"notifications: {result.get('notified', 0)}"),
                text_color="#3ecf8e")

    def process_background_events(self):
        if self.exiting:
            return
        while True:
            try:
                event_type, value = self.background_events.get_nowait()
            except queue.Empty:
                break
            if event_type == "action":
                value()
            elif event_type == "subscription_result":
                self.subscription_check_completed(value)
        if not self.exiting:
            self.after(150, self.process_background_events)

    def exit_application(self):
        if self.exiting:
            return
        self.exiting = True
        self.subscription_monitor.stop()
        self.tray_service.stop()
        self.destroy()

    def build_ui(self):
        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=("#edf2f7", "#101827")); sidebar.pack(side="left", fill="y"); sidebar.pack_propagate(False)
        ctk.CTkLabel(sidebar, text="HAMSTER", font=("Segoe UI", 12, "bold"), text_color="#38bdf8").pack(anchor="w", padx=24, pady=(30, 0))
        ctk.CTkLabel(sidebar, text="Reports", font=("Segoe UI", 28, "bold")).pack(anchor="w", padx=24, pady=(0, 32))
        self.nav_cash = ctk.CTkButton(sidebar, text="  Close Cash Movement", anchor="w", height=46, fg_color="transparent", text_color=("#172033", "#f1f5f9"), command=lambda: self.switch_report("cash")); self.nav_cash.pack(fill="x", padx=14, pady=4)
        self.nav_sales = ctk.CTkButton(sidebar, text="  Product Sales", anchor="w", height=46, text_color="#ffffff", command=lambda: self.switch_report("sales")); self.nav_sales.pack(fill="x", padx=14, pady=4)
        self.nav_purchases = ctk.CTkButton(sidebar, text="  Purchased Products", anchor="w", height=46, fg_color="transparent", text_color=("#172033", "#f1f5f9"), command=lambda: self.switch_report("purchases")); self.nav_purchases.pack(fill="x", padx=14, pady=4)
        self.nav_subscriptions = ctk.CTkButton(sidebar, text="  Subscription Expiry", anchor="w", height=46, fg_color="transparent", text_color=("#172033", "#f1f5f9"), command=lambda: self.switch_report("subscriptions")); self.nav_subscriptions.pack(fill="x", padx=14, pady=4)
        theme_box = ctk.CTkFrame(sidebar, fg_color="transparent")
        theme_box.pack(side="bottom", fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(theme_box, text="Appearance", text_color=("#475569", "#94a3b8")).pack(anchor="w", padx=10)
        self.theme_menu = ctk.CTkOptionMenu(theme_box, values=["Dark", "Light", "System"], command=self.change_theme)
        self.theme_menu.set(self.config_data.get("appearance", "Dark")); self.theme_menu.pack(fill="x", pady=4)
        ctk.CTkButton(sidebar, text="⚙  Database settings", anchor="w", fg_color="transparent", text_color=("#172033", "#f1f5f9"), hover_color=("#d8e2ef", "#263449"), command=self.open_settings).pack(side="bottom", fill="x", padx=14, pady=22)

        main = ctk.CTkFrame(self, corner_radius=0, fg_color=("#f7f9fc", "#0b1120")); main.pack(side="left", fill="both", expand=True)
        top = ctk.CTkFrame(main, fg_color="transparent"); top.pack(fill="x", padx=28, pady=(24, 12))
        self.title_label = ctk.CTkLabel(top, text="Product Sales Report", font=("Segoe UI", 25, "bold")); self.title_label.pack(side="left")
        self.export_btn = ctk.CTkButton(top, text="Export PDF", width=120, fg_color="#334155", command=self.export_pdf); self.export_btn.pack(side="right")

        filters = ctk.CTkFrame(main, fg_color=("#e8eef6", "#111b2e"), corner_radius=14); filters.pack(fill="x", padx=28, pady=8)
        now = datetime.now().replace(second=0, microsecond=0); midnight = now.replace(hour=0, minute=0)
        self.start_field = DateTimeField(filters, "Start date & time", midnight, self.live_date_filter_changed); self.start_field.grid(row=0, column=0, padx=18, pady=14, sticky="w")
        self.end_field = DateTimeField(filters, "End date & time", now, self.live_end_filter_changed); self.end_field.grid(row=0, column=1, padx=18, pady=14, sticky="w")
        box = ctk.CTkFrame(filters, fg_color="transparent"); box.grid(row=0, column=2, padx=18, pady=14, sticky="ew")
        self.search_label = ctk.CTkLabel(box, text="Product search", text_color=("#475569", "#9aa9bd"))
        self.search_label.pack(anchor="w")
        self.search_placeholder = "Barcode, name, or reference"
        self.search_var = ctk.StringVar(value="")
        search_holder = ctk.CTkFrame(box, height=28, fg_color="transparent")
        search_holder.pack(fill="x", anchor="w")
        search_holder.pack_propagate(False)
        self.search_entry = ctk.CTkEntry(search_holder, textvariable=self.search_var)
        self.search_entry.pack(fill="both", expand=True)
        self.search_has_focus = False
        self.search_hint = ctk.CTkLabel(
            search_holder, text=self.search_placeholder, height=20,
            fg_color=self.search_entry.cget("fg_color"),
            text_color=("#64748b", "#94a3b8"), anchor="w",
        )
        self.search_hint.place(x=8, rely=0.5, anchor="w")
        self.search_hint.bind("<Button-1>", self._focus_search_entry)
        self.search_entry.bind("<FocusIn>", self._search_focus_in)
        self.search_entry.bind("<FocusOut>", self._search_focus_out)
        self.search_var.trace_add("write", self._search_text_changed)
        filters.grid_columnconfigure(2, weight=1)

        self.cash_filters = ctk.CTkFrame(filters, fg_color="transparent")
        ctk.CTkLabel(self.cash_filters, text="Close cash sequence", text_color=("#475569", "#9aa9bd")).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ctk.CTkLabel(self.cash_filters, text="Movement", text_color=("#475569", "#9aa9bd")).grid(row=0, column=1, sticky="w")
        self.cash_menu = ctk.CTkOptionMenu(self.cash_filters, values=["No sequences found"], width=410, command=self.live_filter_changed)
        self.cash_menu.grid(row=1, column=0, padx=(0, 12))
        self.movement_menu = ctk.CTkOptionMenu(self.cash_filters, values=["All", "Sold", "Purchased"], width=140, command=self.live_filter_changed)
        self.movement_menu.set("All"); self.movement_menu.grid(row=1, column=1)

        filter2 = ctk.CTkFrame(main, fg_color="transparent"); filter2.pack(fill="x", padx=28, pady=7)
        self.category_menu = ctk.CTkOptionMenu(filter2, values=list(self.categories), width=170, command=self.live_filter_changed); self.category_menu.pack(side="left", padx=(0, 8))
        self.sort_menu = ctk.CTkOptionMenu(filter2, variable=self.sort_mode, values=["Date: newest first", "Date: oldest first", "Category A–Z", "Category Z–A"], width=165, command=self.live_filter_changed); self.sort_menu.pack(side="left")
        self.payment_menu = ctk.CTkOptionMenu(filter2, values=["All payment methods", "Cash", "Cheque", "Voucher", "Card", "Free", "Debt", "VIP Points", "Bank", "Slip", "Mobile", "Credit"], width=165, command=self.live_filter_changed)
        self.payment_menu.set("All payment methods"); self.payment_menu.pack(side="left", padx=(4, 10))
        self.sales_rank_menu = ctk.CTkOptionMenu(
            filter2,
            values=["Sales ranking: default", "Most sold by QTY", "Most repeated"],
            width=175,
            command=self.live_filter_changed,
        )
        self.sales_rank_menu.set("Sales ranking: default")
        self.sales_rank_menu.pack(side="left", padx=(0, 10))
        self.reason_menu = ctk.CTkOptionMenu(filter2, values=list(PURCHASE_REASONS), width=165, command=self.live_filter_changed)
        self.reason_menu.set("All reasons")
        self.subscription_status_menu = ctk.CTkOptionMenu(
            filter2, values=["All statuses", "Active", "Expired", "Ending soon"],
            width=150, command=self.live_filter_changed)
        self.subscription_status_menu.set("All statuses")
        self.subscription_days_menu = ctk.CTkOptionMenu(
            filter2, values=["Days: default", "Days: lowest first", "Days: highest first"],
            width=175, command=self.live_filter_changed)
        self.subscription_days_menu.set("Days: default")
        self.group_check = ctk.CTkCheckBox(filter2, text="Group by category", variable=self.group_categories, command=self.toggle_category_grouping, width=145)
        self.group_check.pack(side="left", padx=(2, 8))
        self.run_btn = ctk.CTkButton(filter2, text="Run Report", width=145, height=38, command=self.run_report); self.run_btn.pack(side="right")
        self.refresh_btn = ctk.CTkButton(filter2, text="↻", width=44, height=38, font=("Segoe UI", 22), fg_color=("#d8e2ef", "#334155"), text_color=("#172033", "#ffffff"), hover_color=("#c5d3e3", "#475569"), command=self.refresh_report)
        self.refresh_btn.pack(side="right", padx=10)
        self.loading_holder = ctk.CTkFrame(main, height=5, fg_color="transparent")
        self.loading_holder.pack(fill="x", padx=28, pady=(0, 2))
        self.loading_holder.pack_propagate(False)
        self.loading_bar = ctk.CTkProgressBar(self.loading_holder, height=3, corner_radius=0, mode="indeterminate")
        self.loading_bar.set(0)

        self.table_holder = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
        self.table_holder.pack(fill="both", expand=True, padx=28, pady=(8, 12))
        table_frame = ctk.CTkFrame(self.table_holder, width=900, height=600,
                                   fg_color=("#ffffff", "#111827"), corner_radius=14)
        self.table_frame = table_frame
        # Keep the outer frame as the viewport. Without this, the Treeview's
        # requested column width makes the frame grow beyond the window and
        # clips the horizontal scrollbar along with the hidden columns.
        table_frame.grid_propagate(False)
        table_frame.place(x=0, y=0, relheight=1)
        self.table_holder.bind("<Configure>", self.update_table_viewport, add="+")
        style = ttk.Style(self); style.theme_use("clam")
        style.configure("Report.Treeview", background="#111827", fieldbackground="#111827", foreground="#e5edf8", rowheight=34, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Report.Treeview.Heading", background="#1f2937", foreground="#a9b8cc", borderwidth=0, relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Report.Treeview", background=[("selected", "#075985")])
        self.tree = ttk.Treeview(table_frame, style="Report.Treeview", show="headings")
        self.tree.tag_configure("category_header", background="#1f6aa5", foreground="#ffffff", font=("Segoe UI", 12, "bold"))
        self.tree.tag_configure("category_total", background="#dbeafe", foreground="#12395b", font=("Segoe UI", 10, "bold"))
        # Purchase-cost increases are warnings (red); decreases are favorable (green).
        self.tree.tag_configure("price_up", background="#421b24", foreground="#fecdd3")
        self.tree.tag_configure("price_down", background="#12372a", foreground="#bbf7d0")
        self.tree.tag_configure("sale_discount", background="#421b24", foreground="#fecdd3")
        self.tree.tag_configure("sale_price_change", background="#12372a", foreground="#bbf7d0")
        self.tree.tag_configure("subscription_ending", background="#4a3b0c", foreground="#fde68a")
        self.tree.tag_configure("subscription_expired", background="#421b24", foreground="#fecdd3")
        vs = ctk.CTkScrollbar(table_frame, command=self.tree.yview)
        hs = ctk.CTkScrollbar(table_frame, orientation="horizontal", command=self.tree.xview)
        self.horizontal_scrollbar = hs
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0)); vs.grid(row=0, column=1, sticky="ns", pady=(8, 0)); hs.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(0, 8))
        self.empty_state = ctk.CTkLabel(
            table_frame,
            text="No results found\nTry changing or clearing your filters.",
            font=("Segoe UI", 15, "bold"),
            text_color=("#64748b", "#94a3b8"),
            justify="center",
        )
        self._column_resize_job = None
        self._column_base_widths = None
        self._last_table_width = 0
        self._last_resize_event = 0.0
        self.tree.bind("<ButtonRelease-1>", self.column_resize_finished, add="+")
        self.tree.bind("<Shift-MouseWheel>", self.scroll_table_horizontally, add="+")
        hs.bind("<MouseWheel>", self.scroll_table_horizontally, add="+")
        table_frame.grid_rowconfigure(0, weight=1); table_frame.grid_columnconfigure(0, weight=1)
        footer = ctk.CTkFrame(main, height=94, fg_color="transparent")
        footer.pack(fill="x", padx=32, pady=(0, 12))
        footer.pack_propagate(False)
        self.status = ctk.CTkLabel(footer, text="Ready", text_color=("#52647c", "#8292aa")); self.status.pack(anchor="w")
        self.totals_frame = ctk.CTkFrame(footer, fg_color="transparent"); self.totals_frame.pack(fill="x", pady=(4, 0))
        self.total_cards = []
        self.configure_columns()

    def scroll_table_horizontally(self, event):
        """Scroll hidden fixed columns without requiring a scrollbar drag."""
        direction = -1 if event.delta > 0 else 1
        self.tree.xview_scroll(direction * 40, "units")
        return "break"

    def update_table_viewport(self, event=None):
        if not hasattr(self, "table_frame") or not hasattr(self, "tree"):
            return
        holder_width = event.width if event is not None else self.table_holder.winfo_width()
        available_width = max(320, holder_width)
        column_width = sum(int(self.tree.column(key, "width")) for key in self.tree["columns"])
        preferred_width = column_width + 30  # vertical scrollbar, borders, and padding
        self.table_frame.configure(width=min(available_width, preferred_width))

    @property
    def columns(self):
        return {"sales": SALES_COLUMNS, "purchases": PURCHASE_COLUMNS,
                "cash": CLOSE_CASH_COLUMNS,
                "subscriptions": SUBSCRIPTION_REPORT_COLUMNS}[self.report_type.get()]

    def configure_columns(self, schedule_resize=True):
        self.render_generation += 1
        self._column_base_widths = None
        self.hide_empty_state()
        self.tree.delete(*self.tree.get_children()); self.tree["columns"] = [c[0] for c in self.columns]
        heading_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        for key, title, width in self.columns:
            # All report values sit directly beneath the center of their
            # heading. Product names remain left aligned for natural reading.
            align = "w" if key in {"item_name", "customer_name"} else "center"
            self.tree.heading(key, text=title, anchor=align)
            # Every heading receives exactly the same trailing breathing room.
            # This prevents long labels from touching the next column while
            # avoiding the oversized gaps produced by content-based sizing.
            fixed_width = max(52, heading_font.measure(title) + 28)
            self.tree.column(key, width=fixed_width, minwidth=52,
                             anchor=align, stretch=False)
        self._last_column_widths = {
            key: int(self.tree.column(key, "width")) for key in self.tree["columns"]
        }
        self.tree.xview_moveto(0)
        self.after_idle(self.update_table_viewport)

    def schedule_column_resize(self, event=None):
        width = event.width if event is not None else self.tree.winfo_width()
        if abs(width - self._last_table_width) < 2:
            return
        # Update at most once per display frame. The old settle timer left a
        # visible empty strip until the user stopped dragging the window.
        if self._column_resize_job is not None:
            try: self.after_cancel(self._column_resize_job)
            except Exception: pass
        self._column_resize_job = self.after(16, self.resize_columns)

    def resize_columns(self):
        if self._column_resize_job is not None:
            try: self.after_cancel(self._column_resize_job)
            except Exception: pass
        self._column_resize_job = None
        # Column positions are intentionally fixed. Window resizing changes the
        # viewport only; users may still drag a header separator themselves.
        self.prepare_tree_display_values()

    def column_resize_finished(self, _event=None):
        widths = {key: int(self.tree.column(key, "width")) for key in self.tree["columns"]}
        if widths == getattr(self, "_last_column_widths", widths):
            return
        self._last_column_widths = widths
        self.update_table_viewport()
        self.prepare_tree_display_values()
        self.render_current_rows()

    @staticmethod
    def column_minimum(key):
        return {
            "sold_at": 120, "purchased_at": 120, "movement_at": 120,
            "ticket_no": 80, "barcode": 100, "item_name": 145,
            "payment_method": 135, "price_status": 190,
            "supplier_name": 130,
            "qty_sold": 90, "qty_purchased": 110,
            "total_buy_price": 115, "total_sold": 110,
            "total_bought": 115, "sales": 90,
            "qty_in": 65, "qty_out": 65,
        }.get(key, 90)

    def grouped_report_rows(self):
        groups = {}
        for row in self.rows:
            groups.setdefault(row.get("category") or "Uncategorized", []).append(row)
        category_descending = self.sort_mode.get() == "Category Z–A"
        names = sorted(groups, key=str.casefold, reverse=category_descending)
        return [(name, groups[name]) for name in names]

    def toggle_category_grouping(self):
        self._column_base_widths = None
        self.resize_columns()
        self.render_current_rows()

    def render_current_rows(self):
        self.render_generation += 1
        generation = self.render_generation
        self.tree.delete(*self.tree.get_children())
        if not self.rows:
            if self.report_has_run.get(self.report_type.get()):
                self.show_empty_state()
            return
        self.hide_empty_state()
        self.prepare_tree_display_values()
        self.render_rows = []
        if self.group_categories.get():
            for category, rows in self.grouped_report_rows():
                self.render_rows.append(("category", category))
                self.render_rows.extend(("row", row) for row in rows)
                self.render_rows.append(("subtotal", (category, rows)))
        else:
            self.render_rows.extend(("row", row) for row in self.rows)
        self._insert_row_batch(0, False, generation, finalize=False)

    def set_window_redraw(self, enabled):
        """Pause/resume native painting so page switches appear as one frame."""
        try:
            hwnd = self.winfo_id()
            ctypes.windll.user32.SendMessageW(hwnd, 0x000B, int(enabled), 0)  # WM_SETREDRAW
            if enabled:
                flags = 0x0001 | 0x0004 | 0x0080 | 0x0100
                ctypes.windll.user32.RedrawWindow(hwnd, None, None, flags)
        except Exception:
            pass

    def populate_cached_rows_immediately(self):
        self.hide_empty_state()
        if not self.rows:
            if self.report_has_run.get(self.report_type.get()):
                self.show_empty_state()
            return
        self.render_rows = []
        if self.group_categories.get():
            for category, rows in self.grouped_report_rows():
                self.render_rows.append(("category", category))
                self.render_rows.extend(("row", row) for row in rows)
                self.render_rows.append(("subtotal", (category, rows)))
        else:
            self.render_rows.extend(("row", row) for row in self.rows)
        for row_type, value in self.render_rows:
            if row_type == "category":
                values = [getattr(self, "_wrapped_categories", {}).get(value, value.upper())] + [""] * (len(self.columns) - 1)
                self.tree.insert("", "end", values=values, tags=("category_header",))
            elif row_type == "subtotal":
                category, rows = value
                values = self.category_total_row(category, rows)
                values[0] = getattr(self, "_wrapped_category_totals", {}).get(category, values[0])
                self.tree.insert("", "end", values=values,
                                 tags=("category_total",))
            else:
                tags = self.row_tags(value)
                display_rows = value.get("__tree_display_rows")
                if not display_rows:
                    display_rows = [value.get("__display_values") or
                                    tuple(self.row_display(value, key) for key, _, _ in self.columns)]
                for display_values in display_rows:
                    self.tree.insert("", "end", values=display_values, tags=tags)

    def configure_payment_filter(self, kind):
        purchase_methods = ["All payment methods", "Bank", "Cheque", "Cash", "Credit"]
        all_methods = ["All payment methods", "Cash", "Cheque", "Voucher", "Card", "Free", "Debt", "VIP Points", "Bank", "Slip", "Mobile", "Credit"]
        values = purchase_methods if kind == "purchases" else all_methods
        selected = self.payment_menu.get()
        self.payment_menu.configure(values=values)
        self.payment_menu.set(selected if selected in values else "All payment methods")
        if kind == "purchases":
            self.reason_menu.pack(side="left", padx=(0, 10), before=self.group_check)
        else:
            self.reason_menu.pack_forget()

    def switch_report(self, kind):
        if self.active_calendar is not None and self.active_calendar.winfo_exists():
            self.active_calendar.close()
        if self.report_type.get() == kind:
            return
        previous_kind = self.report_type.get()
        if previous_kind != kind:
            self.save_report_filters(previous_kind)
        # Navigation should never leave the shared search field visually focused
        # on the report being opened.
        self.focus_set()
        self.suspend_live_filters = True
        self.set_window_redraw(False)
        try:
            self.report_type.set(kind); self.rows = self.report_rows_cache[kind]
            self.configure_columns(schedule_resize=False)
            self.restore_report_filters(kind)
            sales = kind == "sales"
            title = {"sales": "Product Sales Report", "purchases": "Purchased Products Report",
                     "cash": "Close Cash Movement Report",
                     "subscriptions": "Subscription Expiry Report"}[kind]
            self.title_label.configure(text=title)
            inactive_text = ("#172033", "#f1f5f9")
            self.nav_sales.configure(fg_color="#1f6aa5" if sales else "transparent", text_color="#ffffff" if sales else inactive_text)
            self.nav_purchases.configure(fg_color="#1f6aa5" if kind == "purchases" else "transparent", text_color="#ffffff" if kind == "purchases" else inactive_text)
            self.nav_cash.configure(fg_color="#1f6aa5" if kind == "cash" else "transparent", text_color="#ffffff" if kind == "cash" else inactive_text)
            self.nav_subscriptions.configure(fg_color="#1f6aa5" if kind == "subscriptions" else "transparent", text_color="#ffffff" if kind == "subscriptions" else inactive_text)
            subscription_report = kind == "subscriptions"
            self.search_placeholder = ("Customer, phone, ticket, or subscription"
                                       if subscription_report else "Barcode, name, or reference")
            self.search_label.configure(text="Customer search" if subscription_report else "Product search")
            self.search_hint.configure(text=self.search_placeholder)
            self.subscription_status_menu.pack_forget()
            self.subscription_days_menu.pack_forget()
            self.sales_rank_menu.pack_forget()
            if subscription_report:
                self.category_menu.pack_forget()
                self.reason_menu.pack_forget()
                self.group_check.pack_forget()
                self.sort_menu.configure(values=["Expiry: soonest first", "Expiry: latest first",
                                                 "Customer: A-Z", "Customer: Z-A",
                                                 "Start: newest first", "Start: oldest first"], width=145)
                self.payment_menu.configure(width=145)
                if self.sort_menu.get() not in self.sort_menu.cget("values"):
                    self.sort_menu.set("Expiry: soonest first")
                self.sort_menu.pack(side="left", padx=(0, 8))
                self.payment_menu.pack(side="left", padx=(0, 8))
                self.subscription_status_menu.pack(side="left", padx=(0, 8))
                self.subscription_days_menu.pack(side="left")
            else:
                self.sort_menu.configure(values=["Date: newest first", "Date: oldest first",
                                                 "Category A–Z", "Category Z–A"])
                self.category_menu.pack(side="left", padx=(0, 8))
                self.sort_menu.pack(side="left")
                self.payment_menu.pack(side="left", padx=(4, 10))
                if sales:
                    self.sales_rank_menu.pack(side="left", padx=(0, 10), before=self.group_check)
                self.group_check.pack(side="left", padx=(2, 8))
                self.configure_payment_filter(kind)
            if kind == "cash":
                self.start_field.grid_remove(); self.end_field.grid_remove()
                self.cash_filters.grid(row=0, column=0, columnspan=2, padx=18, pady=14, sticky="w")
                if not self.cash_sequences:
                    self.after_idle(self.load_cash_sequences)
            else:
                self.cash_filters.grid_remove(); self.start_field.grid(); self.end_field.grid()
            self.resize_columns()
            self.populate_cached_rows_immediately()
            empty_loaded = self.report_has_run.get(kind) and not self.rows
            status_text = (f"{len(self.rows):,} cached rows" if self.rows
                           else "No results found" if empty_loaded else "Ready")
            self.status.configure(text=status_text, text_color="#3ecf8e" if self.rows else "#8292aa")
            self.update_totals()
            self.update_idletasks()
        finally:
            self.suspend_live_filters = False
            self.set_window_redraw(True)

    def show_empty_state(self):
        self.empty_state.place(relx=0.5, rely=0.45, anchor="center")
        self.empty_state.lift()

    def hide_empty_state(self):
        if hasattr(self, "empty_state"):
            self.empty_state.place_forget()

    def save_report_filters(self, kind):
        self.report_filter_states[kind] = {
            "category": self.category_menu.get(), "sort": self.sort_menu.get(),
            "payment": self.payment_menu.get(), "search": self.get_search_text(),
            "sales_rank": self.sales_rank_menu.get(),
            "reason": self.reason_menu.get(),
            "group": self.group_categories.get(), "movement": self.movement_menu.get(),
            "subscription_status": self.subscription_status_menu.get(),
            "subscription_days": self.subscription_days_menu.get(),
            "cash": self.cash_menu.get(),
            "start_date": self.start_field.date_var.get(), "start_hour": self.start_field.hour.get(),
            "start_minute": self.start_field.minute.get(), "end_date": self.end_field.date_var.get(),
            "end_hour": self.end_field.hour.get(), "end_minute": self.end_field.minute.get(),
            "end_live": self.end_time_live,
        }

    @staticmethod
    def set_entry(entry, value):
        entry.delete(0, "end"); entry.insert(0, value)

    def set_search_entry(self, value):
        self.search_var.set(value or "")
        self._update_search_hint()

    def _focus_search_entry(self, _event=None):
        self.search_hint.place_forget()
        self.search_entry.focus_set()

    def _search_focus_in(self, _event=None):
        self.search_has_focus = True
        self.search_hint.place_forget()

    def _search_focus_out(self, _event=None):
        self.search_has_focus = False
        self._update_search_hint(force_visible=True)

    def _search_text_changed(self, *_args):
        if self.search_var.get():
            self.search_hint.place_forget()
        else:
            self._update_search_hint()
        self.schedule_live_filter(450)

    def live_filter_changed(self, _value=None):
        self.schedule_live_filter(80)

    def live_date_filter_changed(self):
        self.schedule_live_filter(450)

    def live_end_filter_changed(self):
        self.disable_live_end_time()
        self.schedule_live_filter(450)

    def schedule_live_filter(self, delay=120):
        if self.suspend_live_filters or not self.config_data.get("database"):
            return
        if self.live_filter_job is not None:
            try: self.after_cancel(self.live_filter_job)
            except Exception: pass
        self.live_filter_job = self.after(delay, self.apply_live_filters)

    def apply_live_filters(self):
        self.live_filter_job = None
        try:
            self.query_parameters()
        except Exception:
            # Date/time fields may be temporarily incomplete while typing.
            self.status.configure(text="Finish entering a valid filter value", text_color="#f0aa5b")
            return
        self.run_report()

    def _update_search_hint(self, force_visible=False):
        if self.search_var.get():
            self.search_hint.place_forget()
        elif force_visible or not self.search_has_focus:
            self.search_hint.place(x=8, rely=0.5, anchor="w")

    def get_search_text(self):
        return self.search_var.get().strip()

    def restore_report_filters(self, kind):
        state = self.report_filter_states.get(kind)
        if state is None:
            self.category_menu.set("All categories")
            self.sort_menu.set("Date: newest first")
            self.payment_menu.set("All payment methods")
            self.sales_rank_menu.set("Sales ranking: default")
            self.reason_menu.set("All reasons")
            self.subscription_status_menu.set("All statuses")
            self.subscription_days_menu.set("Days: default")
            self.set_search_entry("")
            self.group_categories.set(False)
            self.movement_menu.set("All")
            if kind == "subscriptions":
                now = datetime.now().replace(second=0, microsecond=0)
                self.start_field.set_datetime(now.replace(hour=0, minute=0))
                self.end_field.set_datetime(
                    (now + timedelta(days=365)).replace(hour=23, minute=59))
                self.end_time_live = False
            else:
                self.end_time_live = True
                self.end_field.set_datetime(datetime.now().replace(second=0, microsecond=0))
            return
        self.category_menu.set(state["category"] if state["category"] in self.categories else "All categories")
        self.sort_menu.set(state["sort"])
        payment_values = list(self.payment_menu.cget("values"))
        self.payment_menu.set(state["payment"] if state["payment"] in payment_values else "All payment methods")
        self.sales_rank_menu.set(state.get("sales_rank", "Sales ranking: default"))
        self.reason_menu.set(state.get("reason", "All reasons") if state.get("reason", "All reasons") in PURCHASE_REASONS else "All reasons")
        saved_status = state.get("subscription_status", "All statuses")
        if saved_status == "Inactive":
            saved_status = "Expired"
        self.subscription_status_menu.set(saved_status)
        self.subscription_days_menu.set(state.get("subscription_days", "Days: default"))
        self.set_search_entry(state["search"])
        self.group_categories.set(state["group"]); self.movement_menu.set(state["movement"])
        self.cash_menu.set(state["cash"])
        self.start_field.date_var.set(state["start_date"]); self.set_entry(self.start_field.hour, state["start_hour"]); self.set_entry(self.start_field.minute, state["start_minute"])
        self.end_field.date_var.set(state["end_date"]); self.set_entry(self.end_field.hour, state["end_hour"]); self.set_entry(self.end_field.minute, state["end_minute"])
        self.end_time_live = state.get("end_live", True)
        if self.end_time_live:
            self.end_field.set_datetime(datetime.now().replace(second=0, microsecond=0))

    def disable_live_end_time(self):
        self.end_time_live = False

    def update_live_end_time(self):
        if self.end_time_live:
            now = datetime.now().replace(second=0, microsecond=0)
            current_parts = (self.end_field.date_var.get(), self.end_field.hour.get(),
                             self.end_field.minute.get())
            wanted_parts = (now.strftime("%m-%d-%y"), f"{now.hour:02d}", f"{now.minute:02d}")
            if current_parts != wanted_parts:
                self.end_field.set_datetime(now)
        self.after(15000, self.update_live_end_time)

    def open_settings(self): SettingsDialog(self, self.config_data, self.after_settings_saved)

    def after_settings_saved(self):
        for cached_rows in self.report_rows_cache.values():
            for row in cached_rows:
                row.pop("__display_values", None)
        self.render_current_rows()
        self.load_categories()
        self.subscription_monitor.request_check()

    def change_theme(self, mode):
        if self._theme_sync_job is not None:
            try: self.after_cancel(self._theme_sync_job)
            except Exception: pass
            self._theme_sync_job = None
        ctk.set_appearance_mode(mode)
        self.config_data["appearance"] = mode
        try: save_config(self.config_data)
        except Exception: pass
        if mode == "System":
            # CustomTkinter resolves System asynchronously. Styling ttk now
            # would retain the previous explicit theme, so synchronize after
            # its Windows appearance callback has completed.
            self._theme_sync_job = self.after(100, self.sync_system_table_theme)
        else:
            self._style_table(mode == "Dark")

    def sync_system_table_theme(self):
        self._theme_sync_job = None
        if self.config_data.get("appearance") != "System":
            return
        self._style_table(ctk.get_appearance_mode() == "Dark")
        # Keep the native ttk table synchronized if Windows changes theme while
        # the application remains open.
        self._theme_sync_job = self.after(1000, self.sync_system_table_theme)

    def _style_table(self, dark=True):
        style = ttk.Style(self)
        style.configure("Report.Treeview", background="#111827" if dark else "#ffffff",
                        fieldbackground="#111827" if dark else "#ffffff",
                        foreground="#e5edf8" if dark else "#172033")
        style.configure("Report.Treeview.Heading", background="#1f2937" if dark else "#e5edf2",
                        foreground="#a9b8cc" if dark else "#24344d")
        self.tree.tag_configure("price_up", background="#421b24" if dark else "#ffe4e6",
                                foreground="#fecdd3" if dark else "#9f1239")
        self.tree.tag_configure("price_down", background="#12372a" if dark else "#dcfce7",
                                foreground="#bbf7d0" if dark else "#166534")
        self.tree.tag_configure("sale_discount", background="#421b24" if dark else "#ffe4e6",
                                foreground="#fecdd3" if dark else "#9f1239")
        self.tree.tag_configure("sale_price_change", background="#12372a" if dark else "#dcfce7",
                                foreground="#bbf7d0" if dark else "#166534")
        self.tree.tag_configure("subscription_ending",
                                background="#4a3b0c" if dark else "#fef3c7",
                                foreground="#fde68a" if dark else "#92400e")
        self.tree.tag_configure("subscription_expired",
                                background="#421b24" if dark else "#fee2e2",
                                foreground="#fecdd3" if dark else "#991b1b")
        self.tree.tag_configure("category_total", background="#17324d" if dark else "#dbeafe",
                                foreground="#7dd3fc" if dark else "#12395b",
                                font=("Segoe UI", 10, "bold"))

    def load_cash_sequences(self):
        if not self.config_data.get("database"): return
        try:
            selected = self.cash_menu.get()
            with db_connect(self.config_data) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT money, host, hostsequence, datestart, dateend FROM closedcash ORDER BY datestart DESC LIMIT 500")
                    rows = cur.fetchall()
            self.cash_sequences = {}
            for r in rows:
                end_text = r["dateend"].strftime("%m-%d-%y %H:%M") if r["dateend"] else "Open"
                label = f"Sequence #{r['hostsequence']}  |  {r['datestart']:%m-%d-%y %H:%M} → {end_text}"
                self.cash_sequences[label] = r["money"]
            values = list(self.cash_sequences) or ["No sequences found"]
            self.cash_menu.configure(values=values)
            self.cash_menu.set(selected if selected in self.cash_sequences else values[0])
        except Exception as exc:
            self.status.configure(text=f"Could not load close cash sequences: {exc}", text_color="#ff7b7b")

    @staticmethod
    def number(row, key):
        try: return float(row.get(key) or 0)
        except (TypeError, ValueError): return 0.0

    def totals_data(self, rows=None):
        rows = self.rows if rows is None else rows
        if self.report_type.get() == "subscriptions":
            return [("Subscriptions", len(rows)),
                    ("Total Amount", sum(self.number(r, "amount") for r in rows))]
        if self.report_type.get() == "sales":
            sell = sum(self.number(r, "sell_price") for r in rows)
            qty = sum(self.number(r, "qty_sold") for r in rows)
            sales = sum(self.number(r, "sales") for r in rows)
            return [("Sell Price", sell), ("QTY Sold", qty), ("Sales", sales)]
        if self.report_type.get() == "purchases":
            qty = sum(self.number(r, "qty_purchased") for r in rows)
            bought = sum(self.number(r, "total_buy_price") for r in rows)
            return [("QTY Purchased", qty), ("Total Buy Price", bought)]
        qty_in = sum(self.number(r, "qty_in") for r in rows)
        qty_out = sum(self.number(r, "qty_out") for r in rows)
        sold = sum(self.number(r, "total_sold") for r in rows)
        bought = sum(self.number(r, "total_bought") for r in rows)
        tickets = len({str(r.get("ticket_no")) for r in rows
                       if r.get("ticket_no") not in (None, "")
                       and r.get("movement_type") in (None, "Sold")})
        return [("Total Tickets", tickets), ("In", qty_in), ("Out", qty_out),
                ("Total Sold", sold), ("Total Bought", bought)]

    def category_total_row(self, category, rows):
        totals = dict(self.totals_data(rows))
        label = f"{category} TOTAL"
        if self.report_type.get() == "sales":
            return [label, "", "", "", "", self.format_money(totals["Sell Price"]),
                    "", f'{totals["QTY Sold"]:,.2f}',
                    self.format_money(totals["Sales"])]
        if self.report_type.get() == "purchases":
            return [label, "", "", "", "", "", f'{totals["QTY Purchased"]:,.2f}',
                    self.format_money(totals["Total Buy Price"])]
        return [label, f'{int(totals["Total Tickets"]):,} tickets', "", "", "", "",
                f'{totals["In"]:,.2f}', f'{totals["Out"]:,.2f}',
                self.format_money(totals["Total Sold"]), self.format_money(totals["Total Bought"])]

    def payment_totals_data(self):
        if self.report_type.get() == "subscriptions":
            return []
        totals = {}
        amount_key = {"sales": "sales", "purchases": "total_buy_price"}.get(self.report_type.get())
        for row in self.rows:
            methods = [part.strip() for part in str(row.get("payment_method") or "").split(",") if part.strip()]
            if not methods:
                continue
            amount = (self.number(row, amount_key) if amount_key
                      else self.number(row, "total_sold") + self.number(row, "total_bought"))
            share = amount / len(methods)
            for method in methods:
                totals[method] = totals.get(method, 0.0) + share
        preferred = ["Cash", "Cheque", "Card", "Voucher", "Bank", "Credit",
                     "Debt", "Free", "VIP Points", "Slip", "Mobile"]
        ordered = [name for name in preferred if name in totals]
        ordered.extend(sorted(name for name in totals if name not in preferred))
        return [(f"Total {name}", totals[name]) for name in ordered]

    def update_totals(self):
        for card in self.total_cards:
            card.destroy()
        self.total_cards.clear()
        if not self.rows:
            return
        base_totals = self.totals_data()
        all_totals = base_totals + self.payment_totals_data()
        for index, (label, value) in enumerate(all_totals):
            is_payment = index >= len(base_totals)
            card = ctk.CTkFrame(self.totals_frame, fg_color=(("#dcfce7" if is_payment else "#e5edf6"), ("#12372a" if is_payment else "#172033")), corner_radius=9)
            card.pack(side="left", padx=4)
            ctk.CTkLabel(card, text=label.upper(), font=("Segoe UI", 9, "bold"), text_color=(("#15803d" if is_payment else "#64748b"), ("#86efac" if is_payment else "#8fa3bd"))).pack(anchor="w", padx=12, pady=(6, 0))
            money_labels = {"Buy Price", "Sell Price", "Sales", "Total Buy Price",
                            "Total Sold", "Total Bought", "Total Amount"}
            value_text = (self.format_money(value) if is_payment or label in money_labels
                          else f"{int(value):,}" if label in {"Total Tickets", "Subscriptions"}
                          else f"{value:,.2f}")
            ctk.CTkLabel(card, text=value_text, font=("Segoe UI", 15, "bold"), text_color=(("#166534" if is_payment else "#0f4c81"), ("#bbf7d0" if is_payment else "#67c7ff"))).pack(anchor="w", padx=12, pady=(0, 6))
            self.total_cards.append(card)

    def pdf_payment_totals(self):
        entries = self.payment_totals_data()
        if not entries:
            return None
        cells_per_row = 4
        data = []
        for start in range(0, len(entries), cells_per_row):
            chunk = entries[start:start + cells_per_row]
            chunk += [("", 0)] * (cells_per_row - len(chunk))
            data.append([label.upper() for label, _ in chunk])
            data.append([self.format_money(value) if label else "" for label, value in chunk])
        table = Table(data, colWidths=[(landscape(A4)[0] - 20*mm) / cells_per_row] * cells_per_row)
        table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#dcfce7")),
                                   ("TEXTCOLOR", (0,0), (-1,-1), colors.HexColor("#166534")),
                                   ("FONTNAME", (0,0), (-1,-1), "Helvetica-Bold"),
                                   ("FONTSIZE", (0,0), (-1,-1), 8),
                                   ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#86b99a")),
                                   ("ALIGN", (0,0), (-1,-1), "CENTER"),
                                   ("TOPPADDING", (0,0), (-1,-1), 5),
                                   ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
        return table

    def pdf_total_row(self):
        totals = dict(self.totals_data())
        if self.report_type.get() == "subscriptions":
            return ["TOTAL", "", f"{int(totals['Subscriptions']):,} subscriptions",
                    "", "", "", "", "", "", self.format_money(totals["Total Amount"])]
        if self.report_type.get() == "sales":
            return ["TOTAL", "", "", "", "", self.format_money(totals['Sell Price']),
                    "", f"{totals['QTY Sold']:,.2f}",
                    self.format_money(totals['Sales'])]
        if self.report_type.get() == "purchases":
            return ["TOTAL", "", "", "", "", "",
                    f"{totals['QTY Purchased']:,.2f}",
                    self.format_money(totals['Total Buy Price'])]
        return ["TOTAL", f"{int(totals['Total Tickets']):,} tickets", "", "", "", "",
                f"{totals['In']:,.2f}", f"{totals['Out']:,.2f}", self.format_money(totals['Total Sold']),
                self.format_money(totals['Total Bought'])]

    def load_categories(self):
        try:
            with db_connect(self.config_data) as conn:
                with conn.cursor() as cur: cur.execute("SELECT id, name FROM categories ORDER BY name"); rows = cur.fetchall()
            self.categories = {"All categories": None} | {row["name"]: row["id"] for row in rows}
            self.category_menu.configure(values=list(self.categories)); self.category_menu.set("All categories")
            self.status.configure(text="Database connected")
            self.load_cash_sequences()
        except Exception as exc: self.status.configure(text=f"Database unavailable: {exc}", text_color="#ff7b7b")

    def query_parameters(self):
        search = self.get_search_text()
        escaped_search = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        reason = str(self.config_data.get("purchase_reason", "1")).strip()
        selected_payment = self.payment_menu.get()
        status = self.subscription_status_menu.get()
        common = {"category_id": self.categories.get(self.category_menu.get()), "category_name": None if self.category_menu.get() == "All categories" else self.category_menu.get(), "payment_method": "All" if selected_payment == "All payment methods" else selected_payment, "reason": PURCHASE_REASONS.get(self.reason_menu.get()), "search": search, "search_like": f"%{escaped_search}%", "purchase_reason": int(reason) if reason else None,
                  "subscription_status": "All" if status == "All statuses" else status.title(),
                  "notify_days": int(self.config_data.get("subscription_notify_days", 2))}
        if self.report_type.get() == "cash":
            money = self.cash_sequences.get(self.cash_menu.get())
            if not money: raise ValueError("Select a close cash sequence")
            return common | {"money": money, "movement_filter": self.movement_menu.get()}
        start, end = self.start_field.get(), self.end_field.get()
        if start > end: raise ValueError("Start date/time must be before end date/time")
        return common | {"start_at": start, "end_at": end}

    def order_clause(self):
        if self.report_type.get() == "subscriptions":
            days_order = self.subscription_days_menu.get()
            if days_order == "Days: lowest first":
                return "days_remaining ASC, customer_name ASC"
            if days_order == "Days: highest first":
                return "days_remaining DESC, customer_name ASC"
            return {
                "Expiry: soonest first": "expiry_date ASC, customer_name ASC",
                "Expiry: latest first": "expiry_date DESC, customer_name ASC",
                "Customer: A-Z": "customer_name ASC, expiry_date ASC",
                "Customer: Z-A": "customer_name DESC, expiry_date ASC",
                "Start: newest first": "start_date DESC, customer_name ASC",
                "Start: oldest first": "start_date ASC, customer_name ASC",
            }.get(self.sort_menu.get(), "expiry_date ASC, customer_name ASC")
        # ORDER BY is intentionally limited to these literal clauses. Never
        # pass user-entered text into the SQL template.
        return {"Date: newest first": "1 DESC", "Date: oldest first": "1 ASC",
                "Category A–Z": "category ASC, 1 DESC",
                "Category Z–A": "category DESC, 1 DESC"}.get(
                    self.sort_mode.get(), "1 DESC")

    def run_report(self, refreshing=False):
        try: params = self.query_parameters()
        except Exception as exc: messagebox.showerror(APP_NAME, str(exc)); return
        self.hide_empty_state()
        kind = self.report_type.get()
        template = {"sales": SALES_SQL, "purchases": PURCHASES_SQL,
                    "cash": CLOSE_CASH_SQL,
                    "subscriptions": SUBSCRIPTION_REPORT_SQL}[kind]
        query = template.format(order_clause=self.order_clause())
        column_keys = [column[0] for column in self.columns]
        self.query_generation += 1
        query_generation = self.query_generation
        self.run_btn.configure(state="disabled", text="Run Report"); self.refresh_btn.configure(state="disabled")
        self.status.configure(text="Refreshing all data…" if refreshing else "Loading report…", text_color="#3b82f6")
        self.load_started = time.monotonic()
        self.loading_bar.pack(fill="x", pady=1)
        self.loading_bar.start()
        sales_ranking = self.sales_rank_menu.get()
        threading.Thread(target=self._query_worker,
                         args=(kind, query, params, column_keys, refreshing,
                               query_generation, sales_ranking), daemon=True).start()

    def refresh_report(self):
        # Refresh is also the report's reset action: return every filter to its
        # predictable default before fetching the newest database state.
        if self.live_filter_job is not None:
            try: self.after_cancel(self.live_filter_job)
            except Exception: pass
            self.live_filter_job = None
        self.suspend_live_filters = True
        now = datetime.now().replace(second=0, microsecond=0)
        if self.report_type.get() == "subscriptions":
            self.end_time_live = False
            self.start_field.set_datetime(now.replace(hour=0, minute=0))
            self.end_field.set_datetime(
                (now + timedelta(days=365)).replace(hour=23, minute=59))
        else:
            self.end_time_live = True
            self.start_field.set_datetime(now.replace(hour=0, minute=0))
            self.end_field.set_datetime(now)
        self.category_menu.set("All categories")
        self.sort_menu.set("Expiry: soonest first" if self.report_type.get() == "subscriptions"
                           else "Date: newest first")
        self.payment_menu.set("All payment methods")
        self.sales_rank_menu.set("Sales ranking: default")
        self.reason_menu.set("All reasons")
        self.subscription_status_menu.set("All statuses")
        self.subscription_days_menu.set("Days: default")
        self.set_search_entry("")
        self.group_categories.set(False)
        self.movement_menu.set("All")
        if self.report_type.get() == "cash" and self.cash_sequences:
            self.cash_menu.set(next(iter(self.cash_sequences)))
        self.report_filter_states.pop(self.report_type.get(), None)
        self.suspend_live_filters = False
        self.run_report(refreshing=True)

    def _query_worker(self, kind, query, params, column_keys, refreshing,
                      query_generation, sales_ranking):
        try:
            with db_connect(self.config_data) as conn:
                conn.ping(reconnect=True)
                with conn.cursor() as cur: cur.execute(query, params); rows = cur.fetchall()
                if kind == "purchases":
                    self.mark_purchase_price_changes(rows)
                elif kind in ("sales", "cash"):
                    self.mark_sale_price_status(rows)
                if kind == "sales":
                    self.rank_sales_rows(rows, sales_ranking)
                for row in rows:
                    row["__display_values"] = tuple(self.row_display(row, key) for key in column_keys)
                categories = cash_rows = None
                if refreshing:
                    with conn.cursor() as cur:
                        cur.execute("SELECT id, name FROM categories ORDER BY name")
                        categories = cur.fetchall()
                        cur.execute("SELECT money, hostsequence, datestart, dateend FROM closedcash ORDER BY datestart DESC LIMIT 500")
                        cash_rows = cur.fetchall()
            self.after(0, lambda: self.finish_query_smooth(kind, rows, categories, cash_rows, refreshing, query_generation))
        except Exception as exc: self.after(0, lambda e=exc: self.query_failed(e, query_generation))

    def rank_sales_rows(self, rows, ranking):
        """Rank sale lines by each product's total quantity or occurrence count."""
        if ranking == "Sales ranking: default":
            return
        totals = {}
        repeats = {}
        for row in rows:
            product = str(row.get("barcode") or row.get("item_name") or "")
            quantity = self.number(row, "qty_sold")
            totals[product] = totals.get(product, 0.0) + quantity
            if quantity > 0:
                repeats[product] = repeats.get(product, 0) + 1
        if ranking == "Most sold by QTY":
            rows.sort(key=lambda row: (
                -totals.get(str(row.get("barcode") or row.get("item_name") or ""), 0.0),
                str(row.get("item_name") or "").casefold(),
                -(row.get("sold_at").timestamp() if isinstance(row.get("sold_at"), datetime) else 0),
            ))
        else:
            rows.sort(key=lambda row: (
                -repeats.get(str(row.get("barcode") or row.get("item_name") or ""), 0),
                -totals.get(str(row.get("barcode") or row.get("item_name") or ""), 0.0),
                str(row.get("item_name") or "").casefold(),
            ))

    def finish_query_smooth(self, kind, rows, categories, cash_rows, refreshed, query_generation):
        if query_generation != self.query_generation:
            return
        minimum_ms = 550 if refreshed else 220
        elapsed_ms = int((time.monotonic() - self.load_started) * 1000)
        wait_ms = max(0, minimum_ms - elapsed_ms)
        self.after(wait_ms, lambda: self.show_rows(kind, rows, categories, cash_rows, refreshed)
                   if query_generation == self.query_generation else None)

    def query_failed(self, exc, query_generation=None):
        if query_generation is not None and query_generation != self.query_generation:
            return
        self.stop_loading()
        self.run_btn.configure(state="normal", text="Run Report"); self.refresh_btn.configure(state="normal")
        self.status.configure(text=f"Report failed: {exc}", text_color="#ff7b7b")
        messagebox.showerror(APP_NAME, str(exc))

    def format_money(self, value):
        symbol = str(self.config_data.get("currency", "$") or "$").strip()
        separator = "" if len(symbol) == 1 else " "
        return f"{symbol}{separator}{float(value or 0):,.2f}"

    def display(self, value, key=None):
        if isinstance(value, datetime): return value.strftime("%m-%d-%y %H:%M")
        if isinstance(value, date): return value.strftime("%m-%d-%y")
        if key in MONEY_COLUMNS: return self.format_money(value)
        if isinstance(value, float): return f"{value:,.2f}"
        return "" if value is None else str(value)

    def row_display(self, row, key, pdf=False):
        value = self.display(row.get(key), key)
        if key == "ticket_no" and value and (
                row.get("movement_type") == "Refund"
                or self.number(row, "qty_sold") < 0
                or self.number(row, "qty_out") < 0):
            return value if value.startswith("#") else f"#{value}"
        change = row.get("__price_change", 0)
        if key == "buy_price" and change:
            marker = "▲" if change > 0 else "▼"
            # The invisible suffix matches the rendered width of "▲ "/"▼ "
            # in Segoe UI, keeping the monetary value itself centered beneath
            # unchanged prices while the indicator remains before it.
            return f"{marker} {value}\u2003\u2006"
        return value

    def prepare_tree_display_values(self):
        """Wrap product names inside the user-selected fixed column width."""
        if not hasattr(self, "tree") or "item_name" not in self.tree["columns"]:
            return
        keys = [key for key, _, _ in self.columns]
        # Only a product name is allowed to expand a logical report row.
        # Every other column keeps the normal compact row height.
        wrap_keys = {"item_name", "product_name"}
        font = tkfont.Font(family="Segoe UI", size=10)
        # Grow text-heavy columns only when their actual content needs space.
        # Item Name retains a larger limit; Price Status expands enough for
        # discount/change details while blank and Regular values stay compact.
        widths_changed = False
        for auto_key, maximum_width in (("item_name", 720), ("product_name", 720),
                                        ("price_status", 460)):
            if auto_key not in keys:
                continue
            value_index = keys.index(auto_key)
            current_width = int(self.tree.column(auto_key, "width"))
            longest_width = max(
                (font.measure(str((row.get("__display_values") or
                                   tuple(self.row_display(row, key) for key in keys))[value_index] or ""))
                 for row in self.rows),
                default=0,
            )
            desired_width = min(maximum_width, max(current_width, longest_width + 28))
            if desired_width <= current_width:
                continue
            self.tree.column(auto_key, width=desired_width)
            self._last_column_widths[auto_key] = desired_width
            widths_changed = True
        if widths_changed:
            self.after_idle(self.update_table_viewport)
        for row in self.rows:
            values = list(row.get("__display_values") or
                          tuple(self.row_display(row, key) for key in keys))
            wrapped_columns = {}
            line_count = 1
            for index, key in enumerate(keys):
                if key not in wrap_keys:
                    continue
                available = max(40, int(self.tree.column(key, "width")) - 20)
                lines = self.wrap_tree_text(str(values[index] or ""), available, font).splitlines() or [""]
                wrapped_columns[index] = lines
                line_count = max(line_count, len(lines))
            display_rows = []
            for line_index in range(line_count):
                display_line = list(values) if line_index == 0 else [""] * len(values)
                for index, lines in wrapped_columns.items():
                    display_line[index] = lines[line_index] if line_index < len(lines) else ""
                display_rows.append(tuple(display_line))
            row["__tree_display_rows"] = display_rows
        self._wrapped_categories = {}
        self._wrapped_category_totals = {}
        if self.group_categories.get():
            first_key = keys[0]
            first_available = max(40, int(self.tree.column(first_key, "width")) - 20)
            category_font = tkfont.Font(family="Segoe UI", size=12, weight="bold")
            for category, _rows in self.grouped_report_rows():
                heading = self.wrap_tree_text(category.upper(), first_available, category_font)
                total = self.wrap_tree_text(f"{category} TOTAL", first_available, font)
                self._wrapped_categories[category] = heading
                self._wrapped_category_totals[category] = total
        # Each physical Treeview row stays standard height. Wrapped logical rows
        # receive continuation rows, so short products never inherit the height
        # of the longest product in the report.
        ttk.Style(self).configure("Report.Treeview", rowheight=34)

    @staticmethod
    def wrap_tree_text(text, available, font):
        if not text or font.measure(text) <= available:
            return text
        lines, current = [], ""
        for word in text.split():
            candidate = word if not current else f"{current} {word}"
            if font.measure(candidate) <= available:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            # Split an unusually long unbroken word without overflowing.
            for character in word:
                candidate = current + character
                if current and font.measure(candidate) > available:
                    lines.append(current)
                    current = character
                else:
                    current = candidate
        if current:
            lines.append(current)
        return "\n".join(lines)

    @staticmethod
    def row_tags(row):
        if row.get("expiry_status") == "Ending Soon":
            return ("subscription_ending",)
        if row.get("expiry_status") == "Expired":
            return ("subscription_expired",)
        if row.get("__sale_status") == "discount":
            return ("sale_discount",)
        if row.get("__sale_status") == "changed":
            return ("sale_price_change",)
        change = row.get("__price_change", 0)
        return ("price_up",) if change > 0 else (("price_down",) if change < 0 else ())

    def mark_sale_price_status(self, rows):
        for row in rows:
            if (row.get("movement_type") == "Refund"
                    or self.number(row, "qty_sold") < 0
                    or self.number(row, "qty_out") < 0):
                row["price_status"] = "REFUND (-)"
                row["__sale_status"] = "discount"
                continue
            if row.get("movement_type") not in (None, "Sold"):
                row["price_status"] = "—"
                row.pop("__sale_status", None)
                continue
            has_actual_ticket_price = "actual_sell_price" in row
            price = self.number(row, "actual_sell_price" if has_actual_ticket_price else "sell_price")
            qty = abs(self.number(row, "qty_sold") or self.number(row, "qty_out"))
            discount = abs(self.number(row, "explicit_discount_amount"))
            regular = self.number(row, "sell_price" if has_actual_ticket_price else "regular_sell_price")
            if discount > 0:
                original_total = regular * qty if regular > 0 else price * qty + discount
                percent = (discount / original_total * 100) if original_total else 0
                row["price_status"] = f"Discount {self.format_money(discount)} ({percent:.1f}%)"
                row["__sale_status"] = "discount"
            elif regular > 0 and abs(price - regular) > 0.000001:
                unit_difference = abs(price - regular)
                total_difference = unit_difference * qty
                percent = unit_difference / regular * 100
                if price < regular:
                    row["price_status"] = f"Discount {self.format_money(total_difference)} ({percent:.1f}%)"
                    row["__sale_status"] = "discount"
                else:
                    row["price_status"] = f"Price +{self.format_money(total_difference)} ({percent:.1f}%)"
                    row["__sale_status"] = "changed"
            else:
                row["price_status"] = "Regular"
                row.pop("__sale_status", None)

    def mark_purchase_price_changes(self, rows):
        """Mark displayed purchases whose price differs from the preceding one."""
        previous = {}
        chronological = sorted(
            enumerate(rows),
            key=lambda item: (
                str(item[1].get("barcode") or ""),
                item[1].get("purchased_at") or datetime.min,
                item[0],
            ),
        )
        for _, row in chronological:
            row.pop("__price_change", None)
            product = str(row.get("barcode") or row.get("item_name") or "")
            price = self.number(row, "buy_price")
            if product in previous and abs(price - previous[product]) > 0.000001:
                row["__price_change"] = 1 if price > previous[product] else -1
            previous[product] = price

    def stop_loading(self):
        self.loading_bar.stop()
        self.loading_bar.set(0)
        self.loading_bar.pack_forget()

    def show_rows(self, kind, rows, categories=None, cash_rows=None, refreshed=False):
        self.report_rows_cache[kind] = rows
        self.report_has_run[kind] = True
        if categories is not None:
            selected = self.category_menu.get()
            new_categories = {"All categories": None} | {row["name"]: row["id"] for row in categories}
            if new_categories != self.categories:
                self.categories = new_categories
                self.category_menu.configure(values=list(self.categories))
                self.category_menu.set(selected if selected in self.categories else "All categories")
        if cash_rows is not None:
            selected_cash = self.cash_menu.get()
            new_cash_sequences = {}
            for row in cash_rows:
                end_text = row["dateend"].strftime("%m-%d-%y %H:%M") if row["dateend"] else "Open"
                label = f"Sequence #{row['hostsequence']}  |  {row['datestart']:%m-%d-%y %H:%M} → {end_text}"
                new_cash_sequences[label] = row["money"]
            if new_cash_sequences != self.cash_sequences:
                self.cash_sequences = new_cash_sequences
                values = list(self.cash_sequences) or ["No sequences found"]
                self.cash_menu.configure(values=values)
                self.cash_menu.set(selected_cash if selected_cash in self.cash_sequences else values[0])
        if kind != self.report_type.get():
            self.stop_loading()
            self.run_btn.configure(state="normal", text="Run Report"); self.refresh_btn.configure(state="normal")
            return
        self.rows = rows
        self._column_base_widths = None
        if rows:
            self.hide_empty_state()
        else:
            self.show_empty_state()
        self.resize_columns()
        self.render_generation += 1
        generation = self.render_generation
        self.tree.delete(*self.tree.get_children())
        self.render_rows = []
        if self.group_categories.get():
            for category, grouped_rows in self.grouped_report_rows():
                self.render_rows.append(("category", category))
                self.render_rows.extend(("row", row) for row in grouped_rows)
                self.render_rows.append(("subtotal", (category, grouped_rows)))
        else:
            self.render_rows.extend(("row", row) for row in self.rows)
        self._insert_row_batch(0, refreshed, generation, finalize=True)

    def _insert_row_batch(self, start, refreshed, generation, finalize):
        if generation != self.render_generation:
            return
        batch_size = 600 if len(self.render_rows) > 1500 else 350
        end = min(start + batch_size, len(self.render_rows))
        for row_type, value in self.render_rows[start:end]:
            if row_type == "category":
                values = [getattr(self, "_wrapped_categories", {}).get(value, value.upper())] + [""] * (len(self.columns) - 1)
                self.tree.insert("", "end", values=values, tags=("category_header",))
            elif row_type == "subtotal":
                category, rows = value
                values = self.category_total_row(category, rows)
                values[0] = getattr(self, "_wrapped_category_totals", {}).get(category, values[0])
                self.tree.insert("", "end", values=values,
                                 tags=("category_total",))
            else:
                tags = self.row_tags(value)
                display_rows = value.get("__tree_display_rows")
                if not display_rows:
                    display_rows = [value.get("__display_values") or
                                    tuple(self.row_display(value, key) for key, _, _ in self.columns)]
                for display_values in display_rows:
                    self.tree.insert("", "end", values=display_values, tags=tags)
        if end < len(self.render_rows):
            self.after(1, lambda: self._insert_row_batch(end, refreshed, generation, finalize))
            return
        if not finalize:
            self.update_totals()
            return
        self.stop_loading()
        self.run_btn.configure(state="normal", text="Run Report"); self.refresh_btn.configure(state="normal")
        action = "refreshed" if refreshed else "loaded"
        if self.rows:
            self.status.configure(text=f"{len(self.rows):,} rows {action} · {datetime.now():%H:%M:%S}", text_color="#3ecf8e")
        else:
            self.status.configure(text=f"No results found · {datetime.now():%H:%M:%S}",
                                  text_color=("#64748b", "#94a3b8"))
        self.update_totals()

    def export_pdf(self):
        if not self.rows: messagebox.showinfo(APP_NAME, "Run a report before exporting."); return
        desktop = Path(os.getenv("USERPROFILE", Path.home())) / "Desktop"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        label = {"sales": "product_sales", "purchases": "purchased_products",
                 "cash": "close_cash_movement",
                 "subscriptions": "subscription_expiry"}[self.report_type.get()]
        target = desktop / f"{label}_{stamp}.pdf"
        try:
            font_dir = Path(os.getenv("WINDIR", r"C:\Windows")) / "Fonts"
            if "ReportUnicode" not in pdfmetrics.getRegisteredFontNames():
                regular_font = font_dir / "arial.ttf"
                bold_font = font_dir / "arialbd.ttf"
                if not regular_font.exists():
                    regular_font = font_dir / "segoeui.ttf"
                    bold_font = font_dir / "segoeuib.ttf"
                pdfmetrics.registerFont(TTFont("ReportUnicode", str(regular_font)))
                pdfmetrics.registerFont(TTFont("ReportUnicode-Bold", str(bold_font)))
            styles = getSampleStyleSheet(); title = self.title_label.cget("text")
            doc = SimpleDocTemplate(str(target), pagesize=landscape(A4), leftMargin=10*mm, rightMargin=10*mm, topMargin=10*mm, bottomMargin=10*mm)
            data = [[title for _, title, _ in self.columns]]
            category_rows = []
            category_total_rows = []
            changed_price_rows = []
            sale_status_rows = []
            if self.group_categories.get():
                for category, grouped_rows in self.grouped_report_rows():
                    category_rows.append(len(data))
                    data.append([category.upper()] + [""] * (len(self.columns) - 1))
                    for row in grouped_rows:
                        if row.get("__price_change"):
                            changed_price_rows.append((len(data), row["__price_change"]))
                        if row.get("__sale_status"):
                            sale_status_rows.append((len(data), row["__sale_status"]))
                        data.append([self.row_display(row, key, pdf=True) for key, _, _ in self.columns])
                    category_total_rows.append(len(data))
                    data.append(self.category_total_row(category, grouped_rows))
            else:
                for row in self.rows:
                    if row.get("__price_change"):
                        changed_price_rows.append((len(data), row["__price_change"]))
                    if row.get("__sale_status"):
                        sale_status_rows.append((len(data), row["__sale_status"]))
                    data.append([self.row_display(row, key, pdf=True) for key, _, _ in self.columns])
            data.append(self.pdf_total_row())
            available_width = landscape(A4)[0] - 20*mm
            # Preserve useful relative widths in PDF and let long text wrap.
            # Equal-width columns made Payment Method/Price Status collide while
            # leaving too much room for short numeric fields.
            pdf_weights = []
            for key, title, configured_width in self.columns:
                base = max(configured_width, self.column_minimum(key))
                if key == "item_name":
                    base = max(base, 190)
                elif key in {"payment_method", "price_status", "supplier_name"}:
                    base = max(base, 145)
                pdf_weights.append(base)
            weight_total = sum(pdf_weights)
            widths = [available_width * weight / weight_total for weight in pdf_weights]
            header_style = ParagraphStyle("ReportHeader", fontName="ReportUnicode-Bold", fontSize=7,
                                          leading=8.5, textColor=colors.white, alignment=TA_CENTER)
            body_center = ParagraphStyle("ReportBodyCenter", fontName="ReportUnicode", fontSize=7,
                                         leading=8.5, textColor=colors.HexColor("#172033"), alignment=TA_CENTER)
            body_left = ParagraphStyle("ReportBodyLeft", parent=body_center, alignment=TA_LEFT)
            changed_up_center = ParagraphStyle("ChangedUpCenter", parent=body_center,
                                               fontName="ReportUnicode-Bold", textColor=colors.HexColor("#166534"))
            changed_up_left = ParagraphStyle("ChangedUpLeft", parent=changed_up_center, alignment=TA_LEFT)
            changed_down_center = ParagraphStyle("ChangedDownCenter", parent=body_center,
                                                 fontName="ReportUnicode-Bold", textColor=colors.HexColor("#9f1239"))
            changed_down_left = ParagraphStyle("ChangedDownLeft", parent=changed_down_center, alignment=TA_LEFT)
            category_style = ParagraphStyle("ReportCategory", parent=header_style, fontSize=10,
                                            leading=12, alignment=TA_LEFT)
            total_style = ParagraphStyle("ReportTotal", parent=body_center, fontName="ReportUnicode-Bold",
                                         textColor=colors.HexColor("#12395b"))

            category_set = set(category_rows)
            category_total_set = set(category_total_rows)
            changed_directions = dict(changed_price_rows)
            sale_statuses = dict(sale_status_rows)
            left_columns = {"item_name", "customer_name", "supplier_name",
                            "payment_method", "price_status"}

            arabic_pattern = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
            rtl_styles = {}

            def pdf_paragraph(value, style):
                raw_text = "" if value is None else str(value)
                if arabic_pattern.search(raw_text):
                    raw_text = "\n".join(
                        bidi_display(arabic_reshaper.reshape(line))
                        for line in raw_text.splitlines()
                    )
                    if style.name not in rtl_styles:
                        rtl_styles[style.name] = ParagraphStyle(
                            f"{style.name}RTL", parent=style, alignment=TA_RIGHT,
                        )
                    style = rtl_styles[style.name]
                safe_text = escape(raw_text).replace("\n", "<br/>")
                return Paragraph(safe_text, style)

            wrapped_data = []
            last_row = len(data) - 1
            for row_index, row_values in enumerate(data):
                if row_index == 0:
                    row_styles = [header_style] * len(self.columns)
                elif row_index in category_set:
                    row_styles = [category_style] * len(self.columns)
                elif row_index in category_total_set:
                    row_styles = [total_style] * len(self.columns)
                elif row_index == last_row:
                    row_styles = [total_style] * len(self.columns)
                else:
                    direction = changed_directions.get(row_index)
                    sale_status = sale_statuses.get(row_index)
                    lowered = direction is not None and direction < 0 or sale_status == "discount"
                    raised = direction is not None and direction > 0 or sale_status == "changed"
                    if self.report_type.get() == "purchases" and direction is not None:
                        red_status, green_status = raised, lowered
                    else:
                        red_status, green_status = lowered, raised
                    row_styles = []
                    for key, _, _ in self.columns:
                        is_left = key in left_columns
                        if red_status:
                            row_styles.append(changed_down_left if is_left else changed_down_center)
                        elif green_status:
                            row_styles.append(changed_up_left if is_left else changed_up_center)
                        else:
                            row_styles.append(body_left if is_left else body_center)
                wrapped_data.append([pdf_paragraph(value, row_styles[index])
                                     for index, value in enumerate(row_values)])

            table = Table(wrapped_data, colWidths=widths, repeatRows=1)
            table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#16324f")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTNAME", (0,0), (-1,0), "ReportUnicode-Bold"), ("FONTNAME", (0,1), (-1,-2), "ReportUnicode"), ("FONTSIZE", (0,0), (-1,-1), 7), ("GRID", (0,0), (-1,-1), .25, colors.HexColor("#cbd5e1")), ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, colors.HexColor("#f1f5f9")]), ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#dbeafe")), ("TEXTCOLOR", (0,-1), (-1,-1), colors.HexColor("#12395b")), ("FONTNAME", (0,-1), (-1,-1), "ReportUnicode-Bold"), ("LINEABOVE", (0,-1), (-1,-1), 1.2, colors.HexColor("#2563eb")), ("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
            for row_index in category_rows:
                table.setStyle(TableStyle([("SPAN", (0,row_index), (-1,row_index)),
                                           ("BACKGROUND", (0,row_index), (-1,row_index), colors.HexColor("#1f6aa5")),
                                           ("TEXTCOLOR", (0,row_index), (-1,row_index), colors.white),
                                           ("FONTNAME", (0,row_index), (-1,row_index), "ReportUnicode-Bold"),
                                           ("FONTSIZE", (0,row_index), (-1,row_index), 10),
                                           ("TOPPADDING", (0,row_index), (-1,row_index), 7),
                                           ("BOTTOMPADDING", (0,row_index), (-1,row_index), 7)]))
            for row_index in category_total_rows:
                table.setStyle(TableStyle([("BACKGROUND", (0,row_index), (-1,row_index), colors.HexColor("#e0f2fe")),
                                           ("TEXTCOLOR", (0,row_index), (-1,row_index), colors.HexColor("#12395b")),
                                           ("FONTNAME", (0,row_index), (-1,row_index), "ReportUnicode-Bold"),
                                           ("LINEABOVE", (0,row_index), (-1,row_index), 0.8, colors.HexColor("#38bdf8")),
                                           ("TOPPADDING", (0,row_index), (-1,row_index), 6),
                                           ("BOTTOMPADDING", (0,row_index), (-1,row_index), 6)]))
            for row_index, direction in changed_price_rows:
                increase = direction > 0
                background = colors.HexColor("#ffe4e6" if increase else "#dcfce7")
                foreground = colors.HexColor("#9f1239" if increase else "#166534")
                table.setStyle(TableStyle([("BACKGROUND", (0,row_index), (-1,row_index), background),
                                           ("TEXTCOLOR", (0,row_index), (-1,row_index), foreground),
                                           ("FONTNAME", (0,row_index), (-1,row_index), "ReportUnicode-Bold")]))
            for row_index, status in sale_status_rows:
                background = colors.HexColor("#ffe4e6" if status == "discount" else "#dcfce7")
                foreground = colors.HexColor("#9f1239" if status == "discount" else "#166534")
                table.setStyle(TableStyle([("BACKGROUND", (0,row_index), (-1,row_index), background),
                                           ("TEXTCOLOR", (0,row_index), (-1,row_index), foreground),
                                           ("FONTNAME", (0,row_index), (-1,row_index), "ReportUnicode-Bold")]))
            story = [Paragraph(title, styles["Title"]), Paragraph(f"Generated {datetime.now():%m-%d-%y %H:%M}", styles["Normal"]), Spacer(1, 5*mm), table]
            payment_totals = self.pdf_payment_totals()
            if payment_totals is not None:
                story.extend([Spacer(1, 3*mm), Paragraph("Payment Method Totals", styles["Heading3"]), payment_totals])
            doc.build(story); self.status.configure(text=f"Saved PDF: {target}", text_color="#3ecf8e")
            messagebox.showinfo(APP_NAME, f"PDF saved to:\n{target}")
        except Exception as exc: messagebox.showerror(APP_NAME, f"Could not create PDF:\n{exc}")


if __name__ == "__main__":
    app = ReportApp(start_hidden="--tray" in sys.argv[1:])
    if "--toast-smoke-test" in sys.argv[1:]:
        app.notification_service.show_status(
            "HamsterPOS Reports", "Notification packaging test successful.")
        app.after(1500, app.exit_application)
    if "--smoke-test" in sys.argv[1:]:
        app.after(2500, app.exit_application)
    app.mainloop()

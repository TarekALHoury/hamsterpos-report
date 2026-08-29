from __future__ import annotations

import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "HamsterPOS Reports"


def is_frozen_executable() -> bool:
    return bool(getattr(sys, "frozen", False))


def startup_command() -> str:
    if not is_frozen_executable():
        raise RuntimeError(
            "Start with Windows can only be enabled from the installed report.exe."
        )
    return f'"{sys.executable}" --tray'


def set_start_with_windows(enabled: bool) -> None:
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


def is_start_with_windows_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return is_frozen_executable() and value == startup_command()
    except (FileNotFoundError, OSError, RuntimeError):
        return False

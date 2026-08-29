from __future__ import annotations

from pathlib import Path
from typing import Callable

import pystray
from PIL import Image


class TrayService:
    def __init__(
        self,
        icon_path: Path,
        app_name: str,
        open_app: Callable[[], None],
        check_now: Callable[[], None],
        open_settings: Callable[[], None],
        exit_app: Callable[[], None],
    ):
        image = Image.open(icon_path).convert("RGBA")
        self.icon = pystray.Icon(
            "hamsterpos_reports",
            image,
            app_name,
            menu=pystray.Menu(
                pystray.MenuItem("Open Report App", lambda _icon, _item: open_app(), default=True),
                pystray.MenuItem("Check Subscriptions Now", lambda _icon, _item: check_now()),
                pystray.MenuItem("Settings", lambda _icon, _item: open_settings()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", lambda _icon, _item: exit_app()),
            ),
        )

    def start(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        self.icon.stop()

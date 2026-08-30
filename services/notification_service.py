from __future__ import annotations

from pathlib import Path

from windows_toasts import (Toast, ToastDisplayImage, ToastImagePosition,
                            WindowsToaster)


class NotificationService:
    def __init__(self, app_name: str, icon_path=None):
        self.app_name = app_name
        self.icon_path = Path(icon_path) if icon_path else None

    def _add_app_logo(self, toast: Toast) -> None:
        if self.icon_path and self.icon_path.exists():
            toast.AddImage(ToastDisplayImage.fromPath(
                self.icon_path, altText=self.app_name,
                position=ToastImagePosition.AppLogo))

    def _show(self, toast: Toast) -> None:
        # Construct the WinRT object on the same worker thread that uses it.
        WindowsToaster(self.app_name).show_toast(toast)

    def show_subscription(self, subscription: dict, days_remaining: int) -> bool:
        customer = subscription.get("customer_name") or "Customer"
        phone = subscription.get("customer_phone") or "Not available"
        product = subscription.get("product_name") or "Subscription"
        expiry = subscription["expiry_date"].strftime("%d/%m/%Y")
        if days_remaining == 0:
            timing = "expires today."
        elif days_remaining == 1:
            timing = "expires tomorrow."
        else:
            timing = f"expires in {days_remaining} days."

        toast = Toast()
        self._add_app_logo(toast)
        toast.text_fields = [
            "Subscription Expiring Soon",
            (f"Customer: {customer}\nPhone: {phone}\n"
             f"{product} {timing}\nExpiry Date: {expiry}"),
        ]
        self._show(toast)
        return True

    def show_status(self, title: str, message: str) -> bool:
        toast = Toast()
        self._add_app_logo(toast)
        toast.text_fields = [title, message]
        self._show(toast)
        return True

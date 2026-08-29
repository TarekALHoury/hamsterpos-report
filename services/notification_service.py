from __future__ import annotations

from windows_toasts import Toast, WindowsToaster


class NotificationService:
    def __init__(self, app_name: str):
        self.app_name = app_name

    def _show(self, toast: Toast) -> None:
        # Construct the WinRT object on the same worker thread that uses it.
        WindowsToaster(self.app_name).show_toast(toast)

    def show_subscription(self, subscription: dict, days_remaining: int) -> bool:
        customer = subscription.get("customer_name") or "Customer"
        product = subscription.get("product_name") or "Subscription"
        expiry = subscription["expiry_date"].strftime("%d/%m/%Y")
        if days_remaining == 0:
            timing = "expires today."
        elif days_remaining == 1:
            timing = "expires tomorrow."
        else:
            timing = f"expires in {days_remaining} days."

        toast = Toast()
        toast.text_fields = [
            "Subscription Expiring Soon",
            f"{customer}'s {product} {timing}\nExpiry Date: {expiry}",
        ]
        self._show(toast)
        return True

    def show_status(self, title: str, message: str) -> bool:
        toast = Toast()
        toast.text_fields = [title, message]
        self._show(toast)
        return True

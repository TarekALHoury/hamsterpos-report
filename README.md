# HamsterPOS Reports

Lightweight Windows desktop reporting app for the supplied `palmed.sql` schema.

## Verified schema mapping

### Product Sales

`receipts.id -> tickets.id -> ticketlines.ticket`, with
`ticketlines.product -> products.id -> categories.id`.

- Transaction timestamp: `receipts.datenew`
- Historical sell price: `ticketlines.price`
- Quantity: `ticketlines.units`
- Sales value: `ticketlines.units * ticketlines.price`
- Barcode/name/reference: `products.code`, `products.name`, `products.reference`
- Historical buy price: correlated lookup of the latest positive purchase movement
  in `stockdiary` for the same product where `stockdiary.datenew <= receipts.datenew`.
  Ties are resolved by `stockdiary.id DESC`.

The query never uses `products.pricebuy` for historical sales.

### Purchased Products

`stockdiary.product -> products.id`, `stockdiary.supplier -> suppliers.id`, and
`products.category -> categories.id`.

- Timestamp: `stockdiary.datenew`
- Historical unit buy price: `stockdiary.price`
- Quantity: `stockdiary.units` (positive or negative according to movement reason)
- Supplier: `suppliers.name`
- Total buy: `stockdiary.units * stockdiary.price`

`purchaseorder` is only a header in this dump; it has no product/line foreign key.
It is therefore not used. The Purchased Products Reason filter uses the exact
HamsterPOS movement codes verified from the installed `MovementReason` class.
Retail sell-price fields are intentionally omitted from this report.

Settings includes a currency symbol; the UI totals, price columns, payment totals,
and PDF exports all use the configured symbol.

## Run from source

Install Python 3.11+ from python.org, then:

```powershell
cd hamster_reports
py -m pip install -r requirements.txt
py main.py
```

## Build the Windows installer

Install Inno Setup 6 once, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-installer.ps1
```

This rebuilds the latest `report.exe` and creates
`installer_output\HamsterPOSReportsSetup-1.0.1.exe`. Use `-SkipAppBuild` only when
`dist\report.exe` is already current. The installer adds an uninstall entry and a
Start Menu shortcut, with an optional Desktop shortcut. Database configuration in
`%APPDATA%\HamsterPOSReports` is preserved across upgrades and uninstallations.

The application master icon is stored in `assets\app_icon.png`, with the Windows
multi-size version in `assets\app_icon.ico`. The build embeds it in the title bar,
taskbar, executable, installer, shortcuts, and uninstall entry.

## Build `report.exe`

Run `build.ps1`. The one-file executable is written to `dist\report.exe`.
CustomTkinter gives the app a modern native desktop UI without Chromium/Electron.
Settings are stored in `%APPDATA%\HamsterPOSReports\settings.json`; the password is
encrypted with Windows DPAPI for the current Windows user.

## Close Cash Movement report

The close-cash selector uses `closedcash.hostsequence`, with its start and end time
shown in the label. Sales are joined through
`closedcash.money -> receipts.money`. Purchases are positive receipt movements from
`stockdiary` between that close-cash row's `datestart` and `dateend`. The report can
show all movements, sales only, or purchases only.

All three reports show their totals in a fixed footer outside the scrollable table.
The refresh-arrow button reruns the active report using the current filters.

Payment methods are read from `payments.payment`. Sales link exactly through
`payments.receipt = receipts.id`. Supplier purchases match `payments.supplier` and
`payments.ref` to the stock movement's supplier document/reference; a supplier
purchase without a matching payment is shown as `Credit`. Split tenders are shown
together without duplicating the product row.

The totals footer and PDF export add a card/summary for each payment method present
in the current results. For a split-method product row, its value is divided evenly
between the listed methods so payment summaries do not double-count the report.

`Group by category` adds bold category divider rows to the on-screen report and
matching full-width section headers to the PDF. Report and payment totals remain
calculated across the complete filtered result.

Each report remembers its own filter state while the app is open, including dates,
search, category, sorting, payment method, grouping, and close-cash/movement choices.
Each report also keeps its most recent result set in memory, making page switching
instant; Run Report or Refresh replaces that report's cache with fresh database data.

"""Parameterized SQL for the HamsterPOS reports.

The queries intentionally avoid CTEs/window functions for compatibility with
older MySQL versions commonly bundled with POS installations.
"""

SALES_SQL = """
SELECT
    sold_at, barcode, item_name, payment_method, buy_price, sell_price,
    regular_sell_price, explicit_discount_amount, price_level,
    SUM(qty_sold) AS qty_sold,
    SUM(sales) AS sales,
    category
FROM (
SELECT
    r.datenew AS sold_at, p.code AS barcode, p.name AS item_name,
    COALESCE((
        SELECT GROUP_CONCAT(DISTINCT
            CASE LOWER(pay.payment)
                WHEN 'cash' THEN 'Cash' WHEN 'cashrefund' THEN 'Cash'
                WHEN 'cheque' THEN 'Cheque' WHEN 'voucher' THEN 'Voucher'
                WHEN 'magcard' THEN 'Card' WHEN 'card' THEN 'Card'
                WHEN 'free' THEN 'Free' WHEN 'debt' THEN 'Debt'
                WHEN 'prepaid' THEN 'VIP Points' WHEN 'bank' THEN 'Bank'
                WHEN 'slip' THEN 'Slip' WHEN 'mobile' THEN 'Mobile'
                WHEN 'credit' THEN 'Credit' ELSE pay.payment
            END ORDER BY pay.payment SEPARATOR ', ')
        FROM payments pay WHERE pay.receipt = r.id
    ), '') AS payment_method,
    COALESCE((
        SELECT sd.price
        FROM stockdiary sd
        WHERE sd.product = tl.product
          AND sd.datenew <= r.datenew
          AND sd.units > 0
          AND (%(purchase_reason)s IS NULL OR sd.reason = %(purchase_reason)s)
        ORDER BY sd.datenew DESC, sd.id DESC
        LIMIT 1
    ), 0) AS buy_price,
    tl.price AS sell_price,
    p.pricesell AS regular_sell_price,
    COALESCE((
        SELECT ABS(discount_tl.units * discount_tl.price)
        FROM ticketlines discount_tl
        WHERE discount_tl.ticket = tl.ticket
          AND discount_tl.line = tl.line + 1
          AND discount_tl.product IS NULL
          AND CONVERT(discount_tl.attributes USING utf8mb4) LIKE '%%Line Discount%%'
        LIMIT 1
    ), 0) AS explicit_discount_amount,
    tl.price_level AS price_level,
    tl.units AS qty_sold,
    (tl.units * tl.price) AS sales,
    c.name AS category
FROM receipts r
JOIN tickets t ON t.id = r.id
JOIN ticketlines tl ON tl.ticket = t.id
JOIN products p ON p.id = tl.product
LEFT JOIN categories c ON c.id = p.category
WHERE r.datenew >= %(start_at)s
  AND r.datenew <= %(end_at)s
  AND tl.product IS NOT NULL
  AND tl.units <> 0
  AND (%(category_id)s IS NULL OR p.category = %(category_id)s)
  AND (
       %(search)s = ''
       OR p.code LIKE %(search_like)s
       OR p.name LIKE %(search_like)s
       OR p.reference LIKE %(search_like)s
  )
) sale_rows
WHERE (%(payment_method)s = 'All' OR FIND_IN_SET(%(payment_method)s, REPLACE(payment_method, ', ', ',')) > 0)
GROUP BY sold_at, barcode, item_name, payment_method, buy_price, sell_price,
         regular_sell_price, explicit_discount_amount, price_level, category
ORDER BY {order_clause}
"""

PURCHASES_SQL = """
SELECT
    purchased_at, barcode, item_name, payment_method, buy_price, supplier_name,
    SUM(qty_purchased) AS qty_purchased,
    SUM(total_buy_price) AS total_buy_price,
    category
FROM (
SELECT
    CASE WHEN TIME(sd.datenew) = '00:00:00' THEN COALESCE((
        SELECT purchase_receipt.datenew
        FROM payments purchase_payment
        JOIN receipts purchase_receipt ON purchase_receipt.id = purchase_payment.receipt
        WHERE purchase_payment.supplier = sd.supplier
          AND (purchase_payment.ref = sd.id OR purchase_payment.ref = sd.supplierdoc)
          AND DATE(purchase_receipt.datenew) = DATE(sd.datenew)
        ORDER BY (purchase_payment.ref = sd.id) DESC, purchase_receipt.datenew DESC
        LIMIT 1
    ), po.datenew, sd.datenew) ELSE sd.datenew END AS purchased_at,
    p.code AS barcode, p.name AS item_name,
    CASE WHEN sd.supplier IS NULL THEN '' ELSE COALESCE((
        SELECT CASE LOWER(pay.payment)
                WHEN 'cash' THEN 'Cash'
                WHEN 'cheque' THEN 'Cheque'
                WHEN 'supcheque' THEN 'Cheque'
                WHEN 'subcheque' THEN 'Cheque'
                WHEN 'bank' THEN 'Bank'
                WHEN 'supbank' THEN 'Bank'
                WHEN 'credit' THEN 'Credit'
                ELSE pay.payment
            END
        FROM payments pay
        JOIN receipts pay_receipt ON pay_receipt.id = pay.receipt
        WHERE pay.supplier = sd.supplier
          AND (pay.ref = sd.supplierdoc OR pay.ref = sd.id)
          AND DATE(pay_receipt.datenew) = DATE(sd.datenew)
        ORDER BY pay_receipt.datenew DESC, pay.id DESC
        LIMIT 1
    ), 'Credit') END AS payment_method,
    sd.price AS buy_price,
    COALESCE(s.name, '') AS supplier_name, sd.units AS qty_purchased,
    (sd.units * sd.price) AS total_buy_price,
    c.name AS category
FROM stockdiary sd
JOIN products p ON p.id = sd.product
LEFT JOIN orderdiary od ON od.id = sd.id
LEFT JOIN purchaseorder po ON po.id = od.po
LEFT JOIN categories c ON c.id = p.category
LEFT JOIN suppliers s ON s.id = sd.supplier
WHERE sd.reason IN (1, -2, 4, -4, -8, -3, -6, -5, -7, 1000)
  AND (%(reason)s IS NULL OR sd.reason = %(reason)s)
  AND (%(category_id)s IS NULL OR p.category = %(category_id)s)
  AND (
       %(search)s = ''
       OR p.code LIKE %(search_like)s
       OR p.name LIKE %(search_like)s
       OR p.reference LIKE %(search_like)s
  )
) purchase_rows
WHERE purchased_at >= %(start_at)s
  AND purchased_at <= %(end_at)s
  AND (%(payment_method)s = 'All' OR FIND_IN_SET(%(payment_method)s, REPLACE(payment_method, ', ', ',')) > 0)
GROUP BY purchased_at, barcode, item_name, payment_method, buy_price,
         supplier_name, category
ORDER BY {order_clause}
"""

SALES_COLUMNS = (
    ("sold_at", "Date & Time", 145),
    ("barcode", "Item Barcode", 130),
    ("item_name", "Item Name", 230),
    ("payment_method", "Payment Method", 135),
    ("buy_price", "Buy Price", 95),
    ("sell_price", "Sell Price", 95),
    ("price_status", "Price Status", 190),
    ("qty_sold", "QTY Sold", 90),
    ("sales", "Sales", 105),
)

PURCHASE_COLUMNS = (
    ("purchased_at", "Date & Time", 145),
    ("barcode", "Item Barcode", 125),
    ("item_name", "Item Name", 210),
    ("payment_method", "Payment Method", 135),
    ("buy_price", "Item Buy Price", 110),
    ("supplier_name", "Supplier Name", 160),
    ("qty_purchased", "QTY Purchased", 105),
    ("total_buy_price", "Total Buy Price", 115),
)

CLOSE_CASH_SQL = """
SELECT movement_at, ticket_no, barcode, item_name, payment_method,
       sell_price, regular_sell_price, explicit_discount_amount, price_level,
       SUM(qty_in) AS qty_in, SUM(qty_out) AS qty_out,
       SUM(total_sold) AS total_sold, SUM(total_bought) AS total_bought,
       category, movement_type
FROM (
    SELECT r.datenew AS movement_at, t.ticketid AS ticket_no,
           p.code AS barcode, p.name AS item_name,
           COALESCE((SELECT GROUP_CONCAT(DISTINCT
               CASE LOWER(pay.payment)
                   WHEN 'cash' THEN 'Cash' WHEN 'cashrefund' THEN 'Cash'
                   WHEN 'cheque' THEN 'Cheque' WHEN 'voucher' THEN 'Voucher'
                   WHEN 'magcard' THEN 'Card' WHEN 'card' THEN 'Card'
                   WHEN 'free' THEN 'Free' WHEN 'debt' THEN 'Debt'
                   WHEN 'prepaid' THEN 'VIP Points' WHEN 'bank' THEN 'Bank'
                   WHEN 'slip' THEN 'Slip' WHEN 'mobile' THEN 'Mobile'
                   WHEN 'credit' THEN 'Credit' ELSE pay.payment
               END ORDER BY pay.payment SEPARATOR ', ')
               FROM payments pay WHERE pay.receipt = r.id), '') AS payment_method,
           tl.price AS sell_price,
           p.pricesell AS regular_sell_price,
           COALESCE((
               SELECT ABS(discount_tl.units * discount_tl.price)
               FROM ticketlines discount_tl
               WHERE discount_tl.ticket = tl.ticket
                 AND discount_tl.line = tl.line + 1
                 AND discount_tl.product IS NULL
                 AND CONVERT(discount_tl.attributes USING utf8mb4) LIKE '%%Line Discount%%'
               LIMIT 1
           ), 0) AS explicit_discount_amount,
           tl.price_level AS price_level,
           0 AS qty_in, tl.units AS qty_out,
           (tl.units * tl.price) AS total_sold, 0 AS total_bought,
           c.name AS category, 'Sold' AS movement_type
    FROM closedcash cc
    JOIN receipts r ON r.money = cc.money
    JOIN tickets t ON t.id = r.id
    JOIN ticketlines tl ON tl.ticket = t.id
    JOIN products p ON p.id = tl.product
    LEFT JOIN categories c ON c.id = p.category
    WHERE cc.money = %(money)s AND tl.product IS NOT NULL AND tl.units <> 0
      AND %(movement_filter)s IN ('All', 'Sold')

    UNION ALL

    SELECT CASE WHEN TIME(sd.datenew) = '00:00:00' THEN COALESCE((
               SELECT purchase_receipt.datenew
               FROM payments purchase_payment
               JOIN receipts purchase_receipt ON purchase_receipt.id = purchase_payment.receipt
               WHERE purchase_payment.supplier = sd.supplier
                 AND (purchase_payment.ref = sd.id OR purchase_payment.ref = sd.supplierdoc)
                 AND DATE(purchase_receipt.datenew) = DATE(sd.datenew)
               ORDER BY (purchase_payment.ref = sd.id) DESC, purchase_receipt.datenew DESC
               LIMIT 1
           ), sd.datenew) ELSE sd.datenew END, NULL, p.code, p.name,
           CASE WHEN sd.supplier IS NULL THEN '' ELSE COALESCE((
               SELECT CASE LOWER(pay.payment)
                       WHEN 'cash' THEN 'Cash'
                       WHEN 'cheque' THEN 'Cheque'
                       WHEN 'supcheque' THEN 'Cheque'
                       WHEN 'subcheque' THEN 'Cheque'
                       WHEN 'bank' THEN 'Bank'
                       WHEN 'supbank' THEN 'Bank'
                       WHEN 'credit' THEN 'Credit'
                       ELSE pay.payment
                   END
               FROM payments pay
               JOIN receipts pay_receipt ON pay_receipt.id = pay.receipt
               WHERE pay.supplier = sd.supplier
                 AND (pay.ref = sd.supplierdoc OR pay.ref = sd.id)
                 AND DATE(pay_receipt.datenew) = DATE(sd.datenew)
               ORDER BY pay_receipt.datenew DESC, pay.id DESC
               LIMIT 1
           ), 'Credit') END, 0, 0, 0, 0, sd.units, 0, 0,
           (sd.units * sd.price), c.name, 'Purchased'
    FROM closedcash cc
    JOIN stockdiary sd ON (CASE WHEN TIME(sd.datenew) = '00:00:00' THEN COALESCE((
          SELECT purchase_receipt.datenew
          FROM payments purchase_payment
          JOIN receipts purchase_receipt ON purchase_receipt.id = purchase_payment.receipt
          WHERE purchase_payment.supplier = sd.supplier
            AND (purchase_payment.ref = sd.id OR purchase_payment.ref = sd.supplierdoc)
            AND DATE(purchase_receipt.datenew) = DATE(sd.datenew)
          ORDER BY (purchase_payment.ref = sd.id) DESC, purchase_receipt.datenew DESC
          LIMIT 1
      ), sd.datenew) ELSE sd.datenew END) >= cc.datestart
      AND (CASE WHEN TIME(sd.datenew) = '00:00:00' THEN COALESCE((
          SELECT purchase_receipt.datenew
          FROM payments purchase_payment
          JOIN receipts purchase_receipt ON purchase_receipt.id = purchase_payment.receipt
          WHERE purchase_payment.supplier = sd.supplier
            AND (purchase_payment.ref = sd.id OR purchase_payment.ref = sd.supplierdoc)
            AND DATE(purchase_receipt.datenew) = DATE(sd.datenew)
          ORDER BY (purchase_payment.ref = sd.id) DESC, purchase_receipt.datenew DESC
          LIMIT 1
      ), sd.datenew) ELSE sd.datenew END) <= COALESCE(cc.dateend, NOW())
    JOIN products p ON p.id = sd.product
    LEFT JOIN categories c ON c.id = p.category
    WHERE cc.money = %(money)s AND sd.units > 0
      AND (%(purchase_reason)s IS NULL OR sd.reason = %(purchase_reason)s)
      AND %(movement_filter)s IN ('All', 'Purchased')
) movements
WHERE (%(category_id)s IS NULL OR category = %(category_name)s)
  AND (%(payment_method)s = 'All' OR FIND_IN_SET(%(payment_method)s, REPLACE(payment_method, ', ', ',')) > 0)
  AND (%(search)s = '' OR barcode LIKE %(search_like)s
       OR item_name LIKE %(search_like)s)
GROUP BY movement_at, ticket_no, barcode, item_name, payment_method,
         sell_price, regular_sell_price, explicit_discount_amount, price_level,
         category, movement_type
ORDER BY {order_clause}
"""

CLOSE_CASH_COLUMNS = (
    ("movement_at", "Date & Time", 145),
    ("ticket_no", "Ticket No.", 95),
    ("barcode", "Item Barcode", 125),
    ("item_name", "Item Name", 230),
    ("payment_method", "Payment Method", 135),
    ("price_status", "Price Status", 190),
    ("qty_in", "In", 85),
    ("qty_out", "Out", 85),
    ("total_sold", "Total Sold", 115),
    ("total_bought", "Total Bought", 115),
)

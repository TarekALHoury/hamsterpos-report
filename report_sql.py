"""Parameterized SQL for the HamsterPOS reports.

The queries intentionally avoid CTEs/window functions for compatibility with
older MySQL versions commonly bundled with POS installations.
"""

SALES_SQL = """
SELECT
    sold_at, barcode, item_name, payment_method, sell_price, actual_sell_price,
    regular_sell_price, explicit_discount_amount, price_level,
    SUM(qty_sold) AS qty_sold,
    SUM(sales - explicit_discount_amount) AS sales,
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
    p.pricesell AS sell_price,
    tl.price AS actual_sell_price,
    p.pricesell AS regular_sell_price,
    COALESCE((
        SELECT ABS(discount_tl.units * discount_tl.price)
        FROM ticketlines discount_tl
        WHERE discount_tl.ticket = tl.ticket
          AND discount_tl.line = tl.line + 1
          AND discount_tl.product IS NULL
          AND (CONVERT(discount_tl.attributes USING utf8mb4) LIKE '%%Line Discount%%'
               OR CONVERT(discount_tl.attributes USING utf8mb4) LIKE '%%Total Discount%%')
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
GROUP BY sold_at, barcode, item_name, payment_method, sell_price, actual_sell_price,
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
          AND (purchase_payment.ref = sd.id OR
               (COALESCE(TRIM(sd.supplierdoc), '') <> '' AND purchase_payment.ref = sd.supplierdoc))
          AND DATE(purchase_receipt.datenew) = DATE(sd.datenew)
        ORDER BY (purchase_payment.ref = sd.id) DESC, purchase_receipt.datenew DESC
        LIMIT 1
    ), po.datenew, sd.datenew) ELSE sd.datenew END AS purchased_at,
    p.code AS barcode, p.name AS item_name,
    CASE WHEN sd.reason NOT IN (1, -2) THEN
        CASE sd.reason
            WHEN 4 THEN 'Adjust - Add' WHEN -4 THEN 'Adjust - Minus'
            WHEN -8 THEN 'Subtract' WHEN -3 THEN 'Breakage'
            WHEN -6 THEN 'Free' WHEN -5 THEN 'Sample - Out'
            WHEN -7 THEN 'Used' WHEN 1000 THEN 'Transfer'
            ELSE CONCAT('Reason ', sd.reason)
        END
    WHEN sd.supplier IS NULL THEN '' ELSE COALESCE((
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
          AND (pay.ref = sd.id OR
               (COALESCE(TRIM(sd.supplierdoc), '') <> '' AND pay.ref = sd.supplierdoc) OR
               (COALESCE(TRIM(sd.supplierdoc), '') = '' AND COALESCE(TRIM(pay.ref), '') = ''
                AND pay_receipt.datenew = sd.datenew))
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
    ("sold_at", "Date & Time", 125),
    ("barcode", "Item Barcode", 105),
    ("item_name", "Item Name", 180),
    ("payment_method", "Payment Method", 125),
    ("sell_price", "Sell Price", 90),
    ("price_status", "Price Status", 175),
    ("qty_sold", "QTY Sold", 90),
    ("sales", "Sales", 105),
)

PURCHASE_COLUMNS = (
    ("purchased_at", "Date & Time", 125),
    ("barcode", "Item Barcode", 105),
    ("item_name", "Item Name", 180),
    ("payment_method", "Payment Method", 125),
    ("buy_price", "Item Buy Price", 105),
    ("supplier_name", "Supplier Name", 140),
    ("qty_purchased", "QTY Purchased", 105),
    ("total_buy_price", "Total Buy Price", 115),
)

CLOSE_CASH_SQL = """
SELECT movement_at, ticket_no, barcode, item_name, payment_method,
       sell_price, regular_sell_price, explicit_discount_amount, price_level,
       SUM(qty_in) AS qty_in, SUM(qty_out) AS qty_out,
       SUM(total_sold - CASE WHEN movement_type = 'Sold'
                             THEN explicit_discount_amount ELSE 0 END) AS total_sold,
       SUM(total_bought) AS total_bought,
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
                 AND (CONVERT(discount_tl.attributes USING utf8mb4) LIKE '%%Line Discount%%'
                      OR CONVERT(discount_tl.attributes USING utf8mb4) LIKE '%%Total Discount%%')
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

    SELECT sd.datenew AS movement_at,
           (SELECT refund_ticket.ticketid
            FROM ticketlines refund_line
            JOIN receipts refund_receipt ON refund_receipt.id = refund_line.ticket
            JOIN tickets refund_ticket ON refund_ticket.id = refund_receipt.id
            WHERE refund_line.product = sd.product
              AND refund_receipt.datenew < sd.datenew
            ORDER BY refund_receipt.datenew DESC, refund_line.id DESC
            LIMIT 1) AS ticket_no,
           p.code, p.name,
           COALESCE((SELECT GROUP_CONCAT(DISTINCT
               CASE LOWER(refund_pay.payment)
                   WHEN 'cash' THEN 'Cash' WHEN 'cashrefund' THEN 'Cash'
                   WHEN 'cheque' THEN 'Cheque' WHEN 'voucher' THEN 'Voucher'
                   WHEN 'magcard' THEN 'Card' WHEN 'card' THEN 'Card'
                   WHEN 'free' THEN 'Free' WHEN 'debt' THEN 'Debt'
                   WHEN 'prepaid' THEN 'VIP Points' WHEN 'bank' THEN 'Bank'
                   WHEN 'slip' THEN 'Slip' WHEN 'mobile' THEN 'Mobile'
                   WHEN 'credit' THEN 'Credit' ELSE refund_pay.payment
               END ORDER BY refund_pay.payment SEPARATOR ', ')
               FROM payments refund_pay
               WHERE refund_pay.receipt = (
                   SELECT refund_receipt.id
                   FROM ticketlines refund_line
                   JOIN receipts refund_receipt ON refund_receipt.id = refund_line.ticket
                   JOIN tickets refund_ticket ON refund_ticket.id = refund_receipt.id
                   WHERE refund_line.product = sd.product
                     AND refund_receipt.datenew < sd.datenew
                   ORDER BY refund_receipt.datenew DESC, refund_line.id DESC
                   LIMIT 1
               )), '') AS payment_method,
           sd.price AS sell_price, p.pricesell AS regular_sell_price,
           0 AS explicit_discount_amount, 0 AS price_level,
           sd.units AS qty_in, 0 AS qty_out,
           -(sd.units * sd.price) AS total_sold, 0 AS total_bought,
           c.name AS category, 'Refund' AS movement_type
    FROM closedcash cc
    JOIN stockdiary sd ON sd.datenew >= cc.datestart
                       AND sd.datenew <= COALESCE(cc.dateend, NOW())
    JOIN products p ON p.id = sd.product
    LEFT JOIN categories c ON c.id = p.category
    WHERE cc.money = %(money)s
      AND sd.reason = 2 AND sd.units > 0
      AND %(movement_filter)s IN ('All', 'Sold')

    UNION ALL

    SELECT CASE WHEN TIME(sd.datenew) = '00:00:00' THEN COALESCE((
               SELECT purchase_receipt.datenew
               FROM payments purchase_payment
               JOIN receipts purchase_receipt ON purchase_receipt.id = purchase_payment.receipt
               WHERE purchase_payment.supplier = sd.supplier
                 AND (purchase_payment.ref = sd.id OR
                      (COALESCE(TRIM(sd.supplierdoc), '') <> '' AND purchase_payment.ref = sd.supplierdoc))
                 AND DATE(purchase_receipt.datenew) = DATE(sd.datenew)
               ORDER BY (purchase_payment.ref = sd.id) DESC, purchase_receipt.datenew DESC
               LIMIT 1
           ), sd.datenew) ELSE sd.datenew END, NULL, p.code, p.name,
           CASE WHEN sd.reason NOT IN (1, -2) THEN
               CASE sd.reason
                   WHEN 4 THEN 'Adjust - Add' WHEN -4 THEN 'Adjust - Minus'
                   WHEN -8 THEN 'Subtract' WHEN -3 THEN 'Breakage'
                   WHEN -6 THEN 'Free' WHEN -5 THEN 'Sample - Out'
                   WHEN -7 THEN 'Used' WHEN 1000 THEN 'Transfer'
                   ELSE CONCAT('Reason ', sd.reason)
               END
           WHEN sd.supplier IS NULL THEN '' ELSE COALESCE((
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
                 AND (pay.ref = sd.id OR
                      (COALESCE(TRIM(sd.supplierdoc), '') <> '' AND pay.ref = sd.supplierdoc) OR
                      (COALESCE(TRIM(sd.supplierdoc), '') = '' AND COALESCE(TRIM(pay.ref), '') = ''
                       AND pay_receipt.datenew = sd.datenew))
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
            AND (purchase_payment.ref = sd.id OR
                 (COALESCE(TRIM(sd.supplierdoc), '') <> '' AND purchase_payment.ref = sd.supplierdoc))
            AND DATE(purchase_receipt.datenew) = DATE(sd.datenew)
          ORDER BY (purchase_payment.ref = sd.id) DESC, purchase_receipt.datenew DESC
          LIMIT 1
      ), sd.datenew) ELSE sd.datenew END) >= cc.datestart
      AND (CASE WHEN TIME(sd.datenew) = '00:00:00' THEN COALESCE((
          SELECT purchase_receipt.datenew
          FROM payments purchase_payment
          JOIN receipts purchase_receipt ON purchase_receipt.id = purchase_payment.receipt
          WHERE purchase_payment.supplier = sd.supplier
            AND (purchase_payment.ref = sd.id OR
                 (COALESCE(TRIM(sd.supplierdoc), '') <> '' AND purchase_payment.ref = sd.supplierdoc))
            AND DATE(purchase_receipt.datenew) = DATE(sd.datenew)
          ORDER BY (purchase_payment.ref = sd.id) DESC, purchase_receipt.datenew DESC
          LIMIT 1
      ), sd.datenew) ELSE sd.datenew END) <= COALESCE(cc.dateend, NOW())
    JOIN products p ON p.id = sd.product
    LEFT JOIN categories c ON c.id = p.category
    WHERE cc.money = %(money)s AND sd.units > 0 AND sd.reason <> 2
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
    ("movement_at", "Date & Time", 125),
    ("ticket_no", "Ticket No.", 80),
    ("barcode", "Item Barcode", 105),
    ("item_name", "Item Name", 180),
    ("payment_method", "Payment Method", 120),
    ("price_status", "Price Status", 175),
    ("qty_in", "In", 65),
    ("qty_out", "Out", 65),
    ("total_sold", "Total Sold", 100),
    ("total_bought", "Total Bought", 105),
)

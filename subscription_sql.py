"""SQL for sold HamsterPOS subscription ticket lines."""

SUBSCRIPTION_REPORT_COLUMNS = (
    ("customer_name", "Customer", 150),
    ("customer_phone", "Phone Number", 120),
    ("ticket_number", "Ticket No.", 85),
    ("product_name", "Subscription Product", 180),
    ("payment_method", "Payment Method", 125),
    ("start_date", "Start Date", 100),
    ("expiry_date", "Expiry Date", 100),
    ("days_remaining", "Days Remaining", 115),
    ("expiry_status", "Status", 130),
    ("amount", "Amount", 100),
)

SUBSCRIPTION_REPORT_SQL = """
SELECT * FROM (
SELECT
    COALESCE(NULLIF(TRIM(c.name), ''), 'Customer') AS customer_name,
    COALESCE(NULLIF(TRIM(c.phone), ''), '') AS customer_phone,
    t.ticketid AS ticket_number,
    COALESCE(NULLIF(TRIM(p.name), ''), 'Subscription') AS product_name,
    COALESCE((
        SELECT GROUP_CONCAT(DISTINCT
            CASE LOWER(pay.payment)
                WHEN 'cash' THEN 'Cash' WHEN 'cashrefund' THEN 'Cash'
                WHEN 'cheque' THEN 'Cheque' WHEN 'voucher' THEN 'Voucher'
                WHEN 'magcard' THEN 'Card' WHEN 'card' THEN 'Card'
                WHEN 'ccard' THEN 'Card'
                WHEN 'free' THEN 'Free' WHEN 'debt' THEN 'Debt'
                WHEN 'prepaid' THEN 'VIP Points' WHEN 'bank' THEN 'Bank'
                WHEN 'slip' THEN 'Slip' WHEN 'mobile' THEN 'Mobile'
                WHEN 'credit' THEN 'Credit' ELSE pay.payment
            END ORDER BY pay.payment SEPARATOR ', ')
        FROM payments pay WHERE pay.receipt = r.id
    ), '') AS payment_method,
    tl.ss AS start_date,
    tl.se AS expiry_date,
    DATEDIFF(tl.se, CURDATE()) AS days_remaining,
    CASE
        WHEN tl.se <= CURDATE() THEN 'Expired'
        WHEN DATEDIFF(tl.se, CURDATE()) BETWEEN 1 AND %(notify_days)s
            THEN 'Ending Soon'
        ELSE 'Active'
    END AS expiry_status,
    (tl.units * tl.price) AS amount
FROM ticketlines tl
JOIN tickets t ON t.id = tl.ticket
JOIN receipts r ON r.id = t.id
JOIN customers c ON c.id = t.customer
JOIN products p ON p.id = tl.product
WHERE tl.ss IS NOT NULL
  AND tl.se IS NOT NULL
  AND tl.units > 0
  AND tl.se >= DATE(%(start_at)s)
  AND tl.se <= DATE(%(end_at)s)
  AND (
       %(search)s = ''
       OR c.name LIKE %(search_like)s ESCAPE '!'
       OR c.phone LIKE %(search_like)s ESCAPE '!'
       OR CAST(t.ticketid AS CHAR) LIKE %(search_like)s ESCAPE '!'
       OR p.name LIKE %(search_like)s ESCAPE '!'
       OR p.code LIKE %(search_like)s ESCAPE '!'
  )
) subscription_rows
WHERE (%(payment_method)s = 'All'
       OR FIND_IN_SET(%(payment_method)s, REPLACE(payment_method, ', ', ',')) > 0)
  AND (
       %(subscription_status)s = 'All'
       OR (%(subscription_status)s = 'Active' AND days_remaining > %(notify_days)s)
       OR (%(subscription_status)s = 'Expired' AND days_remaining <= 0)
       OR (%(subscription_status)s = 'Ending Soon'
           AND days_remaining BETWEEN 1 AND %(notify_days)s)
  )
ORDER BY {order_clause}
"""

SUBSCRIPTIONS_EXPIRING_SQL = """
SELECT
    CAST(tl.ID AS CHAR) AS subscription_line_id,
    t.id AS ticket_id,
    t.ticketid AS ticket_number,
    t.customer AS customer_id,
    COALESCE(NULLIF(TRIM(c.name), ''), 'Customer') AS customer_name,
    COALESCE(NULLIF(TRIM(c.phone), ''), '') AS customer_phone,
    tl.ss AS start_date,
    tl.se AS expiry_date,
    (tl.units * tl.price) AS amount,
    p.id AS product_id,
    COALESCE(NULLIF(TRIM(p.name), ''), 'Subscription') AS product_name
FROM ticketlines tl
JOIN tickets t ON t.id = tl.ticket
JOIN receipts r ON r.id = t.id
JOIN customers c ON c.id = t.customer
JOIN products p ON p.id = tl.product
WHERE tl.ss IS NOT NULL
  AND tl.se IS NOT NULL
  AND tl.units > 0
  AND tl.se >= %(today)s
  AND tl.se <= %(last_date)s
ORDER BY tl.se, c.name, t.ticketid, tl.ID
"""

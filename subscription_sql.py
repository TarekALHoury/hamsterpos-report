"""SQL for sold HamsterPOS subscription ticket lines."""

SUBSCRIPTIONS_EXPIRING_SQL = """
SELECT
    CAST(tl.ID AS CHAR) AS subscription_line_id,
    t.id AS ticket_id,
    t.ticketid AS ticket_number,
    t.customer AS customer_id,
    COALESCE(NULLIF(TRIM(c.name), ''), 'Customer') AS customer_name,
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

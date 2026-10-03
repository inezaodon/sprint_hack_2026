-- severity: error
-- description: Every sold listing has an order line for the same channel + SKU, and every order line has a listing on its channel (lines already flagged with a NULL store, and the listing they would match by SKU number, are reported by store_id_missing instead).
SELECT 'sold_listing_without_order_line' AS reason, s.listing_key AS row_key, s.channel, s.sku, s.price AS amount
FROM fct_listings s
WHERE s.status = 'sold'
  AND NOT EXISTS (SELECT 1 FROM fct_order_lines l WHERE l.channel = s.channel AND l.sku = s.sku)
  AND NOT EXISTS (SELECT 1 FROM fct_order_lines l    -- flagged line whose SKU lost its store code ('GWM--109574')
                  WHERE l.channel = s.channel AND l.store_id IS NULL
                    AND regexp_extract(l.sku, '([0-9]+)$', 1) = regexp_extract(s.sku, '([0-9]+)$', 1))
UNION ALL
SELECT 'order_line_without_listing', l.order_key || '#' || l.line_no, l.channel, l.sku, l.sale_price
FROM fct_order_lines l
WHERE NOT EXISTS (SELECT 1 FROM fct_listings s WHERE s.channel = l.channel AND s.sku = l.sku)
  AND l.store_id IS NOT NULL
UNION ALL
SELECT 'order_line_listing_not_sold', l.order_key || '#' || l.line_no, l.channel, l.sku, l.sale_price
FROM fct_order_lines l
WHERE EXISTS (SELECT 1 FROM fct_listings s WHERE s.channel = l.channel AND s.sku = l.sku)
  AND NOT EXISTS (SELECT 1 FROM fct_listings s WHERE s.channel = l.channel AND s.sku = l.sku AND s.status = 'sold')

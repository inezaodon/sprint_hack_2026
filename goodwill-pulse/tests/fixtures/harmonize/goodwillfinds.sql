-- GoodwillFinds: ISO text with Eastern offsets; store from vendor (one typo'd); messy product_type casing;
-- a duplicated order row (same name, new id); a TEST order.
INSERT INTO orders VALUES
 (3001, '#GF10001', '2026-09-30T19:41:07-04:00', 'partially_refunded', 501, 'h1',   'MI', 60.00, 7.00, 4.20, 71.20, 9.00, 'USD'),
 (3002, '#GF10002', '2026-03-08T01:30:00-05:00', 'paid',               502, 'h2',   'OH', 20.00, 6.00, 1.20, 27.20, 3.00, 'USD'),
 (3003, '#GF10003', '2026-09-29T12:00:00-04:00', 'paid',               503, 'TEST', 'IN', 10.00, 6.00, 0.60, 16.60, 1.50, 'USD'),
 (3009, '#GF10001', '2026-09-30T19:41:07-04:00', 'partially_refunded', 501, 'h1',   'MI', 60.00, 7.00, 4.20, 71.20, 9.00, 'USD');  -- dup
INSERT INTO line_items VALUES
 (4001, 3001, 'UP-13-000020', 'Vase',  'Goodwil Michiana #13',  'home decor', 25.00, 1),   -- vendor typo
 (4002, 3001, 'UP-13-000021', 'Ring',  'Goodwill Michiana #13', 'JEWELRY',    35.00, 1),   -- not in item master
 (4003, 3002, 'UP-13-000022', 'Shirt', 'Goodwill Michiana # 13','clothing',   20.00, 1),
 (4004, 3003, 'UP-13-000023', 'Test',  'Goodwill Michiana #13', 'Toys',       10.00, 1),
 (4009, 3009, 'UP-13-000020', 'Vase',  'Goodwil Michiana #13',  'home decor', 25.00, 1);
INSERT INTO refunds VALUES (6001, 3001, '2026-10-01T00:30:00-04:00', 25.00, 'damaged in transit');
INSERT INTO payouts VALUES (7001, '2026-10-02', 66.20, 'paid', 80.00, 9.00, 4.80),
                           (7002, '2026-10-14', 10.00, 'paid', 10.00, 0.00, 0.00);
INSERT INTO products VALUES
 (8001, 'UP-13-000020', 'Vase', 'Goodwil Michiana #13', 'home decor', '2026-09-20T10:00:00-04:00', '2026-09-30T19:41:07-04:00', 'sold', 25.00);

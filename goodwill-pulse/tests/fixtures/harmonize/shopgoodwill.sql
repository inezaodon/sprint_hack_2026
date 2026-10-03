-- ShopGoodwill: naive PACIFIC timestamps; shipping/handling on the first line only; a TEST buyer; a duplicate fee row.
INSERT INTO auctions VALUES
 (5001, 'UP-03-000001', 'Necklace', 'Jewelry & Gemstones', '2026-09-23 10:00:00', '2026-09-30 19:00:00', 10.00, 7, 100.00, 'Sold',   NULL),
 (5002, 'UP-03-000002', 'Jacket',   'Clothing',            '2026-09-23 10:00:00', '2026-09-30 19:00:00',  9.99, 3,  50.00, 'Sold',   NULL),
 (5003, 'UP-05-000003', 'Watch',    'Watches',             '2026-03-01 10:00:00', '2026-03-07 19:00:00', 10.00, 4,  80.00, 'Sold',   NULL),
 (5004, 'UP-05-000004', 'Painting', 'Art',                 '2026-03-01 10:00:00', '2026-03-07 19:00:00', 10.00, 2,  40.00, 'Sold',   NULL),
 (5006, 'UP-03-000009', 'Figurine', 'Collectibles',        '2026-08-03 10:00:00', '2026-08-10 19:00:00', 15.00, 0,   NULL, 'Unsold', NULL),
 (5007, 'UP-03-000009', 'Figurine', 'Collectibles',        '2026-09-25 10:00:00', '2026-10-05 19:00:00', 12.00, 0,   NULL, 'Open',   5006),
 (5011, 'UP-05-000040', 'Lamp',     'Home',                '2025-10-25 10:00:00', '2025-11-01 19:00:00', 10.00, 1,  30.00, 'Sold',   NULL),
 (5012, 'UP-05-000041', 'Vase',     'Home',                '2026-03-01 10:00:00', '2026-03-07 19:00:00', 10.00, 1,  30.00, 'Sold',   NULL),
 (5010, 'UP-08-000030', 'Test toy', 'Toys',                '2026-09-25 10:00:00', '2026-09-29 19:00:00', 10.00, 1,  20.00, 'Sold',   NULL);
INSERT INTO sales VALUES
 -- 8:30 PM PDT Sep 30 = 03:30 UTC Oct 1 = 11:30 PM ET Sep 30  -> business_date 2026-09-30
 (9001, 5001, 'B100', 'IN', '2026-09-30 20:30:00', 100.00, 12.50, 6.00, 6.00, 'CreditCard', false, NULL, NULL),
 (9001, 5002, 'B100', 'IN', '2026-09-30 20:30:00',  50.00,  0.00, 0.00, 3.00, 'CreditCard', true,  50.00, '2026-10-01 22:00:00'),
 -- spring-forward day: 01:30 PST and 03:30 PDT are only ONE hour apart in UTC
 (9002, 5003, 'B200', 'MI', '2026-03-08 01:30:00',  80.00,  9.00, 3.00, 4.80, 'PayPal',     false, NULL, NULL),
 (9003, 5004, 'B200', 'MI', '2026-03-08 03:30:00',  40.00,  8.00, 3.00, 2.40, 'PayPal',     false, NULL, NULL),
 -- fall-back day: 01:07:51 happens twice; take the FIRST (PDT) -> 08:07:51 UTC
 (9005, 5011, 'B300', 'IN', '2025-11-02 01:07:51',  30.00,  5.00, 3.00, 1.80, 'PayPal',     false, NULL, NULL),
 -- spring-forward gap: 02:30 does not exist -> shifted forward to 03:30 PDT = 10:30 UTC
 (9006, 5012, 'B300', 'IN', '2026-03-08 02:30:00',  30.00,  5.00, 3.00, 1.80, 'PayPal',     false, NULL, NULL),
 (9004, 5010, 'TEST', 'IN', '2026-09-30 10:00:00',  20.00,  5.00, 3.00, 1.20, 'CreditCard', false, NULL, NULL);
INSERT INTO seller_fees VALUES
 (1, 9001, 5001, 'Commission',         12.00, '2026-09-30 20:31:00'),
 (2, 9001, 5002, 'Commission',          6.00, '2026-09-30 20:31:00'),
 (3, 9001, NULL, 'PaymentProcessing',   4.50, '2026-09-30 20:31:00'),
 (4, 9001, 5002, 'Commission',          6.00, '2026-09-30 20:31:00'),   -- duplicate export row (new FeeID)
 (5, 9002, 5003, 'Commission',          9.60, '2026-03-08 01:31:00'),
 (6, 9003, 5004, 'Commission',          4.80, '2026-03-08 03:31:00'),
 (7, 9004, 5010, 'Commission',          2.40, '2026-09-30 10:01:00');
INSERT INTO periodic_statements VALUES (1, 2026, 9, 3, '2026-09-21', '2026-09-30', 150.00, 18.00, 4.50, 0.00, 127.50, '2026-10-05');

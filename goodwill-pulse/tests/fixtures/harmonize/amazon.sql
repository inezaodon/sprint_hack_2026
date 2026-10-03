-- Amazon: purchase_date ISO UTC; transaction report in Pacific text with PDT/PST; negative fees; canceled + TEST orders;
-- a duplicate order_items row and a duplicate financial event; a lowercase SKU not in the item master.
INSERT INTO orders VALUES
 ('113-0000001-0000001', '2026-09-30T23:41:07Z', '2026-10-01T00:00:00Z', 'Shipped',  'MFN', 'Amazon.com', 'abc123@marketplace.amazon.com', 'CA', 24.87, 'USD'),
 ('113-0000002-0000002', '2026-01-06T07:30:00Z', '2026-01-07T00:00:00Z', 'Shipped',  'MFN', 'Amazon.com', 'xyz789@marketplace.amazon.com', 'TX', 12.02, 'USD'),
 ('113-0000008-0000008', '2026-09-29T12:00:00Z', '2026-09-29T13:00:00Z', 'Canceled', 'MFN', 'Amazon.com', 'can@marketplace.amazon.com',    'TX',  7.99, 'USD'),
 ('113-0000009-0000009', '2026-09-29T12:00:00Z', '2026-09-29T13:00:00Z', 'Shipped',  'MFN', 'Amazon.com', 'TEST',                          'TX',  7.99, 'USD');
INSERT INTO order_items VALUES
 ('113-0000001-0000001', 'I1',  'GWM-11-100002', 'B001', 'Book A', 1, 4.00, 3.99, 0.30),
 ('113-0000001-0000001', 'I1x', 'GWM-11-100002', 'B001', 'Book A', 1, 4.00, 3.99, 0.30),   -- duplicate API row
 ('113-0000001-0000001', 'I2',  'GWM-11-100003', 'B002', 'Book B', 1, 4.00, 3.99, 0.30),
 ('113-0000001-0000001', 'I3',  'GWM-11-100006', 'B003', 'Book C', 1, 4.00, 3.99, 0.30),
 ('113-0000002-0000002', 'I4',  'gwm-12-100099', 'B004', 'Book D', 1, 7.50, 3.99, 0.53),
 ('113-0000008-0000008', 'I8',  'GWM-11-100008', 'B008', 'Book E', 1, 4.00, 3.99, 0.00),
 ('113-0000009-0000009', 'I9',  'GWM-11-100009', 'B009', 'Book F', 1, 4.00, 3.99, 0.00);
INSERT INTO financial_events VALUES
 ('E1', 'Sep 30, 2026 4:41:07 PM PDT', 'S1', 'Order',       '113-0000001-0000001', 'GWM-11-100002', 'Book A', 1, 'amazon.com', 'Seller',  4.00,  3.99, 0, -0.61, 0, -1.80, 0,   5.58),
 ('E7', 'Sep 30, 2026 4:41:07 PM PDT', 'S1', 'Order',       '113-0000001-0000001', 'GWM-11-100002', 'Book A', 1, 'amazon.com', 'Seller',  4.00,  3.99, 0, -0.61, 0, -1.80, 0,   5.58),  -- duplicate
 ('E2', 'Sep 30, 2026 4:41:07 PM PDT', 'S1', 'Order',       '113-0000001-0000001', 'GWM-11-100003', 'Book B', 1, 'amazon.com', 'Seller',  4.00,  3.99, 0, -0.60, 0, -1.80, 0,   5.59),
 ('E3', 'Sep 30, 2026 4:41:07 PM PDT', 'S1', 'Order',       '113-0000001-0000001', 'GWM-11-100006', 'Book C', 1, 'amazon.com', 'Seller',  4.00,  3.99, 0, -0.60, 0, -1.80, 0,   5.59),
 ('E4', 'Oct 2, 2026 9:15:00 AM PDT',  'S2', 'Refund',      '113-0000001-0000001', 'GWM-11-100003', 'Book B', 1, 'amazon.com', 'Seller', -4.00, -3.99, 0,  0.48, 0,  0.00, 0,  -7.51),
 ('E5', 'Sep 15, 2026 1:00:00 AM PDT', 'S1', 'Service Fee', NULL,                  NULL,            'Subscription', NULL, 'amazon.com', NULL, 0, 0, 0, 0, 0, 0, -39.99, -39.99),
 ('E6', 'Jan 5, 2026 11:30:00 PM PST', 'S0', 'Order',       '113-0000002-0000002', 'gwm-12-100099', 'Book D', 1, 'amazon.com', 'Seller',  7.50,  3.99, 0, -1.13, 0, -1.80, 0,   8.56);
INSERT INTO settlements VALUES ('S0', '2026-01-01', '2026-01-15', '2026-01-17',   8.56),
                               ('S1', '2026-09-17', '2026-10-01', '2026-10-03', -23.24),
                               ('S2', '2026-10-01', '2026-10-15', '2026-10-17',  -7.51);
INSERT INTO listings VALUES
 ('GWM-11-100002', 'B001', 'Book A', '2026-09-01 10:00:00 PDT', 4.00, 0, 'Sold'),
 ('GWM-11-100010', 'B010', 'Book G', '2026-01-15 09:00:00 PST', 5.00, 1, 'Active');

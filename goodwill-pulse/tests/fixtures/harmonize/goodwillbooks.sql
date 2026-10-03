-- Goodwillbooks: integer cents; 'MM/DD/YYYY HH:MM' Eastern; a line with NULL store_code (SKU fallback); TEST customer.
INSERT INTO sales_orders VALUES
 ('GWB000001', '09/30/2026 23:30', 'C1',   'NY', 1500, 399, 105, 225, 2229, 'PARTIAL_REFUND', 500, '10/01/2026 08:00'),
 -- fall-back day in Eastern: 01:30 twice -> first (EDT) = 05:30 UTC
 ('GWB000003', '11/02/2025 01:30', 'C2',   'NY', 1000,   0,   0, 150, 1000, 'COMPLETE',         0, NULL),
 ('GWB000002', '09/30/2026 12:00', 'TEST', 'NY',  800, 399,   0, 120, 1319, 'COMPLETE',         0, NULL);
INSERT INTO sales_order_lines VALUES
 ('GWB000001', 1, '9780547928227', 'GWM-04-100005', 'Hobbit',  '04', 1, 1000),
 ('GWB000001', 2, '9780062316097', 'GWM-02-100004', 'Sapiens', NULL, 1,  500),
 ('GWB000003', 1, '9780000000001', 'GWM-04-100012', 'Emma',    '04', 1, 1000),
 ('GWB000002', 1, '9780000000000', 'GWM-04-100011', 'Test',    '04', 1,  800);
INSERT INTO monthly_statements VALUES ('2026-09', 1500, 225, 0, 1275, '2026-10-15');
INSERT INTO inventory VALUES ('GWM-04-100005', '9780547928227', 'Hobbit', '04', '09/01/2026', '09/30/2026', 'SOLD', 1000),
 ('GWM-04-100011', '9780000000000', 'Test', '04', '09/01/2026', '09/30/2026', 'SOLD', 800);   -- test item

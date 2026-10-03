-- Hand-written ops rows for tests/test_harmonize.py (executed after sql/sources/ops.sql).
INSERT INTO stores SELECT 'Store' || lpad(CAST(i AS VARCHAR), 2, '0'), 'Store ' || i, 'South Bend', i <= 9, i IN (3, 11)
FROM range(1, 25) t(i);
INSERT INTO upright_items VALUES
 ('UP-03-000001', 'Store03', 'general_merch', 'Jewelry',      'Necklace', '2026-09-20 14:00:00+00', 'ai',     'M1', '2026-09-21 14:00:00+00', '2026-09-23 17:00:00+00', 'E001', 6.5),
 ('UP-03-000002', 'Store03', 'general_merch', 'Clothing',     'Jacket',   '2026-09-20 14:00:00+00', 'person', 'M1', '2026-09-21 14:00:00+00', '2026-09-23 17:00:00+00', 'E001', 4.0),
 ('UP-05-000003', 'Store05', 'general_merch', 'Watches',      'Watch',    '2026-02-20 14:00:00+00', 'person', 'M2', '2026-02-21 14:00:00+00', '2026-03-01 17:00:00+00', 'E001', 5.0),
 ('UP-05-000004', 'Store05', 'general_merch', 'Art',          'Painting', '2026-02-20 14:00:00+00', 'person', 'M2', '2026-02-21 14:00:00+00', '2026-03-01 17:00:00+00', 'E001', 5.0),
 ('UP-03-000009', 'Store03', 'general_merch', 'Collectibles', 'Figurine', '2026-08-01 14:00:00+00', 'ai',     'M3', '2026-08-02 14:00:00+00', NULL,                     'E001', 3.0),
 ('UP-07-000010', 'Store07', 'general_merch', 'Electronics',  'Camera',   '2026-09-01 14:00:00+00', 'ai',     'M4', '2026-09-02 14:00:00+00', '2026-09-03 17:00:00+00', 'E001', 7.0),
 ('UP-08-000030', 'Store08', 'general_merch', 'Toys & Games', 'Test toy', '2026-09-01 14:00:00+00', 'ai',     'M4', '2026-09-02 14:00:00+00', '2026-09-03 17:00:00+00', 'E001', 2.0),
 ('UP-13-000020', 'Store13', 'general_merch', 'Home Decor',   'Vase',     '2026-09-10 14:00:00+00', 'person', 'M5', '2026-09-11 14:00:00+00', '2026-09-20 14:00:00+00', 'E001', 4.0),
 ('UP-13-000022', 'Store13', 'general_merch', 'Clothing',     'Shirt',    '2026-02-10 14:00:00+00', 'person', 'M6', '2026-02-11 14:00:00+00', '2026-02-20 14:00:00+00', 'E001', 4.0),
 ('GWM-09-100001','Store09', 'books',         'Books',        'Dune',     '2026-09-01 14:00:00+00', 'person', 'B1', '2026-09-01 15:00:00+00', '2026-09-02 15:00:00+00', 'E003', 1.0),
 ('GWM-11-100002','Store11', 'books',         'Books',        'Book A',   '2026-08-25 14:00:00+00', 'person', 'B2', '2026-08-26 15:00:00+00', '2026-09-01 17:00:00+00', 'E003', 1.0),
 ('GWM-11-100003','Store11', 'books',         'Books',        'Book B',   '2026-08-25 14:00:00+00', 'person', 'B2', '2026-08-26 15:00:00+00', '2026-09-01 17:00:00+00', 'E003', 1.0),
 ('GWM-11-100006','Store11', 'books',         'Books',        'Book C',   '2026-08-25 14:00:00+00', 'person', 'B2', '2026-08-26 15:00:00+00', '2026-09-01 17:00:00+00', 'E003', 1.0),
 ('GWM-04-100005','Store04', 'books',         'Books',        'Hobbit',   '2026-08-25 14:00:00+00', 'person', 'B3', '2026-08-26 15:00:00+00', NULL,                     'E003', 1.0),
 ('GWM-02-100004','Store02', 'books',         'Books',        'Sapiens',  '2026-08-25 14:00:00+00', 'person', 'B3', '2026-08-26 15:00:00+00', '2026-09-01 15:00:00+00', 'E003', 1.0);
INSERT INTO employees VALUES ('E001', 'lister', 20.00, '2024-01-01'), ('E002', 'shipper', 18.50, '2024-06-01'),
                             ('E003', 'lister', 19.00, '2025-01-01');
INSERT INTO timeclock VALUES ('E001', '2026-09-30', 8.00), ('E002', '2026-09-30', 7.50);
-- E003 posted without a timeclock punch: must still appear in fct_labor (hours NULL)
INSERT INTO operational_productivity VALUES ('E001', '2026-09-30', 50, 5, 45, 40), ('E003', '2026-09-30', 30, 0, 30, 28);
INSERT INTO budget VALUES ('2026-09-01', 'ShopGoodwill ', 100000.00), ('2026-09-01', 'amazon', 20000.00);
INSERT INTO monthly_inputs VALUES ('2026-09-01', 15000.00, 2500000.00);

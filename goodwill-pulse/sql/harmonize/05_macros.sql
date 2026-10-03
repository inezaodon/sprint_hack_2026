-- 05_macros.sql : small reusable normalizers used by every staging file.
-- All are TEMP macros: they exist only for the build connection and never leak into harmonized.duckdb
-- (consumers open that file without the source DBs attached, so nothing persisted may reference them).
-- The runner sets TimeZone='UTC' so no implicit cast depends on the build machine's local zone.

-- Money stored as TEXT (eBay JSON returns "45.00"). Strip anything that is not a digit, dot or minus
-- (currency symbols, thousands commas, stray spaces) and TRY_CAST so one bad value becomes NULL instead of
-- failing the whole build; sql/checks can flag NULL money.
CREATE OR REPLACE TEMP MACRO money_txt(s) AS
    TRY_CAST(NULLIF(regexp_replace(trim(s), '[^0-9.\-]', '', 'g'), '') AS DECIMAL(12,2));

-- Integer cents (Goodwillbooks) -> dollars. Cents are exact integers, so dividing and rounding to 2 places is exact.
CREATE OR REPLACE TEMP MACRO cents(c) AS CAST(c / 100.0 AS DECIMAL(12,2));

-- SKUs are the item id printed at the store ('UP-03-000123' general merch via Upright, 'GWM-03-123456' books via
-- Cash Monkey). Marketplaces sometimes hand them back lower-cased or padded, so compare on upper(trim()).
CREATE OR REPLACE TEMP MACRO norm_sku(s) AS NULLIF(upper(trim(s)), '');

-- The 2 digits after the prefix are the originating store; that store gets the revenue credit.
CREATE OR REPLACE TEMP MACRO store_from_sku(s) AS
    CASE WHEN regexp_matches(norm_sku(s), '^(UP|GWM)-[0-9]{2}-')
         THEN 'Store' || regexp_extract(norm_sku(s), '^(UP|GWM)-([0-9]{2})-', 2) END;

-- Line of business is encoded in the SKU prefix: the only way to split eBay (one account, both tools).
CREATE OR REPLACE TEMP MACRO lob_from_sku(s) AS
    CASE WHEN norm_sku(s) LIKE 'UP-%' THEN 'general_merch'
         WHEN norm_sku(s) LIKE 'GWM-%' THEN 'books' END;

-- Fallback line of business when a SKU is unparseable: every channel except eBay carries one tool only.
CREATE OR REPLACE TEMP MACRO channel_lob(ch) AS
    CASE ch WHEN 'amazon' THEN 'books' WHEN 'goodwillbooks' THEN 'books'
            WHEN 'shopgoodwill' THEN 'general_merch' WHEN 'goodwillfinds' THEN 'general_merch' END;

-- Goodwillbooks store_code '03' / '3' -> 'Store03'. Anything else (NULL, blank, junk) -> NULL so the SKU fallback applies.
CREATE OR REPLACE TEMP MACRO store_from_code(c) AS
    CASE WHEN regexp_matches(trim(c), '^[0-9]{1,2}$') THEN 'Store' || lpad(trim(c), 2, '0') END;

-- GoodwillFinds vendor 'Goodwill Michiana #03'. The name part is free text that people mistype
-- ('Goodwil Michiana #03', 'Goodwill Michana # 3'), so only the trailing store number is trusted.
CREATE OR REPLACE TEMP MACRO store_from_vendor(v) AS
    CASE WHEN regexp_matches(v, '#\s*[0-9]{1,2}\s*$')
         THEN 'Store' || lpad(regexp_extract(v, '#\s*([0-9]{1,2})\s*$', 1), 2, '0') END;

-- Business day = calendar date in America/New_York: it matches the 10 PM store sales report
-- (config/channels.yaml business_timezone) so an order at 11:30 PM ET counts today even though it is tomorrow in UTC.
CREATE OR REPLACE TEMP MACRO et_date(ts) AS CAST(timezone('America/New_York', ts) AS DATE);

-- Naive local wall-clock time -> UTC instant. ShopGoodwill runs on America/Los_Angeles; Goodwillbooks on Eastern.
-- ICU resolves DST: a spring-forward gap time is shifted forward, a fall-back repeated hour takes one offset
-- (the source gives us no way to tell which of the two it was).
CREATE OR REPLACE TEMP MACRO pacific_naive(ts) AS timezone('America/Los_Angeles', ts);
CREATE OR REPLACE TEMP MACRO eastern_naive(ts) AS timezone('America/New_York', ts);

-- Amazon reports print Pacific time with a zone abbreviation ('Sep 30, 2026 4:41:07 PM PDT').
-- Use the abbreviation's fixed offset when present: it is exact even in the repeated fall-back hour.
-- Without an abbreviation, treat the text as America/Los_Angeles wall-clock time.
CREATE OR REPLACE TEMP MACRO amz_report_ts(s) AS
    CASE WHEN trim(s) LIKE '% PDT' THEN strptime(replace(trim(s), ' PDT', ' -0700'), '%b %d, %Y %I:%M:%S %p %z')
         WHEN trim(s) LIKE '% PST' THEN strptime(replace(trim(s), ' PST', ' -0800'), '%b %d, %Y %I:%M:%S %p %z')
         ELSE pacific_naive(try_strptime(trim(s), '%b %d, %Y %I:%M:%S %p')) END;
-- Amazon listings report: 'YYYY-MM-DD HH:MM:SS PDT'
CREATE OR REPLACE TEMP MACRO amz_listing_ts(s) AS
    CASE WHEN trim(s) LIKE '% PDT' THEN strptime(replace(trim(s), ' PDT', ' -0700'), '%Y-%m-%d %H:%M:%S %z')
         WHEN trim(s) LIKE '% PST' THEN strptime(replace(trim(s), ' PST', ' -0800'), '%Y-%m-%d %H:%M:%S %z')
         ELSE pacific_naive(try_strptime(trim(s), '%Y-%m-%d %H:%M:%S')) END;

-- ISO-8601 text with 'Z' or an explicit offset (eBay, Amazon purchase_date, GoodwillFinds) casts directly;
-- the offset is honoured, so no zone guessing is needed.
CREATE OR REPLACE TEMP MACRO iso_ts(s) AS TRY_CAST(NULLIF(trim(s), '') AS TIMESTAMPTZ);

-- Test orders: the platforms' QA accounts use buyer 'TEST' (or a test@ address). They are not revenue.
CREATE OR REPLACE TEMP MACRO is_test_buyer(b) AS
    coalesce(upper(trim(b)) = 'TEST' OR lower(trim(b)) LIKE 'test@%', false);

-- Pseudonymous buyer id: unique per channel (the same username on two channels is not the same person),
-- hashed so no buyer PII (emails, usernames) reaches the dashboard DB.
CREATE OR REPLACE TEMP MACRO buyer_key(ch, b) AS
    CASE WHEN NULLIF(trim(b), '') IS NOT NULL THEN md5(ch || '|' || trim(b)) END;

-- Category normalization map. Marketplace category text is each site's own taxonomy with messy casing
-- ('jewelry', 'JEWELRY', 'Jewelry & Gemstones'); map it onto Goodwill's 10 canonical categories.
-- Order matters: first match wins, so specific words go first ('watch' before 'jewel': 'Jewelry & Watches >
-- Wristwatches' -> Watches; 'video game' before 'electron').
-- Used only as a FALLBACK: the Upright item master (ops.upright_items.category) is Goodwill's canonical
-- category and wins whenever the SKU is found there.
CREATE OR REPLACE TEMP MACRO canon_category(t) AS
    CASE
        WHEN NULLIF(trim(t), '') IS NULL THEN NULL
        WHEN lower(trim(t)) IN ('jewelry','collectibles','electronics','clothing','shoes','home decor',
                                'toys & games','art','watches','books')
             THEN CASE lower(trim(t)) WHEN 'home decor' THEN 'Home Decor' WHEN 'toys & games' THEN 'Toys & Games'
                                      ELSE upper(left(trim(t),1)) || lower(substr(trim(t),2)) END
        WHEN regexp_matches(lower(t), 'watch')                                   THEN 'Watches'
        WHEN regexp_matches(lower(t), 'jewel|gemstone|necklace|bracelet|earring') THEN 'Jewelry'
        WHEN regexp_matches(lower(t), 'book|textbook|isbn')                       THEN 'Books'
        WHEN regexp_matches(lower(t), 'video ?game|toy|game|doll|puzzle|lego')    THEN 'Toys & Games'
        WHEN regexp_matches(lower(t), 'collect|antique|memorabilia|coin|stamp|vintage') THEN 'Collectibles'
        WHEN regexp_matches(lower(t), 'electron|computer|camera|audio|phone|tablet|stereo') THEN 'Electronics'
        WHEN regexp_matches(lower(t), 'shoe|footwear|sneaker|boot|sandal')        THEN 'Shoes'
        WHEN regexp_matches(lower(t), 'cloth|apparel|fashion|dress|shirt|jacket|coat') THEN 'Clothing'
        WHEN regexp_matches(lower(t), 'home|decor|kitchen|houseware|pottery|glassware|furnish') THEN 'Home Decor'
        WHEN regexp_matches(lower(t), '(^|[^a-z])art([^a-z]|$)|painting|print|sculpture') THEN 'Art'
    END;

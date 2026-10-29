| Query | Business purpose | Before (ms) | After (ms) | Speed-up | Technique |
|---|---|---:|---:|---:|---|
| Q1 | Customer "My orders" | 36.876 | 0.023 | 1,603× | Composite B-tree (customer_id, created_at DESC) |
| Q2 | Dispatcher pending queue | 52.982 | 0.036 | 1,472× | Partial index WHERE status = PENDING |
| Q3 | Rider monthly earnings | 60.035 | 0.019 | 3,160× | Covering index + INCLUDE → index-only scan |
| Q4 | Zone revenue, last 7 days | 157.303 | 21.027 | 7× | BRIN index on created_at |
| Q5 | Case-insensitive e-mail lookup | 12.226 | 0.011 | 1,111× | Expression index on lower(email) |
| Q6a | Leaderboard (correlated draft) | > 60 000 (timed out) | 3,645.135 | > 16× | Q3 index turns 3 seq scans per rider into index scans |
| Q6b | Leaderboard (rewritten join) | 577.663 | 556.136 | 1× | Query rewrite; then materialized view (1.231 ms) |

Median of 5 runs, EXPLAIN (ANALYZE, BUFFERS), PostgreSQL 16, parallel workers disabled, warm cache.

"""
Renders terminal-style PNG "screenshots" from the captured output files in each
portfolio's evidence/ or results/ folder, so the LMS documentation can include
images. The text in every image is copied verbatim from the output file named
in its title bar - nothing is retyped or edited.

Usage: python3 tools/render_screenshots.py      (needs Playwright + Chromium)
"""
import html, pathlib, re
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent

SHOTS = [
    # (output png, source file, start regex, end regex (exclusive) or None, max lines, prompt line)
    ("portfolio-1-database-design/screenshots/p1_constraint_tests.png",
     "portfolio-1-database-design/evidence/constraint_tests_output.txt", r"--- Test 1", r"--- Test 7", 40,
     "psql -d ekodash -f sql/03_constraint_tests.sql"),
    ("portfolio-2-query-optimisation/screenshots/p2_q1_before.png",
     "portfolio-2-query-optimisation/results/baseline/Q1_plan.txt", r"^Limit", r"^-- first rows", 30,
     "EXPLAIN (ANALYZE, BUFFERS) ... Q1 customer order history  -- BEFORE"),
    ("portfolio-2-query-optimisation/screenshots/p2_q1_after.png",
     "portfolio-2-query-optimisation/results/optimised/Q1_plan.txt", r"^Limit", r"^-- first rows", 30,
     "EXPLAIN (ANALYZE, BUFFERS) ... Q1 customer order history  -- AFTER ix_order_customer_created"),
    ("portfolio-2-query-optimisation/screenshots/p2_q3_index_only.png",
     "portfolio-2-query-optimisation/results/optimised/Q3_plan.txt", r"^Aggregate", r"^-- first rows", 30,
     "EXPLAIN (ANALYZE, BUFFERS) ... Q3 rider monthly earnings  -- AFTER covering index"),
    ("portfolio-3-transactions-concurrency/screenshots/p3_d3_lost_update.png",
     "portfolio-3-transactions-concurrency/evidence/transaction_demos_output.txt", r"D3  LOST", r"^=+\nD4|D4  RACE", 24,
     "python3 scripts/transaction_demos.py D3"),
    ("portfolio-3-transactions-concurrency/screenshots/p3_d4_race.png",
     "portfolio-3-transactions-concurrency/evidence/transaction_demos_output.txt", r"D4  RACE", r"D5  NON", 24,
     "python3 scripts/transaction_demos.py D4"),
    ("portfolio-3-transactions-concurrency/screenshots/p3_d6_write_skew.png",
     "portfolio-3-transactions-concurrency/evidence/transaction_demos_output.txt", r"D6  WRITE", r"D7  DEAD", 24,
     "python3 scripts/transaction_demos.py D6"),
    ("portfolio-3-transactions-concurrency/screenshots/p3_d7_deadlock.png",
     "portfolio-3-transactions-concurrency/evidence/transaction_demos_output.txt", r"D7  DEAD", None, 24,
     "python3 scripts/transaction_demos.py D7"),
    ("portfolio-4-nosql/screenshots/p4_n2_index.png",
     "portfolio-4-nosql/evidence/02_mongodb_queries_output.txt", r"N2  Customer", r"N3  Dispatch", 12,
     "mongosh --quiet ekodash scripts/02_mongodb_queries.js   (N2)"),
    ("portfolio-4-nosql/screenshots/p4_n4_n6.png",
     "portfolio-4-nosql/evidence/02_mongodb_queries_output.txt", r"N4  Analytics", r"N5  Rider", 14,
     "mongosh --quiet ekodash scripts/02_mongodb_queries.js   (N4)"),
    ("portfolio-4-nosql/screenshots/p4_n6_validation.png",
     "portfolio-4-nosql/evidence/02_mongodb_queries_output.txt", r"N6  \$jsonSchema", r"Storage summary", 10,
     "mongosh --quiet ekodash scripts/02_mongodb_queries.js   (N6)"),
    ("portfolio-4-nosql/screenshots/p4_redis_geo.png",
     "portfolio-4-nosql/evidence/03_redis_output.txt", r"K1  Live", r"K3  OTP", 22,
     "python3 scripts/03_redis_keyvalue.py"),
    ("portfolio-5-distributed-cloud/screenshots/p5_pg_replication.png",
     "portfolio-5-distributed-cloud/evidence/02_pg_replication_tests_output.txt", r"R1  Repl", r"R4  Read", 22,
     "python3 scripts/02_pg_replication_tests.py   (R1-R3)"),
    ("portfolio-5-distributed-cloud/screenshots/p5_pg_failover.png",
     "portfolio-5-distributed-cloud/evidence/02_pg_replication_tests_output.txt", r"R5  Repl", None, 30,
     "python3 scripts/02_pg_replication_tests.py   (R5-R7)"),
    ("portfolio-5-distributed-cloud/screenshots/p5_mongo_failover.png",
     "portfolio-5-distributed-cloud/evidence/04_mongo_replica_set_tests_output.txt", r"M4  Multi", None, 30,
     "python3 scripts/04_mongo_distributed_tests.py rs   (M4-M5)"),
    ("portfolio-5-distributed-cloud/screenshots/p5_sharding.png",
     "portfolio-5-distributed-cloud/evidence/04c_sharding_tests_output.txt", r"S1  Shard", None, 30,
     "python3 scripts/04_mongo_distributed_tests.py shard   (S1-S3)"),
]

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#e9ecef;padding:18px;font-family:'DejaVu Sans Mono',monospace}}
.win{{background:#1e2127;border-radius:9px;box-shadow:0 6px 24px rgba(0,0,0,.25);overflow:hidden;display:inline-block;min-width:900px}}
.bar{{background:#2c313a;color:#aab2bf;font:12px 'DejaVu Sans',sans-serif;padding:8px 12px;display:flex;gap:7px;align-items:center}}
.d{{width:11px;height:11px;border-radius:50%}}
pre{{margin:0;padding:12px 16px 16px;color:#dcdfe4;font-size:13px;line-height:1.38;white-space:pre}}
.p{{color:#98c379}} .e{{color:#e06c75}} .r{{color:#e5c07b}}
</style></head><body><div class="win"><div class="bar"><span class="d" style="background:#ff5f56"></span>
<span class="d" style="background:#ffbd2e"></span><span class="d" style="background:#27c93f"></span>
&nbsp; {title}</div><pre><span class="p">$</span> {prompt}
{body}</pre></div></body></html>"""


def excerpt(text, start, end, max_lines):
    m = re.search(start, text, re.M)
    t = text[m.start():] if m else text
    if end:
        e = re.search(end, t[1:], re.M)
        if e:
            t = t[: e.start() + 1]
    lines = t.rstrip("\n= ").splitlines()
    while lines and set(lines[-1].strip()) <= {"="}:
        lines.pop()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines in the output file)"]
    return lines


def colour(line):
    s = html.escape(line)
    if re.search(r"ERROR|REJECTED|BROKEN|LOST UPDATE|TIMEOUT", line):
        return f'<span class="e">{s}</span>'
    if line.strip().startswith("RESULT") or "Execution Time" in line:
        return f'<span class="r">{s}</span>'
    return s


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(device_scale_factor=2)
        for out, src, start, end, maxl, prompt in SHOTS:
            text = (ROOT / src).read_text()
            body = "\n".join(colour(l) for l in excerpt(text, start, end, maxl))
            pg.set_content(PAGE.format(title=html.escape(f"output file: {src}"), prompt=html.escape(prompt), body=body))
            outp = ROOT / out
            outp.parent.mkdir(parents=True, exist_ok=True)
            pg.locator(".win").screenshot(path=str(outp))
            print("wrote", out)
        b.close()


if __name__ == "__main__":
    main()

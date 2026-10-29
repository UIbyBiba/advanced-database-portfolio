"""
MIT 8103 - Portfolio 2 benchmark harness (EkoDash Logistics)

Runs every query in ../sql/01_business_queries.sql with
EXPLAIN (ANALYZE, BUFFERS), records the plan and the median execution
time over 5 runs, then repeats after ../sql/02_optimisation_indexes.sql
has been applied.

Usage:
    python3 benchmark.py baseline      # before indexes
    python3 benchmark.py optimised     # after indexes
Outputs go to ../results/<phase>/
"""
import os, re, sys, json, statistics, pathlib
import psycopg

DSN = os.environ.get("EKODASH_DSN", "host=localhost dbname=ekodash user=postgres")
HERE = pathlib.Path(__file__).resolve().parent
SQL_FILE = HERE.parent / "sql" / "01_business_queries.sql"
RUNS = 5
TIMEOUT_MS = 60000   # a query slower than 60 s is cancelled and reported as a timeout


def load_queries():
    """Split the SQL file into labelled queries (Q1..Q5, 6a, 6b)."""
    text = SQL_FILE.read_text()
    queries, label, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"--\s*(Q\d|6[ab])\b", line)
        if m:
            if label and "".join(buf).strip():
                queries[label] = "\n".join(buf).strip().rstrip(";")
            label, buf = m.group(1).replace("6", "Q6", 1) if m.group(1).startswith("6") else m.group(1), []
            continue
        if label and not line.strip().startswith("--"):
            buf.append(line)
    if label and "".join(buf).strip():
        queries[label] = "\n".join(buf).strip().rstrip(";")
    return {k: v for k, v in queries.items() if v.upper().startswith("SELECT")}


def main(phase):
    out = HERE.parent / "results" / phase
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET search_path TO ekodash")
        conn.execute("SET max_parallel_workers_per_gather = 0")  # stable, single-core timings
        for label, sql in load_queries().items():
            times, plan_text = [], ""
            conn.execute(f"SET statement_timeout = {TIMEOUT_MS}")
            try:
                conn.execute("EXPLAIN (ANALYZE) " + sql)      # warm-up run (fills cache)
            except psycopg.errors.QueryCanceled:
                plan = "\n".join(r[0] for r in conn.execute("EXPLAIN " + sql).fetchall())
                summary[label] = {"median_ms": None, "timeout_ms": TIMEOUT_MS, "rows_returned": None}
                (out / f"{label}_plan.txt").write_text(
                    f"-- {label} [{phase}]  CANCELLED: did not finish within {TIMEOUT_MS/1000:.0f} s\n{sql};\n\n"
                    f"-- estimated plan (EXPLAIN without ANALYZE):\n{plan}\n")
                print(f"{phase:10s} {label:4s} TIMEOUT (> {TIMEOUT_MS/1000:.0f} s)")
                continue
            for _ in range(RUNS):
                rows = conn.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql).fetchall()
                plan_text = "\n".join(r[0] for r in rows)
                times.append(float(re.search(r"Execution Time: ([\d.]+) ms", plan_text).group(1)))
            result_rows = conn.execute(sql).fetchall()
            med = statistics.median(times)
            summary[label] = {"median_ms": med, "runs_ms": times, "rows_returned": len(result_rows)}
            (out / f"{label}_plan.txt").write_text(
                f"-- {label} [{phase}]  median of {RUNS} runs = {med:.3f} ms\n{sql};\n\n{plan_text}\n\n"
                f"-- first rows of result:\n" + "\n".join(str(r) for r in result_rows[:10]) + "\n")
            print(f"{phase:10s} {label:4s} median {med:10.3f} ms   rows={len(result_rows)}")
    (out / "summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "baseline")

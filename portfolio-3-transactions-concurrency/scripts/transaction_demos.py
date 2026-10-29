"""
MIT 8103 - Portfolio 3: Transactions and Concurrency (EkoDash Logistics)

Seven demonstrations, each run against the real PostgreSQL 16 database.
Concurrent demos open TWO separate connections (two sessions) and use
threading.Event objects to force an exact interleaving of statements,
so the anomaly is reproduced every time rather than by luck.

    D1  COMMIT    - place an order and pay from wallet as one atomic unit
    D2  ROLLBACK  - failed payment undoes everything; SAVEPOINT partial undo
    D3  Lost update on a rider wallet  -> fixed with SELECT ... FOR UPDATE
    D4  Two riders accept the same order -> fixed with conditional UPDATE,
        plus a dispatch queue using FOR UPDATE SKIP LOCKED
    D5  Non-repeatable read: READ COMMITTED vs REPEATABLE READ
    D6  Write skew ("zone must keep one rider on duty")
        REPEATABLE READ allows it, SERIALIZABLE prevents it (+ retry)
    D7  Deadlock between two wallet transfers -> fixed with ordered locking

Usage:  python3 transaction_demos.py            (all demos)
        python3 transaction_demos.py D3 D6      (selected demos)
"""
import os, sys, time, threading, datetime
import psycopg
from psycopg import errors

DSN = os.environ.get("EKODASH_DSN", "host=localhost dbname=ekodash user=postgres")
T0 = time.perf_counter()
_print_lock = threading.Lock()


def log(who, msg):
    with _print_lock:
        print(f"  [{time.perf_counter() - T0:7.3f}s] {who:<3} {msg}", flush=True)


def connect(isolation=None):
    conn = psycopg.connect(DSN)            # autocommit off: explicit transactions
    conn.execute("SET search_path TO ekodash")
    conn.commit()
    if isolation:
        conn.isolation_level = isolation
    return conn


def q1(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()


def header(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78, flush=True)


# ---------------------------------------------------------------- fixtures
def setup_fixtures():
    """Small, isolated test data so demos never depend on random seed data."""
    with connect() as c:
        c.execute("DELETE FROM wallet_transaction WHERE wallet_id IN (SELECT wallet_id FROM wallet WHERE customer_id IN (1,2) OR rider_id IN (1,2))")
        c.execute("UPDATE wallet SET balance = 5000 WHERE customer_id = 1")
        c.execute("UPDATE wallet SET balance = 300  WHERE customer_id = 2")
        c.execute("UPDATE wallet SET balance = 10000 WHERE rider_id IN (1, 2)")
        # a demo zone with exactly two riders on duty (for the write-skew demo)
        c.execute("INSERT INTO zone VALUES (99, 1, 'P3 Demo Zone', 1500) ON CONFLICT DO NOTHING")
        c.execute("UPDATE rider SET home_zone_id = 99, duty_status = 'ON_DUTY' WHERE rider_id IN (1499, 1500)")
        c.commit()


def teardown_fixtures():
    with connect() as c:
        c.execute("UPDATE rider SET home_zone_id = 1 + (rider_id % 20), duty_status = 'ON_DUTY' WHERE rider_id IN (1499, 1500)")
        c.execute("DELETE FROM zone WHERE zone_id = 99")
        c.commit()


def new_pending_order(conn, customer_id=1):
    return q1(conn, """INSERT INTO delivery_order (customer_id, pickup_address_id, dropoff_street,
                         dropoff_zone_id, recipient_phone, package_size, weight_kg, fee_amount)
                       SELECT customer_id, address_id, '5 Demo Street', zone_id, '+2348099999999', 'S', 1.2, 1800
                       FROM customer_address WHERE customer_id = %s LIMIT 1
                       RETURNING order_id""", (customer_id,))[0]


# ---------------------------------------------------------------- D1
def place_and_pay(conn, customer_id, fee):
    """One business transaction = 5 statements that must all succeed or all fail."""
    order_id = q1(conn, """INSERT INTO delivery_order (customer_id, pickup_address_id, dropoff_street,
                     dropoff_zone_id, recipient_phone, package_size, weight_kg, fee_amount)
                   SELECT customer_id, address_id, '22 Broad St', zone_id, '+2348012345678', 'M', 3.0, %s
                   FROM customer_address WHERE customer_id = %s LIMIT 1 RETURNING order_id""", (fee, customer_id))[0]
    log("TX", f"INSERT delivery_order            -> order_id {order_id}")
    wallet_id = q1(conn, "UPDATE wallet SET balance = balance - %s, updated_at = now() "
                         "WHERE customer_id = %s RETURNING wallet_id", (fee, customer_id))[0]
    log("TX", f"UPDATE wallet balance - {fee}     -> wallet {wallet_id}")
    conn.execute("INSERT INTO wallet_transaction (wallet_id, order_id, entry_type, amount) VALUES (%s,%s,'DEBIT',%s)",
                 (wallet_id, order_id, fee))
    log("TX", "INSERT wallet_transaction (DEBIT)")
    conn.execute("INSERT INTO payment (order_id, method, amount, status, reference, paid_at) "
                 "VALUES (%s,'WALLET',%s,'SUCCESS',%s, now())", (order_id, fee, f"P3-{order_id}"))
    log("TX", "INSERT payment (WALLET, SUCCESS)")
    conn.execute("INSERT INTO order_status_history (order_id, status, changed_by) VALUES (%s,'PENDING','CUSTOMER')",
                 (order_id,))
    log("TX", "INSERT order_status_history")
    return order_id


def d1_commit():
    header("D1  COMMIT - order + wallet payment as ONE atomic transaction")
    with connect() as c:
        before = q1(c, "SELECT balance FROM wallet WHERE customer_id = 1")[0]
        log("", f"customer 1 wallet before = {before}")
        oid = place_and_pay(c, 1, 2000)
        c.commit()
        log("TX", "COMMIT")
        after = q1(c, "SELECT balance FROM wallet WHERE customer_id = 1")[0]
        rows = q1(c, """SELECT (SELECT count(*) FROM delivery_order WHERE order_id=%s),
                               (SELECT count(*) FROM payment WHERE order_id=%s),
                               (SELECT count(*) FROM wallet_transaction WHERE order_id=%s)""", (oid, oid, oid))
        c.commit()
        log("", f"customer 1 wallet after  = {after}   (order/payment/ledger rows = {rows})")
        print(f"  RESULT: all 5 changes are durable together; balance {before} -> {after}")


# ---------------------------------------------------------------- D2
def d2_rollback():
    header("D2  ROLLBACK - insufficient balance undoes the whole transaction; SAVEPOINT")
    with connect() as c:
        before_bal = q1(c, "SELECT balance FROM wallet WHERE customer_id = 2")[0]
        before_orders = q1(c, "SELECT count(*) FROM delivery_order WHERE customer_id = 2")[0]
        c.commit()
        log("", f"customer 2 wallet = {before_bal}, orders = {before_orders}")
        try:
            place_and_pay(c, 2, 2000)          # wallet has only 300
            c.commit()
        except errors.CheckViolation as e:
            log("TX", f"ERROR {e.sqlstate} check_violation: {e.diag.constraint_name}")
            c.rollback()
            log("TX", "ROLLBACK")
        after_bal = q1(c, "SELECT balance FROM wallet WHERE customer_id = 2")[0]
        after_orders = q1(c, "SELECT count(*) FROM delivery_order WHERE customer_id = 2")[0]
        c.commit()
        log("", f"customer 2 wallet = {after_bal}, orders = {after_orders}")
        print("  RESULT: the order INSERT that had already succeeded was undone as well (atomicity).")

        print("\n  -- SAVEPOINT: an optional step fails but the main work is kept --")
        oid = place_and_pay(c, 1, 1500)
        c.execute("SAVEPOINT before_rating")
        log("TX", "SAVEPOINT before_rating")
        try:
            c.execute("INSERT INTO rider_rating (order_id, score) VALUES (%s, 9)", (oid,))
        except errors.CheckViolation as e:
            log("TX", f"ERROR {e.sqlstate} on rating (score 9) -> ROLLBACK TO SAVEPOINT")
            c.execute("ROLLBACK TO SAVEPOINT before_rating")
        c.commit()
        log("TX", "COMMIT")
        kept = q1(c, "SELECT count(*) FROM payment WHERE order_id = %s", (oid,))[0]
        c.commit()
        print(f"  RESULT: order {oid} and its payment kept (payment rows = {kept}); only the bad rating was discarded.")


# ---------------------------------------------------------------- D3
def credit_rider(who, delta, use_lock, read_done, other_read_done):
    with connect() as c:
        lock = " FOR UPDATE" if use_lock else ""
        bal = q1(c, f"SELECT balance FROM wallet WHERE rider_id = 1{lock}")[0]
        log(who, f"SELECT balance{lock:<11} -> {bal}")
        read_done.set()
        if not use_lock:
            other_read_done.wait()        # make sure BOTH have read the old value
        new = bal + delta
        c.execute("UPDATE wallet SET balance = %s WHERE rider_id = 1", (new,))
        log(who, f"UPDATE balance = {bal} + {delta} = {new}")
        time.sleep(0.2)
        c.commit()
        log(who, "COMMIT")


def run_d3(use_lock):
    with connect() as c:
        c.execute("UPDATE wallet SET balance = 10000 WHERE rider_id = 1"); c.commit()
    e1, e2 = threading.Event(), threading.Event()
    t1 = threading.Thread(target=credit_rider, args=("T1", 1000, use_lock, e1, e2))
    t2 = threading.Thread(target=credit_rider, args=("T2", 500, use_lock, e2, e1))
    t1.start(); e1.wait(); t2.start(); t1.join(); t2.join()
    with connect() as c:
        final = q1(c, "SELECT balance FROM wallet WHERE rider_id = 1")[0]
    return final


def d3_lost_update():
    header("D3  LOST UPDATE - two payouts credited to rider 1 at the same time (start 10000)")
    print("  -- (a) read-modify-write in application code, READ COMMITTED, no lock --")
    final = run_d3(use_lock=False)
    print(f"  RESULT: final balance = {final}; expected 11500 -> "
          f"{'LOST UPDATE: a credit of ' + str(11500 - final) + ' vanished' if final != 11500 else 'ok'}")
    print("\n  -- (b) same code with SELECT ... FOR UPDATE (pessimistic row lock) --")
    final = run_d3(use_lock=True)
    print(f"  RESULT: final balance = {final}; T2 waited for T1's lock, then read the new value -> {'correct' if final == 11500 else 'WRONG'}")


# ---------------------------------------------------------------- D4
def accept_naive(who, order_id, rider_id, read_done, go):
    with connect() as c:
        st = q1(c, "SELECT status FROM delivery_order WHERE order_id = %s", (order_id,))[0]
        log(who, f"rider {rider_id} sees order {order_id} status = {st}")
        read_done.set(); go.wait()
        if st == "PENDING":
            c.execute("UPDATE delivery_order SET status='ASSIGNED', assigned_rider_id=%s, assigned_at=now() "
                      "WHERE order_id = %s", (rider_id, order_id))
            c.commit()
            log(who, f"UPDATE ... assigned_rider_id = {rider_id}; COMMIT  -> app tells rider {rider_id} 'job is yours'")


def accept_safe(who, order_id, rider_id, read_done, go):
    with connect() as c:
        read_done.set(); go.wait()
        row = c.execute("UPDATE delivery_order SET status='ASSIGNED', assigned_rider_id=%s, assigned_at=now() "
                        "WHERE order_id = %s AND status = 'PENDING' RETURNING order_id", (rider_id, order_id)).fetchone()
        c.commit()
        log(who, f"conditional UPDATE ... WHERE status='PENDING' -> {'1 row: ACCEPTED' if row else '0 rows: already taken'}")


def run_d4(fn):
    with connect() as c:
        oid = new_pending_order(c); c.commit()
    r1, r2, go = threading.Event(), threading.Event(), threading.Event()
    ts = [threading.Thread(target=fn, args=("T1", oid, 101, r1, go)),
          threading.Thread(target=fn, args=("T2", oid, 121, r2, go))]
    for t in ts: t.start()
    r1.wait(); r2.wait(); go.set()
    for t in ts: t.join()
    with connect() as c:
        return oid, q1(c, "SELECT assigned_rider_id FROM delivery_order WHERE order_id = %s", (oid,))[0]


def dispatcher(who, results):
    with connect() as c:
        row = c.execute("""SELECT order_id FROM delivery_order
                           WHERE status = 'PENDING' AND customer_id = 1
                           ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
        log(who, f"SELECT ... FOR UPDATE SKIP LOCKED -> order {row[0] if row else None}")
        results[who] = row[0] if row else None
        time.sleep(0.5)                       # hold the lock while 'assigning'
        c.commit()


def d4_double_assignment():
    header("D4  RACE CONDITION - two riders tap 'Accept' on the same order")
    print("  -- (a) check-then-act: SELECT status, then UPDATE --")
    oid, final = run_d4(accept_naive)
    print(f"  RESULT: BOTH riders were told the job is theirs; database keeps rider {final} (the other was silently overwritten)")
    print("\n  -- (b) atomic conditional UPDATE (compare-and-set) --")
    oid, final = run_d4(accept_safe)
    print(f"  RESULT: exactly one rider wins (rider {final}); the other is told immediately that the job is taken")

    print("\n  -- (c) two dispatcher workers pulling from the PENDING queue with SKIP LOCKED --")
    with connect() as c:
        c.execute("UPDATE delivery_order SET status='CANCELLED' WHERE customer_id = 1 AND status = 'PENDING'")
        new_pending_order(c); new_pending_order(c); c.commit()
    res = {}
    t1 = threading.Thread(target=dispatcher, args=("W1", res)); t1.start(); time.sleep(0.1)
    t2 = threading.Thread(target=dispatcher, args=("W2", res)); t2.start()
    t1.join(); t2.join()
    print(f"  RESULT: W1 got order {res['W1']}, W2 got order {res['W2']} - no waiting, no duplicate work")


# ---------------------------------------------------------------- D5
def d5_non_repeatable(level):
    with connect() as c:
        c.execute("UPDATE zone SET base_fee = 1500 WHERE zone_id = 1"); c.commit()
    reader = connect(level)
    writer = connect()
    a = q1(reader, "SELECT base_fee FROM zone WHERE zone_id = 1")[0]
    log("T1", f"[{level.name}] read Yaba base_fee  -> {a}")
    writer.execute("UPDATE zone SET base_fee = 1700 WHERE zone_id = 1"); writer.commit()
    log("T2", "UPDATE Yaba base_fee = 1700; COMMIT")
    b = q1(reader, "SELECT base_fee FROM zone WHERE zone_id = 1")[0]
    log("T1", f"[{level.name}] read again (same txn)  -> {b}")
    reader.commit(); reader.close(); writer.close()
    with connect() as c:
        c.execute("UPDATE zone SET base_fee = 1500 WHERE zone_id = 1"); c.commit()
    return a, b


def d5():
    header("D5  NON-REPEATABLE READ - a quote is computed while finance changes the price")
    a, b = d5_non_repeatable(psycopg.IsolationLevel.READ_COMMITTED)
    print(f"  RESULT: READ COMMITTED saw {a} then {b} inside ONE transaction (non-repeatable read)\n")
    a, b = d5_non_repeatable(psycopg.IsolationLevel.REPEATABLE_READ)
    print(f"  RESULT: REPEATABLE READ saw {a} then {b} - one consistent snapshot (MVCC)")


# ---------------------------------------------------------------- D6
def go_off_duty(who, rider_id, level, checked, other_checked, outcome):
    c = connect(level)
    try:
        on = q1(c, "SELECT count(*) FROM rider WHERE home_zone_id = 99 AND duty_status = 'ON_DUTY'")[0]
        log(who, f"[{level.name}] riders on duty in zone 99 = {on}")
        checked.set(); other_checked.wait()
        if on >= 2:
            c.execute("UPDATE rider SET duty_status = 'OFF_DUTY' WHERE rider_id = %s", (rider_id,))
            log(who, f"rule ok (>=2 on duty) -> rider {rider_id} goes OFF_DUTY")
        if who == "T2":
            time.sleep(0.2)
        c.commit()
        log(who, "COMMIT ok")
        outcome[who] = "committed"
    except errors.SerializationFailure as e:
        log(who, f"ERROR {e.sqlstate} serialization_failure -> ROLLBACK (app should retry)")
        c.rollback()
        outcome[who] = "aborted"
    finally:
        c.close()


def run_d6(level):
    with connect() as c:
        c.execute("UPDATE rider SET duty_status = 'ON_DUTY' WHERE rider_id IN (1499,1500)"); c.commit()
    e1, e2, out = threading.Event(), threading.Event(), {}
    t1 = threading.Thread(target=go_off_duty, args=("T1", 1499, level, e1, e2, out))
    t2 = threading.Thread(target=go_off_duty, args=("T2", 1500, level, e2, e1, out))
    t1.start(); t2.start(); t1.join(); t2.join()
    with connect() as c:
        left = q1(c, "SELECT count(*) FROM rider WHERE home_zone_id = 99 AND duty_status='ON_DUTY'")[0]
    return out, left


def d6_write_skew():
    header("D6  WRITE SKEW - rule: a zone must always keep at least 1 rider ON_DUTY (zone 99 has 2)")
    out, left = run_d6(psycopg.IsolationLevel.REPEATABLE_READ)
    print(f"  RESULT: REPEATABLE READ -> {out}; riders left on duty = {left}  (business rule BROKEN)\n")
    out, left = run_d6(psycopg.IsolationLevel.SERIALIZABLE)
    print(f"  RESULT: SERIALIZABLE    -> {out}; riders left on duty = {left}  (rule kept)")
    # retry of the aborted transaction: it now sees only 1 rider on duty and correctly refuses
    with connect(psycopg.IsolationLevel.SERIALIZABLE) as c:
        on = q1(c, "SELECT count(*) FROM rider WHERE home_zone_id = 99 AND duty_status = 'ON_DUTY'")[0]
        c.commit()
    log("T2", f"retry: riders on duty = {on} -> request refused by the rule, nothing written")


# ---------------------------------------------------------------- D7
def transfer(who, src, dst, amount, ordered, step1, step2):
    c = connect()
    try:
        first, second = (sorted([src, dst]) if ordered else (src, dst))
        c.execute("SELECT balance FROM wallet WHERE rider_id = %s FOR UPDATE", (first,))
        log(who, f"locked wallet of rider {first}")
        step1.set(); step2.wait(timeout=2 if not ordered else 0.3)
        c.execute("SELECT balance FROM wallet WHERE rider_id = %s FOR UPDATE", (second,))
        log(who, f"locked wallet of rider {second}")
        c.execute("UPDATE wallet SET balance = balance - %s WHERE rider_id = %s", (amount, src))
        c.execute("UPDATE wallet SET balance = balance + %s WHERE rider_id = %s", (amount, dst))
        c.commit()
        log(who, f"moved {amount} from rider {src} to rider {dst}; COMMIT")
    except errors.DeadlockDetected as e:
        log(who, f"ERROR {e.sqlstate} deadlock_detected -> PostgreSQL chose this txn as victim, ROLLBACK")
        c.rollback()
    finally:
        c.close()


def run_d7(ordered):
    e1, e2 = threading.Event(), threading.Event()
    t1 = threading.Thread(target=transfer, args=("T1", 1, 2, 700, ordered, e1, e2))
    t2 = threading.Thread(target=transfer, args=("T2", 2, 1, 300, ordered, e2, e1))
    t1.start(); t2.start(); t1.join(); t2.join()


def d7_deadlock():
    header("D7  DEADLOCK - riders 1 and 2 send each other money at the same moment")
    print("  -- (a) each transaction locks its own wallet first --")
    run_d7(ordered=False)
    print("  RESULT: circular wait; PostgreSQL's deadlock detector (deadlock_timeout = 1s) aborted one transaction\n")
    print("  -- (b) both transactions lock wallets in ascending rider_id order --")
    run_d7(ordered=True)
    print("  RESULT: second transaction simply waits for the first; no deadlock possible")


DEMOS = {"D1": d1_commit, "D2": d2_rollback, "D3": d3_lost_update, "D4": d4_double_assignment,
         "D5": d5, "D6": d6_write_skew, "D7": d7_deadlock}

if __name__ == "__main__":
    chosen = sys.argv[1:] or list(DEMOS)
    print(f"EkoDash Portfolio 3 - run at {datetime.datetime.now():%Y-%m-%d %H:%M:%S} on "
          f"{psycopg.connect(DSN).execute('select version()').fetchone()[0].split(' on ')[0]}")
    setup_fixtures()
    try:
        for d in chosen:
            DEMOS[d]()
    finally:
        teardown_fixtures()

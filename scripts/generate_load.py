#!/usr/bin/env python3
"""
generate_load.py — Continuous DML load generator for GoldenGate stress testing.

Inserts, updates, and deletes rows across all 10 source tables to produce
redo traffic for the Extract processes to capture.

Usage:
    pip install oracledb
    python scripts/generate_load.py [--dsn oracle-db:1521/FREEPDB1] [--rate 50] [--duration 300]

Defaults:  dsn=localhost:1521/FREEPDB1  rate=50 ops/sec  duration=300s (5 min)
"""

import argparse
import random
import string
import sys
import time
from contextlib import contextmanager

try:
    import oracledb
except ImportError:
    print("Install oracledb:  pip install oracledb")
    sys.exit(1)


# ── Table-specific DML generators ────────────────────────────

def _rand_str(n=8):
    return "".join(random.choices(string.ascii_lowercase, k=n))


def _rand_email():
    return f"{_rand_str(6)}@load.test"


GENERATORS = {
    "customers": lambda: (
        "INSERT INTO gg_src.customers (name,email,region,tier) "
        "VALUES (:1,:2,:3,:4)",
        [f"Load-{_rand_str()}", _rand_email(),
         random.choice(["US-EAST", "US-WEST", "EU-WEST", "APAC"]),
         random.choice(["PREMIUM", "STANDARD", "BASIC"])],
    ),
    "orders": lambda: (
        "INSERT INTO gg_src.orders (customer_id,amount,currency,status) "
        "VALUES (:1,:2,:3,:4)",
        [random.randint(1, 500), round(random.uniform(5, 2000), 2),
         random.choice(["USD", "EUR", "GBP"]),
         random.choice(["PENDING", "SHIPPED", "COMPLETED"])],
    ),
    "order_items": lambda: (
        "INSERT INTO gg_src.order_items (order_id,product_id,quantity,unit_price,discount_pct) "
        "VALUES (:1,:2,:3,:4,:5)",
        [random.randint(1, 1000), random.randint(1, 200),
         random.randint(1, 10), round(random.uniform(5, 500), 2),
         round(random.uniform(0, 25), 2)],
    ),
    "products_update": lambda: (
        "UPDATE gg_src.products SET stock_qty=stock_qty+:1, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=:2",
        [random.randint(-20, 50), random.randint(1, 200)],
    ),
    "inventory_events": lambda: (
        "INSERT INTO gg_src.inventory_events (product_id,event_type,quantity_delta,warehouse) "
        "VALUES (:1,:2,:3,:4)",
        [random.randint(1, 200),
         random.choice(["RECEIVED", "SHIPPED", "RETURNED", "ADJUSTED"]),
         random.randint(-50, 100),
         random.choice(["WH-EAST", "WH-WEST", "WH-EU", "WH-APAC"])],
    ),
    "payments": lambda: (
        "INSERT INTO gg_src.payments (order_id,method,amount,status) "
        "VALUES (:1,:2,:3,:4)",
        [random.randint(1, 1000),
         random.choice(["CREDIT_CARD", "DEBIT", "WIRE", "PAYPAL"]),
         round(random.uniform(5, 2000), 2),
         random.choice(["PROCESSING", "COMPLETED", "FAILED"])],
    ),
    "audit_log": lambda: (
        "INSERT INTO gg_src.audit_log (entity_type,entity_id,action,actor,details) "
        "VALUES (:1,:2,:3,:4,:5)",
        [random.choice(["order", "customer", "product", "payment"]),
         random.randint(1, 1000),
         random.choice(["CREATE", "UPDATE", "DELETE", "VIEW"]),
         f"user-{random.randint(1, 50)}",
         f"load-test-{_rand_str(16)}"],
    ),
    "user_sessions": lambda: (
        "INSERT INTO gg_src.user_sessions "
        "(customer_id,session_token,ip_address,user_agent) "
        "VALUES (:1,:2,:3,:4)",
        [random.randint(1, 500), _rand_str(32),
         f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}",
         f"LoadBot/{random.randint(1,5)}.0"],
    ),
    "notifications": lambda: (
        "INSERT INTO gg_src.notifications (customer_id,channel,subject,body,status) "
        "VALUES (:1,:2,:3,:4,:5)",
        [random.randint(1, 500),
         random.choice(["EMAIL", "SMS", "PUSH"]),
         f"Notification {_rand_str(8)}",
         f"Body text {_rand_str(32)}",
         random.choice(["QUEUED", "SENT", "FAILED"])],
    ),
    "metrics_raw": lambda: (
        "INSERT INTO gg_src.metrics_raw (metric_name,metric_value,tags) "
        "VALUES (:1,:2,:3)",
        [random.choice(["cpu.usage", "mem.free", "disk.iops",
                         "net.rx_bytes", "app.latency_ms"]),
         round(random.uniform(0, 100), 4),
         f"host=srv{random.randint(1,20)},dc={random.choice(['east','west'])}"],
    ),
}


@contextmanager
def oracle_connection(dsn, user="gg_src", password="GGMCP_Admin123"):
    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    try:
        yield conn
    finally:
        conn.close()


def run_load(dsn, target_rate, duration_sec):
    gen_keys = list(GENERATORS.keys())
    interval = 1.0 / target_rate if target_rate > 0 else 0

    print(f"Connecting to {dsn} as gg_src ...")
    with oracle_connection(dsn) as conn:
        cur = conn.cursor()
        start = time.monotonic()
        ops = 0
        errors = 0
        commit_batch = 0

        print(f"Generating ~{target_rate} ops/sec for {duration_sec}s "
              f"across {len(gen_keys)} table generators...")

        while time.monotonic() - start < duration_sec:
            t0 = time.monotonic()
            gen_name = random.choice(gen_keys)
            sql, params = GENERATORS[gen_name]()
            try:
                cur.execute(sql, params)
                commit_batch += 1
                if commit_batch >= 20:
                    conn.commit()
                    commit_batch = 0
                ops += 1
            except oracledb.Error as e:
                errors += 1
                if errors <= 5:
                    print(f"  [{gen_name}] {e}")

            # Throttle to target rate
            elapsed = time.monotonic() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)

            # Progress every 10s
            total_elapsed = time.monotonic() - start
            if ops % (target_rate * 10) == 0 and ops > 0:
                print(f"  [{total_elapsed:6.0f}s] {ops} ops, "
                      f"{errors} errors, "
                      f"{ops/total_elapsed:.1f} ops/sec actual")

        # Final commit
        conn.commit()
        total = time.monotonic() - start
        print(f"\nDone: {ops} ops in {total:.1f}s "
              f"({ops/total:.1f} ops/sec), {errors} errors")


def main():
    parser = argparse.ArgumentParser(description="GoldenGate load generator")
    parser.add_argument("--dsn", default="localhost:1521/FREEPDB1")
    parser.add_argument("--rate", type=int, default=50,
                        help="Target operations per second")
    parser.add_argument("--duration", type=int, default=300,
                        help="Duration in seconds")
    args = parser.parse_args()
    run_load(args.dsn, args.rate, args.duration)


if __name__ == "__main__":
    main()

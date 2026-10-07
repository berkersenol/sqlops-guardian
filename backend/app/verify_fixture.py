"""
SQLOps Guardian - the verifier's fixture database.

A small SQLite database the verification agent runs candidate rewrites
against. It is disposable: build() drops and recreates it from the constants
below, so it can always be regenerated and nothing of value lives in it. That
disposability is the verifier's real safety boundary -- the SELECT-only guard
in verifier.py is defence in depth, not the only defence.

The data is designed backwards from the rewrites we want to catch. A
semantic-equivalence check by execution is only as good as the rows it runs
against: two queries that differ solely on NULL handling return identical
results on data without NULLs, and the comparison then "proves" an
equivalence that does not hold. So every row below exists to make some
specific rewrite fail, and the mapping is recorded here because it is the
part most likely to be broken by a well-meaning edit.

  orders.user_id is nullable and row o5 has NULL
      The NOT IN / NOT EXISTS trap. `id NOT IN (SELECT user_id FROM orders)`
      evaluates to NULL rather than TRUE for every candidate row once the
      subquery yields a single NULL, so the original returns zero rows while
      the NOT EXISTS "equivalent" returns the users with no orders. Remove
      this NULL and the two look identical.

  users 3 and 4 have no orders at all
      The other half of that trap, and of INNER JOIN vs LEFT JOIN. Without a
      user who has no orders there is nothing for NOT EXISTS to return, and
      the mismatch above disappears even with the NULL present.

  orders o1 and o8 match BOTH 'shipped' AND total > 500
      The UNION ALL trap -- the bug this project actually shipped in a seed
      case. `WHERE status = 'shipped' OR total > 500` rewritten as two
      SELECTs combined with UNION ALL emits a row matching both predicates
      twice. If no row satisfies both branches, UNION and UNION ALL return
      the same thing and the bug is invisible. Two such rows rather than one,
      so a single mis-entered row does not silently disarm the case.

  users 1 and 2 each have two 'shipped' orders
      JOIN fan-out. Rewriting `WHERE EXISTS (SELECT ... FROM orders)` as a
      JOIN duplicates the left row once per match. As *sets* the two results
      are identical, so only a multiset comparison catches it -- this is the
      row group that justifies comparing with duplicates counted.

  orders.created_at spans 2024, 2025, 2026, both 2025 boundaries, and a NULL
      Date-range rewrites. '2025-01-01' and '2025-12-31' catch an off-by-one
      in a half-open range, '2026-01-05' catches `<= '2026-01-01'`, and the
      NULL is deliberately harmless: both the strftime form and the range
      form exclude it. A fixture whose every NULL breaks something would not
      show whether the agent can tell a dangerous NULL from an inert one.

  orders o6 has a NULL status, and users 5 a NULL email
      Inert NULLs again, for the same reason.

Nothing here is production data and nothing is sensitive; it is all invented.
"""

import logging
import sqlite3
from pathlib import Path

from .config import config

logger = logging.getLogger(__name__)

# Column nullability is load-bearing, not decoration: orders.user_id must stay
# nullable for the NOT IN case to exist at all, so the NOT NULL constraints
# below are placed deliberately and sparsely.
SCHEMA = """
CREATE TABLE users (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT,
    country     TEXT,
    signup_date TEXT NOT NULL
);

CREATE TABLE orders (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER,
    status     TEXT,
    total      REAL,
    created_at TEXT
);

CREATE TABLE products (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    category TEXT,
    price    REAL NOT NULL,
    discount REAL
);

CREATE TABLE order_items (
    order_id   INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity   INTEGER NOT NULL
);
"""

USERS = [
    # id, name, email, country, signup_date
    (1, "Ada",   "ada@example.test",   "NL", "2024-02-11"),
    (2, "Brij",  "brij@example.test",  "IN", "2024-06-30"),
    (3, "Chen",  "chen@example.test",  "SG", "2025-01-09"),  # no orders
    (4, "Dara",  "dara@example.test",  "IE", "2025-04-22"),  # no orders
    (5, "Eve",   None,                 "NL", "2025-08-15"),  # NULL email
]

ORDERS = [
    # id, user_id, status, total, created_at
    (1, 1,    "shipped",   750.00, "2025-03-14"),  # shipped AND > 500
    (2, 1,    "shipped",   120.00, "2025-07-02"),  # user 1's second shipped
    (3, 2,    "pending",   980.00, "2024-11-20"),  # > 500 only, and 2024
    (4, 2,    "shipped",   300.00, "2026-01-05"),  # shipped only, and 2026
    (5, None, "delivered",  45.00, "2025-01-01"),  # NULL user_id; lower bound
    (6, 5,    None,        610.00, "2025-12-31"),  # NULL status; upper bound
    (7, 5,    "pending",    75.00, None),          # NULL created_at (inert)
    (8, 2,    "shipped",   640.00, "2025-06-06"),  # shipped AND > 500
]

PRODUCTS = [
    # id, name, category, price, discount
    (1, "Widget",  "hardware", 19.99, 0.10),
    (2, "Gadget",  "hardware", 49.50, None),   # NULL discount
    (3, "Service", None,       99.00, 0.25),   # NULL category
    (4, "Widget",  "hardware", 19.99, None),   # duplicate name + NULL
]

# Two rows for (1, 1): order_items has no primary key on purpose, so genuine
# duplicate rows exist. A multiset comparison that was accidentally written as
# a set comparison still passes on tables whose rows are all distinct, and
# this table is what makes that mistake visible.
ORDER_ITEMS = [
    (1, 1, 2),
    (1, 1, 2),
    (1, 2, 1),
    (2, 3, 5),
    (3, 1, 1),
    (8, 4, 3),
]


def build(db_path: str | Path | None = None) -> Path:
    """Create the fixture database from scratch, replacing any existing file.

    Idempotent by deletion rather than by CREATE TABLE IF NOT EXISTS: a
    half-built or stale fixture is worse than no fixture, because the agent
    would report equivalence against data that no longer contains the rows
    this module promises.
    """
    path = Path(db_path or config.VERIFY_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)

    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?)", USERS)
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", ORDERS)
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", PRODUCTS)
        conn.executemany("INSERT INTO order_items VALUES (?, ?, ?)", ORDER_ITEMS)
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "Built verifier fixture at %s (%d users, %d orders, %d products, %d items)",
        path, len(USERS), len(ORDERS), len(PRODUCTS), len(ORDER_ITEMS),
    )
    return path


def ensure(db_path: str | Path | None = None) -> Path:
    """Return the fixture path, building it only if the file is absent."""
    path = Path(db_path or config.VERIFY_DB_PATH)
    if not path.is_file():
        return build(path)
    return path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    build()


if __name__ == "__main__":
    main()

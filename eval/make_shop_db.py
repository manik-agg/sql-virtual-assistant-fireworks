"""Builds a small second database (no description CSVs) to test generalization beyond the BIRD schema.

Deliberately includes the things that trip text-to-SQL: coded status columns, a many-to-many link,
a reserved word table name ("group"), dates as ISO text, and a nullable FK.
"""

import random
import sqlite3
from pathlib import Path

OUT = Path("eval/data/shop.sqlite")
random.seed(7)


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.unlink(missing_ok=True)
    c = sqlite3.connect(OUT)
    c.executescript("""
    CREATE TABLE country (code TEXT PRIMARY KEY, name TEXT NOT NULL, continent TEXT NOT NULL);
    CREATE TABLE customer (id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, country_code TEXT REFERENCES country(code),
                           signup_date DATE NOT NULL, tier TEXT NOT NULL);
    CREATE TABLE product (id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, unit_price REAL NOT NULL);
    CREATE TABLE "group" (id INTEGER PRIMARY KEY, label TEXT NOT NULL);
    CREATE TABLE product_group (product_id INTEGER REFERENCES product(id), group_id INTEGER REFERENCES "group"(id));
    CREATE TABLE purchase (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customer(id),
                           placed_on DATE NOT NULL, st TEXT NOT NULL, ref_id INTEGER REFERENCES purchase(id));
    CREATE TABLE purchase_line (purchase_id INTEGER REFERENCES purchase(id), product_id INTEGER REFERENCES product(id),
                                qty INTEGER NOT NULL, discount_pct REAL NOT NULL);
    """)
    countries = [
        ("US", "United States", "North America"),
        ("CA", "Canada", "North America"),
        ("DE", "Germany", "Europe"),
        ("FR", "France", "Europe"),
        ("JP", "Japan", "Asia"),
        ("BR", "Brazil", "South America"),
    ]
    c.executemany("INSERT INTO country VALUES (?,?,?)", countries)
    tiers = ["free", "plus", "pro"]
    for i in range(1, 301):
        d = f"20{random.choice([21, 22, 23, 24])}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        c.execute(
            "INSERT INTO customer VALUES (?,?,?,?,?)",
            (i, f"Customer {i}", random.choice(countries)[0], d, random.choice(tiers)),
        )
    cats = ["books", "games", "music", "tools"]
    for i in range(1, 41):
        c.execute(
            "INSERT INTO product VALUES (?,?,?,?)",
            (i, f"Product {i}", random.choice(cats), round(random.uniform(5, 200), 2)),
        )
    c.executemany('INSERT INTO "group" VALUES (?,?)', [(1, "bestseller"), (2, "clearance"), (3, "new")])
    for p in range(1, 41):
        for g in random.sample([1, 2, 3], random.randint(0, 2)):
            c.execute("INSERT INTO product_group VALUES (?,?)", (p, g))
    # st: P=paid, S=shipped, X=cancelled, R=refunded (no documentation on purpose; refunds point to the original purchase)
    for i in range(1, 1201):
        d = f"202{random.randint(2, 4)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        c.execute(
            "INSERT INTO purchase VALUES (?,?,?,?,?)",
            (i, random.randint(1, 300), d, random.choices("PSXR", [30, 50, 12, 8])[0], None),
        )
        for p in random.sample(range(1, 41), random.randint(1, 4)):
            c.execute(
                "INSERT INTO purchase_line VALUES (?,?,?,?)",
                (i, p, random.randint(1, 5), random.choice([0, 0, 0, 10, 20])),
            )
    c.commit()
    print("built", OUT)


if __name__ == "__main__":
    main()

"""业务数据库层：高频交易事实留在关系库。"""
from __future__ import annotations
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS passengers (
  passenger_id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drivers (
  driver_id TEXT PRIMARY KEY, name TEXT NOT NULL, vehicle_type TEXT NOT NULL,
  region TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vehicles (
  vehicle_id TEXT PRIMARY KEY, plate TEXT NOT NULL, vehicle_type TEXT NOT NULL,
  status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS coupons (
  coupon_id TEXT PRIMARY KEY, amount REAL NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY, passenger_id TEXT, origin TEXT, destination TEXT,
  region TEXT, vehicle_type TEXT, created_at TEXT, status TEXT, driver_id TEXT,
  coupon_id TEXT, cancel_fee REAL DEFAULT 0, cancel_reason TEXT,
  cancel_rule_id TEXT, cancel_decision TEXT, ticket_id TEXT);
CREATE TABLE IF NOT EXISTS trips (
  trip_id TEXT PRIMARY KEY, order_id TEXT, driver_id TEXT, vehicle_id TEXT,
  route TEXT, status TEXT, started_at TEXT, arrived_at TEXT, onboard_at TEXT,
  ended_at TEXT, distance_km REAL DEFAULT 0, duration_min REAL DEFAULT 0,
  meter_start_at TEXT, meter_end_at TEXT);
CREATE TABLE IF NOT EXISTS billing_records (
  billing_id TEXT PRIMARY KEY, trip_id TEXT, order_id TEXT,
  base_fare REAL DEFAULT 0, distance_fee REAL DEFAULT 0, time_fee REAL DEFAULT 0,
  region_surcharge REAL DEFAULT 0, coupon_deduction REAL DEFAULT 0,
  total REAL, source TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY, billing_id TEXT, order_id TEXT,
  amount REAL, status TEXT, paid_at TEXT);
CREATE TABLE IF NOT EXISTS refunds (
  refund_id TEXT PRIMARY KEY, payment_id TEXT, order_id TEXT,
  amount REAL, created_at TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS dispatches (
  dispatch_id TEXT PRIMARY KEY, order_id TEXT, driver_id TEXT, sent_at TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS support_tickets (
  ticket_id TEXT PRIMARY KEY, order_id TEXT, question TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_trips_order ON trips(order_id);
CREATE INDEX IF NOT EXISTS idx_trips_driver ON trips(driver_id, status);
CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trips(vehicle_id, status);
CREATE INDEX IF NOT EXISTS idx_billing_order ON billing_records(order_id);
"""


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.executescript(SCHEMA)

    def run(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, args)

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def insert(self, table: str, data: dict) -> None:
        cols = ",".join(data.keys())
        marks = ",".join("?" for _ in data)
        self.conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values())
        )

    def update(self, table: str, key: str, keyval: str, data: dict) -> None:
        sets = ",".join(f"{k}=?" for k in data)
        self.conn.execute(
            f"UPDATE {table} SET {sets} WHERE {key}=?",
            tuple(data.values()) + (keyval,),
        )

    def scalar(self, sql: str, args: tuple = ()):
        row = self.conn.execute(sql, args).fetchone()
        return row[0] if row else None

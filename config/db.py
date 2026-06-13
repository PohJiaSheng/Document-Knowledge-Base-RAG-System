"""Oracle DB connection pool — module-level singleton for connection reuse across requests."""
from __future__ import annotations

import os

import oracledb

DB_CONFIG = {
    "username":     os.getenv("DB_USER",         ""),
    "password":     os.getenv("DB_PASSWORD",     ""),
    "host":         os.getenv("DB_HOST",         ""),
    "port":         int(os.getenv("DB_PORT",     "")),
    "sid":          os.getenv("DB_SID",          ""),
    "service_name": os.getenv("DB_SERVICE_NAME") or None,
}

if DB_CONFIG["sid"]:
    _dsn = oracledb.makedsn(DB_CONFIG["host"], DB_CONFIG["port"], sid=DB_CONFIG["sid"])
else:
    _dsn = oracledb.makedsn(
        DB_CONFIG["host"], DB_CONFIG["port"], service_name=DB_CONFIG["service_name"]
    )

# Create pool once at import time — reused for all requests (fast).
pool = oracledb.create_pool(
    user=DB_CONFIG["username"],
    password=DB_CONFIG["password"],
    dsn=_dsn,
    min=1,
    max=5,
    increment=1,
    homogeneous=True,
)


def get_conn():
    """Acquire a connection from the pool."""
    return pool.acquire()

"""
DloWatch database (SQLite, one file, no server to install).

Tables
  alerts          leak alerts raised by the detector or by public reports
  reports         public leak reports
  jobs            crew jobs
  crew_results    what crews found (used to measure and improve detection)
  network_leaks   leaks in the simulated network (the "field"; never shown on screen)
  users           staff and crew accounts, passwords and PINs hashed (PBKDF2)
  meta            counters and settings that must survive a restart

The app keeps working copies in memory for speed. flush() writes every record that changed
since the last flush, so a restart picks up exactly where it stopped.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("data") / "dlowatch.db"

TABLES = {
    # name: (key column, [indexed columns taken from the record])
    "alerts": ("id", ["zone", "kind", "status", "created"]),
    "reports": ("code", ["zone", "created", "alert"]),
    "jobs": ("id", ["alert", "crew_id", "status", "created"]),
    "crew_results": ("id", ["alert", "zone", "label", "at"]),
    "network_leaks": ("id", ["zone", "pipe_id", "start", "end"]),
}


def _hash(secret, salt=None):
    salt = salt or os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), 120_000).hex()
    return f"pbkdf2${salt}${h}"


def check_secret(secret, stored):
    try:
        _, salt, _h = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(_hash(secret, salt), stored)


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("DLOWATCH_DB") or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.set_aside_if_old()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        self.saved = {t: {} for t in TABLES}      # table -> id -> json text last written
        with self.db:
            for t, (key, cols) in TABLES.items():
                extra = "".join(f", {c}" for c in cols)
                self.db.execute(f"CREATE TABLE IF NOT EXISTS {t} ({key} TEXT PRIMARY KEY{extra}, data TEXT NOT NULL, updated INTEGER)")
                for c in cols:
                    self.db.execute(f"CREATE INDEX IF NOT EXISTS ix_{t}_{c} ON {t}({c})")
            self.db.execute("""CREATE TABLE IF NOT EXISTS users (
                login TEXT PRIMARY KEY, kind TEXT NOT NULL, role TEXT NOT NULL, name TEXT NOT NULL,
                crew_id TEXT, secret_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created INTEGER)""")
            self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")

    def set_aside_if_old(self):
        """A database file made by an earlier version (different tables) is renamed, not deleted,
        and a new one is started. Nothing is lost: the old file stays next to the new one."""
        if not self.path.exists():
            return
        con = sqlite3.connect(self.path)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            old = False
            for t in TABLES:
                if t in tables:
                    cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                    old = old or "data" not in cols
            old = old or "staff" in tables or "crew_logins" in tables
        finally:
            con.close()
        if old:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for suffix in ("", "-wal", "-shm"):
                f = Path(str(self.path) + suffix)
                if f.exists():
                    f.rename(f.with_name(f"{self.path.stem}-old-{stamp}{self.path.suffix}{suffix}"))
            print(f"DloWatch: the database was made by an earlier version, kept as {self.path.stem}-old-{stamp}{self.path.suffix}; "
                  "starting a new one")

    # ------------------------------------------------------------ records
    def load(self, table):
        key = TABLES[table][0]
        out = {}
        for k, data in self.db.execute(f"SELECT {key}, data FROM {table}"):
            out[k] = json.loads(data)
            self.saved[table][k] = data
        return out

    def flush(self, table, records):
        """Write records (dict id -> record) that changed, delete ones that are gone. Returns rows written."""
        key, cols = TABLES[table]
        seen, n, now = set(), 0, int(time.time())
        with self.lock, self.db:
            for rid, rec in list(records.items()):
                rid = str(rid)
                seen.add(rid)
                text = json.dumps(rec, separators=(",", ":"), default=float)
                if self.saved[table].get(rid) == text:
                    continue
                vals = [rid] + [rec.get(c) for c in cols] + [text, now]
                ph = ",".join("?" * len(vals))
                self.db.execute(f"INSERT OR REPLACE INTO {table} ({key}{''.join(', ' + c for c in cols)}, data, updated) VALUES ({ph})", vals)
                self.saved[table][rid] = text
                n += 1
            for rid in [r for r in self.saved[table] if r not in seen]:
                self.db.execute(f"DELETE FROM {table} WHERE {key} = ?", (rid,))
                del self.saved[table][rid]
                n += 1
        return n

    # ------------------------------------------------------------ meta
    def get_meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # ------------------------------------------------------------ users
    def users_count(self):
        return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def add_user(self, login, kind, role, name, secret, crew_id=None):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO users (login, kind, role, name, crew_id, secret_hash, active, created) "
                            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)", (login, kind, role, name, crew_id, _hash(secret), int(time.time())))

    def check_user(self, login, kind, secret):
        row = self.db.execute("SELECT role, name, crew_id, secret_hash, active FROM users WHERE login = ? AND kind = ?",
                              (login, kind)).fetchone()
        if not row or not row[4] or not check_secret(secret, row[3]):
            return None
        return {"role": row[0], "name": row[1], "crew_id": row[2]}


# ------------------------------------------------------------ command line
if __name__ == "__main__":
    import sys
    st = Store()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "info"
    if cmd == "info":
        print(st.path)
        for t in list(TABLES) + ["users"]:
            print(f"  {t:14s} {st.db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    elif cmd == "reset":
        # same as the admin Reset button: keeps accounts, settings and the login key
        with st.db:
            for t in TABLES:
                st.db.execute(f"DELETE FROM {t}")
        print("Alerts, reports, jobs, crew results and network leaks deleted. Accounts kept.")
    else:
        print("Usage: python store.py [info|reset]")

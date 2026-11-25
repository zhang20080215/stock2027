"""SQLite schema shared by the collector and read-only web service."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, channel TEXT, title TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, session_id TEXT, role TEXT, timestamp TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id,timestamp);
CREATE INDEX IF NOT EXISTS messages_time ON messages(timestamp);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, session_id TEXT, tool TEXT, label TEXT, started TEXT, finished TEXT, status TEXT);
CREATE INDEX IF NOT EXISTS tasks_time ON tasks(started);
CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY, timestamp TEXT, market TEXT, data TEXT, session_id TEXT, message_id TEXT);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, title TEXT, timestamp TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, session_id TEXT, timestamp TEXT, title TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS model_results(id TEXT PRIMARY KEY, session_id TEXT, user_message_id TEXT UNIQUE, prompt TEXT, timestamp TEXT, provider TEXT, model TEXT, text TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS model_results_time ON model_results(timestamp);
CREATE TABLE IF NOT EXISTS model_events(id TEXT PRIMARY KEY, session_id TEXT, user_message_id TEXT, prompt TEXT, timestamp TEXT, provider TEXT, model TEXT, text TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS model_events_time ON model_events(timestamp);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS paper_accounts(market TEXT PRIMARY KEY, initial REAL NOT NULL, cash REAL NOT NULL);
CREATE TABLE IF NOT EXISTS paper_orders(id TEXT PRIMARY KEY, request_id TEXT NOT NULL, result_id TEXT NOT NULL, market TEXT NOT NULL, code TEXT NOT NULL, name TEXT, created TEXT NOT NULL, signal_time TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, plan TEXT NOT NULL, shares INTEGER, price REAL, filled TEXT, quote_time TEXT, UNIQUE(request_id,market,code));
CREATE TABLE IF NOT EXISTS paper_quotes(symbol TEXT PRIMARY KEY, price REAL, ask REAL, timestamp TEXT, lot INTEGER, fetched TEXT);
CREATE TABLE IF NOT EXISTS history_bars(provider TEXT, market TEXT, code TEXT, time TEXT, close REAL, volume REAL, PRIMARY KEY(provider,market,code,time));
"""


def connect(path, readonly=False):
    if readonly:
        from pathlib import Path
        connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    else:
        connection = sqlite3.connect(path, timeout=30)
        # Rollback journals allow readers in a filesystem read-only sandbox.
        # WAL readers may need to create -shm files even with mode=ro.
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.executescript(SCHEMA)
    connection.row_factory = sqlite3.Row
    return connection

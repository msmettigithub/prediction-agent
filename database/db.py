"""SQLite database setup and operations."""

from __future__ import annotations

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from database.models import Contract, Prediction, Resolution, ToolRun

SCHEMA_VERSION = 2

MIGRATIONS = [
    # v1: initial schema
    """
    CREATE TABLE IF NOT EXISTS contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        yes_price REAL NOT NULL DEFAULT 0.0,
        volume_24h REAL NOT NULL DEFAULT 0.0,
        open_time TEXT,
        close_time TEXT,
        resolved INTEGER NOT NULL DEFAULT 0,
        resolution INTEGER,
        resolved_at TEXT,
        cross_market_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source, source_id)
    );

    CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER NOT NULL REFERENCES contracts(id),
        model_prob REAL NOT NULL,
        confidence TEXT NOT NULL DEFAULT 'low',
        edge REAL NOT NULL DEFAULT 0.0,
        kelly_fraction REAL NOT NULL DEFAULT 0.0,
        recommendation TEXT NOT NULL DEFAULT 'PASS',
        key_factors TEXT NOT NULL DEFAULT '[]',
        bull_case TEXT NOT NULL DEFAULT '',
        bear_case TEXT NOT NULL DEFAULT '',
        tools_used TEXT NOT NULL DEFAULT '[]',
        tools_failed TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS resolutions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER NOT NULL REFERENCES contracts(id),
        prediction_id INTEGER REFERENCES predictions(id),
        model_prob REAL NOT NULL,
        market_prob REAL NOT NULL,
        resolved_yes INTEGER NOT NULL,
        brier_component REAL NOT NULL,
        correct_direction INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tool_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool_name TEXT NOT NULL,
        contract_id INTEGER REFERENCES contracts(id),
        success INTEGER NOT NULL DEFAULT 0,
        latency_ms REAL NOT NULL DEFAULT 0.0,
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    );

    INSERT INTO schema_version (version) VALUES (1);

    CREATE INDEX IF NOT EXISTS idx_contracts_source ON contracts(source, source_id);
    CREATE INDEX IF NOT EXISTS idx_contracts_resolved ON contracts(resolved);
    CREATE INDEX IF NOT EXISTS idx_contracts_category ON contracts(category);
    CREATE INDEX IF NOT EXISTS idx_predictions_contract ON predictions(contract_id);
    CREATE INDEX IF NOT EXISTS idx_resolutions_contract ON resolutions(contract_id);
    CREATE INDEX IF NOT EXISTS idx_tool_runs_tool ON tool_runs(tool_name);
    """,
    # v2: deep_dive_results table + deep_dive_id FK on predictions + alerted column on contracts
    """
    CREATE TABLE IF NOT EXISTS deep_dive_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER NOT NULL REFERENCES contracts(id),
        model_probability REAL NOT NULL,
        confidence TEXT NOT NULL DEFAULT 'low',
        edge REAL NOT NULL DEFAULT 0.0,
        kelly_fraction REAL NOT NULL DEFAULT 0.0,
        recommended_action TEXT NOT NULL DEFAULT 'PASS',
        key_factors TEXT NOT NULL DEFAULT '[]',
        bull_case TEXT NOT NULL DEFAULT '',
        bear_case TEXT NOT NULL DEFAULT '',
        base_rate_used REAL NOT NULL DEFAULT 0.5,
        modifiers_applied TEXT NOT NULL DEFAULT '[]',
        tools_used TEXT NOT NULL DEFAULT '[]',
        tools_failed TEXT NOT NULL DEFAULT '[]',
        reasoning_trace TEXT NOT NULL DEFAULT '',
        generated_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_deep_dive_contract ON deep_dive_results(contract_id);
    """,
    # v3: alerted_at on contracts + deep_dive_id FK on predictions
    """
    -- handled programmatically in _migrate_v3()
    """,
    # v4: paper_trades table
    """
    CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER NOT NULL REFERENCES contracts(id),
        side TEXT NOT NULL,             -- 'YES' or 'NO'
        entry_price REAL NOT NULL,      -- market price when trade opened
        model_prob REAL NOT NULL,       -- model's probability estimate
        kelly_fraction REAL NOT NULL,   -- quarter-Kelly fraction used
        bet_amount REAL NOT NULL,       -- dollar amount of paper bet
        status TEXT NOT NULL DEFAULT 'open',  -- 'open', 'won', 'lost'
        exit_price REAL,                -- resolution price (1.0 or 0.0)
        pnl REAL,                       -- profit/loss in dollars
        opened_at TEXT NOT NULL,
        closed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);
    CREATE INDEX IF NOT EXISTS idx_paper_trades_contract ON paper_trades(contract_id);
    """,
    # v5: live_trades table — REAL ORDERS PLACED ON KALSHI
    """
    CREATE TABLE IF NOT EXISTS live_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER NOT NULL REFERENCES contracts(id),
        kalshi_order_id TEXT,
        kalshi_ticker TEXT NOT NULL,
        side TEXT NOT NULL,
        entry_price REAL NOT NULL,
        shares INTEGER NOT NULL,
        cost REAL NOT NULL,
        max_payout REAL NOT NULL,
        model_prob REAL NOT NULL,
        edge_at_entry REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        exit_price REAL,
        pnl REAL,
        opened_at TEXT NOT NULL,
        closed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_live_trades_status ON live_trades(status);
    CREATE INDEX IF NOT EXISTS idx_live_trades_contract ON live_trades(contract_id);
    """,
]


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self):
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        if cursor.fetchone() is None:
            # Fresh DB — run all DDL migrations, then programmatic ones
            for i, m in enumerate(MIGRATIONS):
                if i == 2:  # v3 is programmatic
                    self._migrate_v3()
                else:
                    self.conn.executescript(m)
            self.conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (len(MIGRATIONS),))
            self.conn.commit()
            return

        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        current_version = row["version"] if row else 0
        for i in range(current_version, len(MIGRATIONS)):
            if i == 2:
                # v3 uses ALTER TABLE — handled programmatically
                self._migrate_v3()
            else:
                self.conn.executescript(MIGRATIONS[i])
        self.conn.execute("UPDATE schema_version SET version = ?", (len(MIGRATIONS),))
        self.conn.commit()

    def _migrate_v3(self):
        """v3: add alerted_at to contracts, deep_dive_id to predictions.
        Uses ALTER TABLE with duplicate-column guard for safe re-runs."""
        alter_statements = [
            "ALTER TABLE contracts ADD COLUMN alerted_at TEXT",
            "ALTER TABLE predictions ADD COLUMN deep_dive_id INTEGER REFERENCES deep_dive_results(id)",
        ]
        for stmt in alter_statements:
            try:
                self.conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass  # column already exists — safe to ignore
                else:
                    raise

    def close(self):
        self.conn.close()

    # --- Contract operations ---

    def upsert_contract(self, c: Contract) -> int:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT INTO contracts (source, source_id, title, category, yes_price,
               volume_24h, open_time, close_time, resolved, resolution, resolved_at,
               cross_market_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, source_id) DO UPDATE SET
               yes_price=excluded.yes_price, volume_24h=excluded.volume_24h,
               close_time=excluded.close_time, resolved=excluded.resolved,
               resolution=excluded.resolution, resolved_at=excluded.resolved_at,
               cross_market_id=excluded.cross_market_id, updated_at=excluded.updated_at""",
            (
                c.source, c.source_id, c.title, c.category, c.yes_price,
                c.volume_24h,
                c.open_time.isoformat() if c.open_time else None,
                c.close_time.isoformat() if c.close_time else None,
                int(c.resolved),
                int(c.resolution) if c.resolution is not None else None,
                c.resolved_at.isoformat() if c.resolved_at else None,
                c.cross_market_id,
                c.created_at.isoformat() if c.created_at else now,
                now,
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM contracts WHERE source=? AND source_id=?",
            (c.source, c.source_id),
        ).fetchone()
        return row["id"]

    def get_contract(self, contract_id: int) -> Optional[Contract]:
        row = self.conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_contract(row)

    def get_contract_by_source(self, source: str, source_id: str) -> Optional[Contract]:
        row = self.conn.execute(
            "SELECT * FROM contracts WHERE source=? AND source_id=?",
            (source, source_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_contract(row)

    def get_open_contracts(self) -> list[Contract]:
        rows = self.conn.execute(
            "SELECT * FROM contracts WHERE resolved=0 ORDER BY close_time"
        ).fetchall()
        return [self._row_to_contract(r) for r in rows]

    def get_resolved_contracts(self) -> list[Contract]:
        rows = self.conn.execute(
            "SELECT * FROM contracts WHERE resolved=1 AND resolution IS NOT NULL ORDER BY resolved_at"
        ).fetchall()
        return [self._row_to_contract(r) for r in rows]

    def update_contract_resolution(self, contract_id: int, resolution: bool,
                                    resolved_at: Optional[datetime] = None) -> bool:
        """Mark a contract as resolved with a known outcome.

        Returns True if the row was updated, False if not found.
        No-op if the contract is already resolved (won't overwrite).
        """
        now = (resolved_at or datetime.utcnow()).isoformat()
        cursor = self.conn.execute(
            """UPDATE contracts SET resolved=1, resolution=?, resolved_at=?, updated_at=?
               WHERE id=? AND resolved=0""",
            (int(resolution), now, datetime.utcnow().isoformat(), contract_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def _row_to_contract(self, row) -> Contract:
        return Contract(
            id=row["id"],
            source=row["source"],
            source_id=row["source_id"],
            title=row["title"],
            category=row["category"],
            yes_price=row["yes_price"],
            volume_24h=row["volume_24h"],
            open_time=datetime.fromisoformat(row["open_time"]) if row["open_time"] else None,
            close_time=datetime.fromisoformat(row["close_time"]) if row["close_time"] else None,
            resolved=bool(row["resolved"]),
            resolution=bool(row["resolution"]) if row["resolution"] is not None else None,
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
            cross_market_id=row["cross_market_id"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            alerted_at=datetime.fromisoformat(row["alerted_at"]) if row["alerted_at"] else None,
        )

    def set_alerted(self, contract_id: int):
        """Mark a contract as alerted (persisted across restarts)."""
        now = datetime.utcnow().isoformat()
        self.conn.execute("UPDATE contracts SET alerted_at=? WHERE id=?", (now, contract_id))
        self.conn.commit()

    def get_unalerted_edge_candidates(self) -> list[Contract]:
        """Return open contracts that have never been alerted."""
        rows = self.conn.execute(
            "SELECT * FROM contracts WHERE resolved=0 AND alerted_at IS NULL ORDER BY close_time"
        ).fetchall()
        return [self._row_to_contract(r) for r in rows]

    # --- Prediction operations ---

    def insert_prediction(self, p: Prediction) -> int:
        now = datetime.utcnow().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO predictions (contract_id, model_prob, confidence, edge,
               kelly_fraction, recommendation, key_factors, bull_case, bear_case,
               tools_used, tools_failed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                p.contract_id, p.model_prob, p.confidence, p.edge,
                p.kelly_fraction, p.recommendation, p.key_factors,
                p.bull_case, p.bear_case, p.tools_used, p.tools_failed, now,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_predictions_for_contract(self, contract_id: int) -> list[Prediction]:
        rows = self.conn.execute(
            "SELECT * FROM predictions WHERE contract_id=? ORDER BY created_at DESC",
            (contract_id,),
        ).fetchall()
        return [self._row_to_prediction(r) for r in rows]

    def _row_to_prediction(self, row) -> Prediction:
        return Prediction(
            id=row["id"],
            contract_id=row["contract_id"],
            model_prob=row["model_prob"],
            confidence=row["confidence"],
            edge=row["edge"],
            kelly_fraction=row["kelly_fraction"],
            recommendation=row["recommendation"],
            key_factors=row["key_factors"],
            bull_case=row["bull_case"],
            bear_case=row["bear_case"],
            tools_used=row["tools_used"],
            tools_failed=row["tools_failed"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    # --- Resolution operations ---

    def insert_resolution(self, r: Resolution) -> int:
        now = datetime.utcnow().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO resolutions (contract_id, prediction_id, model_prob,
               market_prob, resolved_yes, brier_component, correct_direction, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r.contract_id, r.prediction_id, r.model_prob,
                r.market_prob, int(r.resolved_yes), r.brier_component,
                int(r.correct_direction), now,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_all_resolutions(self) -> list[Resolution]:
        rows = self.conn.execute("SELECT * FROM resolutions ORDER BY created_at").fetchall()
        return [self._row_to_resolution(r) for r in rows]

    def _row_to_resolution(self, row) -> Resolution:
        return Resolution(
            id=row["id"],
            contract_id=row["contract_id"],
            prediction_id=row["prediction_id"],
            model_prob=row["model_prob"],
            market_prob=row["market_prob"],
            resolved_yes=bool(row["resolved_yes"]),
            brier_component=row["brier_component"],
            correct_direction=bool(row["correct_direction"]),
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    # --- ToolRun operations ---

    def insert_tool_run(self, t: ToolRun) -> int:
        now = datetime.utcnow().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO tool_runs (tool_name, contract_id, success, latency_ms,
               error_message, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (t.tool_name, t.contract_id, int(t.success), t.latency_ms, t.error_message, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    # --- Deep dive result operations ---

    def insert_deep_dive_result(self, result: dict) -> int:
        now = datetime.utcnow().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO deep_dive_results (contract_id, model_probability, confidence,
               edge, kelly_fraction, recommended_action, key_factors, bull_case, bear_case,
               base_rate_used, modifiers_applied, tools_used, tools_failed,
               reasoning_trace, generated_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result["contract_id"], result["model_probability"], result["confidence"],
                result["edge"], result["kelly_fraction"], result["recommended_action"],
                json.dumps(result["key_factors"]), result["bull_case"], result["bear_case"],
                result["base_rate_used"], json.dumps(result["modifiers_applied"]),
                json.dumps(result["tools_used"]), json.dumps(result["tools_failed"]),
                result["reasoning_trace"], result["generated_at"], now,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    # --- Paper trade operations ---

    def insert_paper_trade(self, trade: dict) -> int:
        now = datetime.utcnow().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO paper_trades (contract_id, side, entry_price, model_prob,
               kelly_fraction, bet_amount, status, opened_at)
               VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
            (
                trade["contract_id"], trade["side"], trade["entry_price"],
                trade["model_prob"], trade["kelly_fraction"], trade["bet_amount"], now,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_open_paper_trades(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM paper_trades WHERE status='open' ORDER BY opened_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_paper_trades(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM paper_trades ORDER BY opened_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def close_paper_trade(self, trade_id: int, won: bool, exit_price: float, pnl: float):
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """UPDATE paper_trades SET status=?, exit_price=?, pnl=?, closed_at=?
               WHERE id=?""",
            ("won" if won else "lost", exit_price, pnl, now, trade_id),
        )
        self.conn.commit()

    def has_open_paper_trade(self, contract_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM paper_trades WHERE contract_id=? AND status='open' LIMIT 1",
            (contract_id,),
        ).fetchone()
        return row is not None

    # --- Live trade operations ---

    def insert_live_trade(self, trade: dict) -> int:
        now = datetime.utcnow().isoformat()
        cursor = self.conn.execute(
            """INSERT INTO live_trades (contract_id, kalshi_order_id, kalshi_ticker, side,
               entry_price, shares, cost, max_payout, model_prob, edge_at_entry, status, opened_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (
                trade["contract_id"], trade.get("kalshi_order_id"), trade["kalshi_ticker"],
                trade["side"], trade["entry_price"], trade["shares"], trade["cost"],
                trade["max_payout"], trade["model_prob"], trade["edge_at_entry"], now,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_open_live_trades(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM live_trades WHERE status='open' ORDER BY opened_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_live_trades(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM live_trades ORDER BY opened_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def close_live_trade(self, trade_id: int, won: bool, exit_price: float, pnl: float):
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """UPDATE live_trades SET status=?, exit_price=?, pnl=?, closed_at=?
               WHERE id=?""",
            ("won" if won else "lost", exit_price, pnl, now, trade_id),
        )
        self.conn.commit()

    def has_open_live_trade(self, contract_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM live_trades WHERE contract_id=? AND status='open' LIMIT 1",
            (contract_id,),
        ).fetchone()
        return row is not None

    def total_live_deployed(self) -> float:
        """Sum of cost across all open live trades."""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost), 0) AS total FROM live_trades WHERE status='open'"
        ).fetchone()
        return float(row["total"]) if row else 0.0

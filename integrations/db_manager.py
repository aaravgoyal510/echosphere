import os
import json
import logging
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class DBManager:
    def __init__(
        self, 
        pg_host: str = "localhost", 
        pg_port: int = 5432, 
        pg_user: str = "postgres", 
        pg_password: str = "postgres", 
        pg_database: str = "echosphere",
        sqlite_path: str = "echosphere.db"
    ):
        self.use_sqlite = False
        self.sqlite_path = sqlite_path
        self.pg_conn = None
        
        try:
            # Try to connect to PostgreSQL
            self.pg_conn = psycopg2.connect(
                host=pg_host,
                port=pg_port,
                user=pg_user,
                password=pg_password,
                dbname=pg_database,
                connect_timeout=2
            )
            self.pg_conn.autocommit = True
            logger.info("Connected to PostgreSQL successfully.")
        except Exception as e:
            self.use_sqlite = True
            logger.info(
                f"Using local SQLite database at '{sqlite_path}' (PostgreSQL not configured)."
            )

    def get_connection(self):
        if self.use_sqlite:
            # SQLite connection
            conn = sqlite3.connect(self.sqlite_path)
            conn.row_factory = sqlite3.Row
            return conn
        else:
            # PostgreSQL connection
            return self.pg_conn

    def initialize_tables(self):
        if self.use_sqlite:
            self._initialize_sqlite()
        else:
            self._initialize_postgres()

    def _initialize_postgres(self):
        with self.pg_conn.cursor() as cur:
            # Enable pgvector if available
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                logger.info("pgvector extension verified/enabled.")
                has_vector = True
            except Exception as e:
                logger.warning(f"Could not enable pgvector extension: {e}. Storing embeddings as arrays.")
                has_vector = False

            # Create tables
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pricing_tiers (
                    tier_id VARCHAR(50) PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    min_seats INTEGER NOT NULL,
                    max_seats INTEGER,
                    price_per_seat_monthly NUMERIC(10, 2) NOT NULL,
                    included_features TEXT[] NOT NULL,
                    onboarding_fee NUMERIC(10, 2) NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS promotions (
                    promo_id VARCHAR(50) PRIMARY KEY,
                    description TEXT NOT NULL,
                    discount_pct NUMERIC(5, 2) NOT NULL,
                    valid_until VARCHAR(50) NOT NULL,
                    applies_to_tiers VARCHAR(50)[] NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    lead_id VARCHAR(50) PRIMARY KEY,
                    external_crm_id VARCHAR(100),
                    name VARCHAR(150),
                    phone VARCHAR(50) UNIQUE NOT NULL,
                    email VARCHAR(150),
                    company VARCHAR(150),
                    status VARCHAR(50) NOT NULL,
                    qualification JSONB NOT NULL,
                    source VARCHAR(50) NOT NULL,
                    owner VARCHAR(100),
                    created_at VARCHAR(50) NOT NULL,
                    updated_at VARCHAR(50) NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS call_log_entries (
                    call_id VARCHAR(50) PRIMARY KEY,
                    lead_id VARCHAR(50) REFERENCES leads(lead_id),
                    started_at VARCHAR(50) NOT NULL,
                    ended_at VARCHAR(50) NOT NULL,
                    duration_sec NUMERIC(10, 2) NOT NULL,
                    transcript_url TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    objections_raised JSONB NOT NULL,
                    outcome VARCHAR(50) NOT NULL,
                    escalation_reason TEXT
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS available_slots (
                    slot_id VARCHAR(50) PRIMARY KEY,
                    slot_start VARCHAR(50) NOT NULL,
                    slot_end VARCHAR(50) NOT NULL,
                    meeting_type VARCHAR(50) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'available'
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id VARCHAR(50) PRIMARY KEY,
                    lead_id VARCHAR(50) NOT NULL,
                    slot_start VARCHAR(50) NOT NULL,
                    slot_end VARCHAR(50) NOT NULL,
                    meeting_type VARCHAR(50) NOT NULL,
                    notes TEXT,
                    created_at VARCHAR(50) NOT NULL
                );
            """)

            # Use vector type if extension is loaded, otherwise standard float array
            embedding_type = "vector(1536)" if has_vector else "DOUBLE PRECISION[]"
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS kb_documents (
                    doc_id VARCHAR(50) PRIMARY KEY,
                    type VARCHAR(50) NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    content TEXT NOT NULL,
                    competitor_name VARCHAR(100),
                    updated_at VARCHAR(50) NOT NULL,
                    embedding {embedding_type}
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS call_stats (
                    call_id VARCHAR(50) PRIMARY KEY,
                    timestamp VARCHAR(50) NOT NULL,
                    outcome VARCHAR(50) NOT NULL,
                    objections_raised INTEGER NOT NULL,
                    objections_resolved INTEGER NOT NULL,
                    guardrail_triggers INTEGER NOT NULL,
                    team_size INTEGER,
                    competitors_mentioned TEXT,
                    duration_seconds NUMERIC(10, 2) NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS lead_summaries (
                    lead_id VARCHAR(50) PRIMARY KEY,
                    summary TEXT NOT NULL,
                    updated_at VARCHAR(50) NOT NULL
                );
            """)
            logger.info("PostgreSQL tables initialized.")

    def _initialize_sqlite(self):
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pricing_tiers (
                    tier_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    min_seats INTEGER NOT NULL,
                    max_seats INTEGER,
                    price_per_seat_monthly REAL NOT NULL,
                    included_features TEXT NOT NULL, -- JSON array
                    onboarding_fee REAL NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS promotions (
                    promo_id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    discount_pct REAL NOT NULL,
                    valid_until TEXT NOT NULL,
                    applies_to_tiers TEXT NOT NULL -- JSON array
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    lead_id TEXT PRIMARY KEY,
                    external_crm_id TEXT,
                    name TEXT,
                    phone TEXT UNIQUE NOT NULL,
                    email TEXT,
                    company TEXT,
                    status TEXT NOT NULL,
                    qualification TEXT NOT NULL, -- JSON object
                    source TEXT NOT NULL,
                    owner TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS call_log_entries (
                    call_id TEXT PRIMARY KEY,
                    lead_id TEXT REFERENCES leads(lead_id),
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    duration_sec REAL NOT NULL,
                    transcript_url TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    objections_raised TEXT NOT NULL, -- JSON array
                    outcome TEXT NOT NULL,
                    escalation_reason TEXT
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS available_slots (
                    slot_id TEXT PRIMARY KEY,
                    slot_start TEXT NOT NULL,
                    slot_end TEXT NOT NULL,
                    meeting_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'available'
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    slot_start TEXT NOT NULL,
                    slot_end TEXT NOT NULL,
                    meeting_type TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS kb_documents (
                    doc_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    competitor_name TEXT,
                    updated_at TEXT NOT NULL,
                    embedding TEXT -- JSON array of floats
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS call_stats (
                    call_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    objections_raised INTEGER NOT NULL,
                    objections_resolved INTEGER NOT NULL,
                    guardrail_triggers INTEGER NOT NULL,
                    team_size INTEGER,
                    competitors_mentioned TEXT,
                    duration_seconds REAL NOT NULL
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS lead_summaries (
                    lead_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.commit()
            logger.info("SQLite tables initialized.")
        finally:
            conn.close()

    def get_all_pricing_tiers(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            if self.use_sqlite:
                cur.execute("SELECT * FROM pricing_tiers")
                rows = cur.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "tier_id": row["tier_id"],
                        "name": row["name"],
                        "min_seats": row["min_seats"],
                        "max_seats": row["max_seats"],
                        "price_per_seat_monthly": row["price_per_seat_monthly"],
                        "included_features": json.loads(row["included_features"]) if isinstance(row["included_features"], str) else row["included_features"],
                        "onboarding_fee": row["onboarding_fee"]
                    })
                return results
            else:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM pricing_tiers")
                    return [dict(r) for r in cur.fetchall()]
        finally:
            if self.use_sqlite:
                conn.close()

    def save_pricing_tier(self, tier_id: str, name: str, min_seats: int, max_seats: Optional[int], price_per_seat_monthly: float, included_features: List[str], onboarding_fee: float):
        conn = self.get_connection()
        features_json = json.dumps(included_features)
        try:
            cur = conn.cursor()
            if self.use_sqlite:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO pricing_tiers 
                    (tier_id, name, min_seats, max_seats, price_per_seat_monthly, included_features, onboarding_fee)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tier_id, name, min_seats, max_seats, price_per_seat_monthly, features_json, onboarding_fee)
                )
                conn.commit()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO pricing_tiers 
                        (tier_id, name, min_seats, max_seats, price_per_seat_monthly, included_features, onboarding_fee)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tier_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        min_seats = EXCLUDED.min_seats,
                        max_seats = EXCLUDED.max_seats,
                        price_per_seat_monthly = EXCLUDED.price_per_seat_monthly,
                        included_features = EXCLUDED.included_features,
                        onboarding_fee = EXCLUDED.onboarding_fee
                        """,
                        (tier_id, name, min_seats, max_seats, price_per_seat_monthly, included_features, onboarding_fee)
                    )
        finally:
            if self.use_sqlite:
                conn.close()

    def delete_pricing_tier(self, tier_id: str):
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            if self.use_sqlite:
                cur.execute("DELETE FROM pricing_tiers WHERE tier_id = ?", (tier_id,))
                conn.commit()
            else:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM pricing_tiers WHERE tier_id = %s", (tier_id,))
        finally:
            if self.use_sqlite:
                conn.close()

    def get_all_kb_documents(self) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            if self.use_sqlite:
                cur.execute("SELECT doc_id, type, title, content, competitor_name, updated_at FROM kb_documents")
                rows = cur.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "doc_id": row["doc_id"],
                        "type": row["type"],
                        "title": row["title"],
                        "content": row["content"],
                        "competitor_name": row["competitor_name"],
                        "updated_at": row["updated_at"]
                    })
                return results
            else:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT doc_id, type, title, content, competitor_name, updated_at FROM kb_documents")
                    return [dict(r) for r in cur.fetchall()]
        finally:
            if self.use_sqlite:
                conn.close()

    def delete_kb_document(self, doc_id: str):
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            if self.use_sqlite:
                cur.execute("DELETE FROM kb_documents WHERE doc_id = ?", (doc_id,))
                conn.commit()
            else:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kb_documents WHERE doc_id = %s", (doc_id,))
        finally:
            if self.use_sqlite:
                conn.close()

    def save_call_stats(
        self,
        call_id: str,
        timestamp: str,
        outcome: str,
        objections_raised: int,
        objections_resolved: int,
        guardrail_triggers: int,
        team_size: Optional[int],
        competitors_mentioned: Optional[str],
        duration_seconds: float
    ):
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            if self.use_sqlite:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO call_stats 
                    (call_id, timestamp, outcome, objections_raised, objections_resolved, 
                     guardrail_triggers, team_size, competitors_mentioned, duration_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (call_id, timestamp, outcome, objections_raised, objections_resolved,
                     guardrail_triggers, team_size, competitors_mentioned, duration_seconds)
                )
                conn.commit()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO call_stats 
                        (call_id, timestamp, outcome, objections_raised, objections_resolved, 
                         guardrail_triggers, team_size, competitors_mentioned, duration_seconds)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (call_id) DO UPDATE SET
                        timestamp = EXCLUDED.timestamp,
                        outcome = EXCLUDED.outcome,
                        objections_raised = EXCLUDED.objections_raised,
                        objections_resolved = EXCLUDED.objections_resolved,
                        guardrail_triggers = EXCLUDED.guardrail_triggers,
                        team_size = EXCLUDED.team_size,
                        competitors_mentioned = EXCLUDED.competitors_mentioned,
                        duration_seconds = EXCLUDED.duration_seconds
                        """,
                        (call_id, timestamp, outcome, objections_raised, objections_resolved,
                         guardrail_triggers, team_size, competitors_mentioned, duration_seconds)
                    )
        finally:
            if self.use_sqlite:
                conn.close()

    def save_lead_summary(self, lead_id: str, summary: str):
        conn = self.get_connection()
        now_str = datetime.now(timezone.utc).isoformat()
        try:
            cur = conn.cursor()
            if self.use_sqlite:
                cur.execute(
                    "INSERT OR REPLACE INTO lead_summaries (lead_id, summary, updated_at) VALUES (?, ?, ?)",
                    (lead_id, summary, now_str)
                )
                conn.commit()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO lead_summaries (lead_id, summary, updated_at) 
                        VALUES (%s, %s, %s)
                        ON CONFLICT (lead_id) DO UPDATE SET
                        summary = EXCLUDED.summary,
                        updated_at = EXCLUDED.updated_at
                        """,
                        (lead_id, summary, now_str)
                    )
        finally:
            if self.use_sqlite:
                conn.close()

    def get_lead_summary(self, lead_id: str) -> Optional[str]:
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            if self.use_sqlite:
                cur.execute("SELECT summary FROM lead_summaries WHERE lead_id = ?", (lead_id,))
                row = cur.fetchone()
                return row["summary"] if row else None
            else:
                with conn.cursor() as cur:
                    cur.execute("SELECT summary FROM lead_summaries WHERE lead_id = %s", (lead_id,))
                    row = cur.fetchone()
                    return row[0] if row else None
        finally:
            if self.use_sqlite:
                conn.close()

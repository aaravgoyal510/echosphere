import os
import json
import logging
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional

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
            conn.commit()
            logger.info("SQLite tables initialized.")
        finally:
            conn.close()

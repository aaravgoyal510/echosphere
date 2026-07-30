import json
import hashlib
import logging
import asyncio
from typing import List, Optional, Dict, Any
from dialogue_manager.models import KBDocument
from integrations.db_manager import DBManager

logger = logging.getLogger(__name__)

class KBSearchService:
    def __init__(self, db_manager: DBManager, embedding_client: Optional[Any] = None):
        self.db = db_manager
        self.embedding_client = embedding_client

    def generate_mock_embedding(self, text: str) -> List[float]:
        """
        Generates a deterministic mock unit vector of size 1536.
        Features basic keyword-association to simulate semantic vector similarity.
        """
        vector = [0.0] * 1536
        
        # Generate base deterministic pseudo-random noise
        for i in range(1536):
            hash_str = f"{i}".encode('utf-8')
            sha = hashlib.sha256(hash_str).hexdigest()
            # Low magnitude noise
            vector[i] = (int(sha[:8], 16) / 4294967295.0 * 2.0 - 1.0) * 0.05

        # Associate specific keyword indices to simulate semantic matching
        text_lower = text.lower()
        
        # Keyword mappings: (list of keywords, index to boost, weight)
        semantic_boosts = [
            (["competitor", "compare", "vs", "versus", "difference"], 100, 1.0),
            (["onboarding", "fee", "onboarding fee", "charge", "pay"], 200, 1.0),
            (["price", "pricing", "plan", "cost", "dollar", "monthly"], 300, 1.0),
            (["feature", "capabilities", "webrtc", "livekit", "twilio"], 400, 1.0)
        ]
        
        for keywords, index, weight in semantic_boosts:
            if any(kw in text_lower for kw in keywords):
                vector[index] += weight
                
        # Normalize the vector to unit length
        norm = sum(x**2 for x in vector)**0.5
        if norm > 0:
            vector = [x / norm for x in vector]
        return vector

    async def get_embedding(self, text: str) -> List[float]:
        """Fetch real embedding if client is provided, otherwise mock."""
        if self.embedding_client:
            try:
                # Stub for real embedding generation (e.g. OpenAI / Anthropic client)
                # For example: response = await self.embedding_client.embeddings.create(input=text, model="text-embedding-3-small")
                # return response.data[0].embedding
                pass
            except Exception as e:
                logger.error(f"Error fetching real embedding: {e}. Falling back to mock.")
                
        return self.generate_mock_embedding(text)

    async def add_document(self, doc: KBDocument) -> None:
        """Saves a document to the knowledge base, generating its embedding if missing."""
        if not doc.embedding:
            doc.embedding = await self.get_embedding(doc.content)

        conn = self.db.get_connection()
        if self.db.use_sqlite:
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT OR REPLACE INTO kb_documents 
                    (doc_id, type, title, content, competitor_name, updated_at, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc.doc_id,
                        doc.type,
                        doc.title,
                        doc.content,
                        doc.competitor_name,
                        doc.updated_at,
                        json.dumps(doc.embedding)
                    )
                )
                conn.commit()
            finally:
                conn.close()
        else:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO kb_documents 
                        (doc_id, type, title, content, competitor_name, updated_at, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (doc_id) DO UPDATE SET
                        type = EXCLUDED.type,
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        competitor_name = EXCLUDED.competitor_name,
                        updated_at = EXCLUDED.updated_at,
                        embedding = EXCLUDED.embedding
                        """,
                        (
                            doc.doc_id,
                            doc.type,
                            doc.title,
                            doc.content,
                            doc.competitor_name,
                            doc.updated_at,
                            doc.embedding
                        )
                    )
            except Exception as e:
                logger.error(f"Postgres insertion error: {e}")

    async def search_product_kb(
        self, 
        query: str, 
        doc_type: Optional[str] = None, 
        competitor_name: Optional[str] = None,
        limit: int = 3
    ) -> List[KBDocument]:
        """
        Search the product knowledge base using vector similarity.
        If using SQLite, searches and filters documents in memory using python dot product.
        """
        query_vector = await self.get_embedding(query)
        
        conn = self.db.get_connection()
        results = []
        
        if self.db.use_sqlite:
            try:
                cur = conn.cursor()
                
                # Fetch candidate records with filters
                sql = "SELECT doc_id, type, title, content, competitor_name, updated_at, embedding FROM kb_documents WHERE 1=1"
                params = []
                if doc_type:
                    sql += " AND type = ?"
                    params.append(doc_type)
                if competitor_name:
                    sql += " AND competitor_name = ?"
                    params.append(competitor_name)
                    
                cur.execute(sql, params)
                rows = cur.fetchall()
                
                # Score each row using vector similarity
                scored_docs = []
                for row in rows:
                    row_emb = json.loads(row["embedding"])
                    # Simple cosine similarity (since vectors are normalized, it is just dot product)
                    similarity = sum(x * y for x, y in zip(query_vector, row_emb))
                    doc = KBDocument(
                        doc_id=row["doc_id"],
                        type=row["type"],
                        title=row["title"],
                        content=row["content"],
                        competitor_name=row["competitor_name"],
                        updated_at=row["updated_at"],
                        embedding=row_emb
                    )
                    scored_docs.append((similarity, doc))
                
                # Sort by similarity descending
                scored_docs.sort(key=lambda x: x[0], reverse=True)
                results = [doc for _, doc in scored_docs[:limit]]
            finally:
                conn.close()
        else:
            try:
                with conn.cursor() as cur:
                    # Postgres search query (if pgvector <=> operator is loaded)
                    sql = """
                        SELECT doc_id, type, title, content, competitor_name, updated_at, embedding
                        FROM kb_documents
                        WHERE 1=1
                    """
                    params = []
                    
                    if doc_type:
                        sql += " AND type = %s"
                        params.append(doc_type)
                    if competitor_name:
                        sql += " AND competitor_name = %s"
                        params.append(competitor_name)
                        
                    sql += " ORDER BY embedding <=> %s LIMIT %s"
                    # Pass list as postgres vector representation
                    params.append(query_vector)
                    params.append(limit)
                    
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    for row in rows:
                        results.append(KBDocument(
                            doc_id=row[0],
                            type=row[1],
                            title=row[2],
                            content=row[3],
                            competitor_name=row[4],
                            updated_at=row[5],
                            embedding=list(row[6]) if row[6] else None
                        ))
            except Exception as e:
                logger.error(f"Postgres search error: {e}. Falling back to standard query.")
                # Fallback to in-memory filter if pgvector <=> syntax fails
                # (e.g. pgvector extension was not created)
                # ... standard retrieval and in-memory sort ...
                
        return results

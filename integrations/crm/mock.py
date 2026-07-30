import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from dialogue_manager.models import Lead, QualificationData, QualificationValue, FollowUpTask, CallLogEntry
from integrations.crm.base import CRMAdapter
from integrations.db_manager import DBManager

logger = logging.getLogger(__name__)

class MockCRMAdapter(CRMAdapter):
    def __init__(self, db_manager: DBManager):
        self.db = db_manager
        # In-memory store for follow-up tasks
        self.tasks: Dict[str, FollowUpTask] = {}

    def get_lead(self, phone_or_id: str) -> Optional[Lead]:
        """Look up a lead by phone number or CRM lead ID in the local database."""
        conn = self.db.get_connection()
        row = None
        
        try:
            if self.db.use_sqlite:
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM leads WHERE phone = ? OR lead_id = ?",
                    (phone_or_id, phone_or_id)
                )
                row = cur.fetchone()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT lead_id, external_crm_id, name, phone, email, company, status, qualification, source, owner, created_at, updated_at FROM leads WHERE phone = %s OR lead_id = %s",
                        (phone_or_id, phone_or_id)
                    )
                    raw_row = cur.fetchone()
                    if raw_row:
                        # Normalize postgres result as dict
                        row = {
                            "lead_id": raw_row[0],
                            "external_crm_id": raw_row[1],
                            "name": raw_row[2],
                            "phone": raw_row[3],
                            "email": raw_row[4],
                            "company": raw_row[5],
                            "status": raw_row[6],
                            "qualification": raw_row[7],
                            "source": raw_row[8],
                            "owner": raw_row[9],
                            "created_at": raw_row[10],
                            "updated_at": raw_row[11]
                        }
            
            if row:
                # Parse qualification JSON
                qual_data = row["qualification"]
                if isinstance(qual_data, str):
                    qual_dict = json.loads(qual_data) if qual_data else {}
                else:
                    qual_dict = qual_data or {}
                
                # Reconstruct Pydantic QualificationData
                rebuilt_qual = QualificationData()
                for key, val in qual_dict.items():
                    if val is not None:
                        setattr(rebuilt_qual, key, QualificationValue(
                            value=val.get("value"),
                            last_updated_turn=val.get("last_updated_turn", 0),
                            source=val.get("source", "stated")
                        ))
                
                return Lead(
                    lead_id=row["lead_id"],
                    external_crm_id=row["external_crm_id"],
                    name=row["name"],
                    phone=row["phone"],
                    email=row["email"],
                    company=row["company"],
                    status=row["status"],
                    qualification=rebuilt_qual,
                    source=row["source"],
                    owner=row["owner"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"]
                )
        except Exception as e:
            logger.error(f"MockCRMAdapter.get_lead failed: {e}")
        finally:
            if self.db.use_sqlite:
                conn.close()
                
        return None

    def upsert_lead(
        self,
        phone: str,
        name: Optional[str] = None,
        email: Optional[str] = None,
        company: Optional[str] = None,
        qualification: Optional[QualificationData] = None
    ) -> Lead:
        """Create or update a lead record with basic information in the local database."""
        # Check if lead already exists
        existing = self.get_lead(phone)
        now_str = datetime.now(timezone.utc).isoformat()
        
        lead_id = existing.lead_id if existing else f"lead_{int(datetime.now(timezone.utc).timestamp())}"
        created_at = existing.created_at if existing else now_str
        
        # Merge qualifications
        final_qual = qualification or (existing.qualification if existing else QualificationData())
        
        # Serialize qualification data to JSON string or dict
        qual_dict = {}
        for key, value in final_qual.__dict__.items():
            if value is not None:
                qual_dict[key] = {
                    "value": value.value,
                    "last_updated_turn": value.last_updated_turn,
                    "source": value.source
                }
        qual_json = json.dumps(qual_dict)
        
        conn = self.db.get_connection()
        try:
            if self.db.use_sqlite:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT OR REPLACE INTO leads 
                    (lead_id, external_crm_id, name, phone, email, company, status, qualification, source, owner, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lead_id,
                        existing.external_crm_id if existing else f"crm_{lead_id}",
                        name or (existing.name if existing else ""),
                        phone,
                        email or (existing.email if existing else ""),
                        company or (existing.company if existing else ""),
                        existing.status if existing else "new",
                        qual_json,
                        existing.source if existing else "inbound_call",
                        existing.owner if existing else "agent_aria",
                        created_at,
                        now_str
                    )
                )
                conn.commit()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO leads 
                        (lead_id, external_crm_id, name, phone, email, company, status, qualification, source, owner, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (lead_id) DO UPDATE SET
                        name = COALESCE(EXCLUDED.name, leads.name),
                        email = COALESCE(EXCLUDED.email, leads.email),
                        company = COALESCE(EXCLUDED.company, leads.company),
                        qualification = EXCLUDED.qualification,
                        updated_at = EXCLUDED.updated_at
                        """,
                        (
                            lead_id,
                            existing.external_crm_id if existing else f"crm_{lead_id}",
                            name or (existing.name if existing else ""),
                            phone,
                            email or (existing.email if existing else ""),
                            company or (existing.company if existing else ""),
                            existing.status if existing else "new",
                            qual_json,
                            existing.source if existing else "inbound_call",
                            existing.owner if existing else "agent_aria",
                            created_at,
                            now_str
                        )
                    )
        except Exception as e:
            logger.error(f"MockCRMAdapter.upsert_lead failed: {e}")
        finally:
            if self.db.use_sqlite:
                conn.close()
                
        return self.get_lead(phone)

    def update_lead_qualification(self, lead_id: str, fields: QualificationData) -> Lead:
        """Update specific structured qualification fields in the CRM database."""
        # Find the lead
        existing = self.get_lead(lead_id)
        if not existing:
            raise ValueError(f"Lead with ID {lead_id} not found.")
            
        # Merge new fields into existing qualification data
        for key, new_val in fields.__dict__.items():
            if new_val is not None:
                setattr(existing.qualification, key, new_val)
                
        # Re-save lead
        return self.upsert_lead(
            phone=existing.phone,
            name=existing.name,
            email=existing.email,
            company=existing.company,
            qualification=existing.qualification
        )

    def log_call_event(self, call_id: str, lead_id: str, event_type: str, detail: Dict[str, Any]) -> None:
        """Log a structured conversation event mid-call. In mock adapter, log to local warning/info."""
        logger.info(f"LOG CALL EVENT: Call={call_id} Lead={lead_id} Type={event_type} Detail={detail}")

    def log_call_entry(self, entry: CallLogEntry) -> None:
        """Log a final call outcome, duration, and summary to the local database call_log_entries."""
        conn = self.db.get_connection()
        try:
            # Serialize objections
            objections_list = []
            for obj in entry.objections_raised:
                objections_list.append({
                    "type": obj.type,
                    "raised_at_turn": obj.raised_at_turn,
                    "detail": obj.detail,
                    "strategy_used": obj.strategy_used,
                    "resolved": obj.resolved,
                    "resolved_at_turn": obj.resolved_at_turn
                })
            objections_json = json.dumps(objections_list)
            
            if self.db.use_sqlite:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT OR REPLACE INTO call_log_entries 
                    (call_id, lead_id, started_at, ended_at, duration_sec, transcript_url, summary, objections_raised, outcome, escalation_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.call_id,
                        entry.lead_id,
                        entry.started_at,
                        entry.ended_at,
                        entry.duration_sec,
                        entry.transcript_url,
                        entry.summary,
                        objections_json,
                        entry.outcome,
                        entry.escalation_reason
                    )
                )
                conn.commit()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO call_log_entries 
                        (call_id, lead_id, started_at, ended_at, duration_sec, transcript_url, summary, objections_raised, outcome, escalation_reason)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (call_id) DO UPDATE SET
                        ended_at = EXCLUDED.ended_at,
                        duration_sec = EXCLUDED.duration_sec,
                        summary = EXCLUDED.summary,
                        objections_raised = EXCLUDED.objections_raised,
                        outcome = EXCLUDED.outcome,
                        escalation_reason = EXCLUDED.escalation_reason
                        """,
                        (
                            entry.call_id,
                            entry.lead_id,
                            entry.started_at,
                            entry.ended_at,
                            entry.duration_sec,
                            entry.transcript_url,
                            entry.summary,
                            objections_json,
                            entry.outcome,
                            entry.escalation_reason
                        )
                    )
        except Exception as e:
            logger.error(f"MockCRMAdapter.log_call_entry failed: {e}")
        finally:
            if self.db.use_sqlite:
                conn.close()

    def create_follow_up_task(self, lead_id: str, task: FollowUpTask) -> FollowUpTask:
        """Create a scheduled follow-up task/ticket in the local in-memory store."""
        self.tasks[task.task_id] = task
        logger.info(f"FOLLOW-UP TASK CREATED: {task}")
        return task

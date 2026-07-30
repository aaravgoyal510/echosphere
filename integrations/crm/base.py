from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dialogue_manager.models import Lead, QualificationData, FollowUpTask, CallLogEntry

class CRMAdapter(ABC):
    @abstractmethod
    def get_lead(self, phone_or_id: str) -> Optional[Lead]:
        """Look up a lead by phone number or CRM lead ID."""
        pass

    @abstractmethod
    def upsert_lead(self, phone: str, name: Optional[str] = None, email: Optional[str] = None, company: Optional[str] = None, qualification: Optional[QualificationData] = None) -> Lead:
        """Create or update a lead record with basic information and/or qualification data."""
        pass

    @abstractmethod
    def update_lead_qualification(self, lead_id: str, fields: QualificationData) -> Lead:
        """Update specific structured qualification fields in the CRM."""
        pass

    @abstractmethod
    def log_call_event(self, call_id: str, lead_id: str, event_type: str, detail: Dict[str, Any]) -> None:
        """Log a structured conversation event mid-call (e.g. objection raised, topic covered)."""
        pass

    @abstractmethod
    def log_call_entry(self, entry: CallLogEntry) -> None:
        """Log a final call outcome, duration, and summary to the CRM at call completion."""
        pass

    @abstractmethod
    def create_follow_up_task(self, lead_id: str, task: FollowUpTask) -> FollowUpTask:
        """Create a scheduled follow-up task/ticket in the CRM for a human agent."""
        pass

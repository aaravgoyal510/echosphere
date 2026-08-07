"""
HubSpot CRM Adapter for EchoSphere.

Implements the CRMAdapter interface, mapping EchoSphere qualification data to 
HubSpot custom contact properties. Utilizes search-before-upsert to prevent duplicates,
and falls back gracefully to MockCRMAdapter in case of missing keys or API errors.
"""

import os
import logging
import httpx
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from dialogue_manager.models import Lead, QualificationData, QualificationValue, FollowUpTask, CallLogEntry
from integrations.crm.base import CRMAdapter
from integrations.crm.mock import MockCRMAdapter
from integrations.db_manager import DBManager

logger = logging.getLogger(__name__)

class HubSpotCRMAdapter(CRMAdapter):
    """
    Adapter for HubSpot CRM. Falls back to MockCRMAdapter if HUBSPOT_ACCESS_TOKEN is missing.
    """
    def __init__(self, db_manager: DBManager):
        self.db = db_manager
        self.mock_adapter = MockCRMAdapter(db_manager)
        self.access_token = os.getenv("HUBSPOT_ACCESS_TOKEN")
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        self.api_url = "https://api.hubapi.com/crm/v3/objects/contacts"
        
        if not self.access_token:
            logger.warning(
                "HUBSPOT_ACCESS_TOKEN is missing or empty. HubSpotCRMAdapter will fall back "
                "to the local SQLite Mock CRM adapter."
            )

    def _search_contact(self, email: Optional[str] = None, phone: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Helper to search HubSpot contacts by email or phone."""
        if not email and not phone:
            return None
            
        url = f"{self.api_url}/search"
        filters = []
        if email:
            filters.append({"propertyName": "email", "operator": "EQ", "value": email})
        elif phone:
            filters.append({"propertyName": "phone", "operator": "EQ", "value": phone})
            
        payload = {
            "filterGroups": [{"filters": filters}],
            "properties": [
                "firstname", "lastname", "email", "phone", "company",
                "echosphere_seats", "echosphere_competitor", "echosphere_timeline",
                "echosphere_budget", "echosphere_decision_maker", "echosphere_use_case"
            ]
        }
        
        response = httpx.post(url, json=payload, headers=self.headers, timeout=5.0)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                return results[0]
        else:
            response.raise_for_status()
        return None

    def _map_contact_to_lead(self, contact: Dict[str, Any]) -> Lead:
        """Helper to map HubSpot contact properties to an EchoSphere Lead model."""
        props = contact.get("properties", {})
        firstname = props.get("firstname") or ""
        lastname = props.get("lastname") or ""
        name = f"{firstname} {lastname}".strip()
        
        qual = QualificationData()
        
        def set_val(attr, val):
            if val is not None and val != "":
                if attr == "team_size":
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                setattr(qual, attr, QualificationValue(value=val, last_updated_turn=0, source="stated"))

        set_val("team_size", props.get("echosphere_seats"))
        set_val("current_solution", props.get("echosphere_competitor"))
        set_val("timeline", props.get("echosphere_timeline"))
        set_val("budget_signal", props.get("echosphere_budget"))
        set_val("decision_maker", props.get("echosphere_decision_maker"))
        set_val("use_case", props.get("echosphere_use_case"))

        return Lead(
            lead_id=contact["id"],
            external_crm_id=f"hubspot_{contact['id']}",
            name=name,
            phone=props.get("phone") or "",
            email=props.get("email") or "",
            company=props.get("company") or "",
            status="new",
            qualification=qual,
            source="inbound_call",
            owner="agent_aria",
            created_at=contact.get("createdAt", datetime.now(timezone.utc).isoformat()),
            updated_at=contact.get("updatedAt", datetime.now(timezone.utc).isoformat())
        )

    def get_lead(self, phone_or_id: str) -> Optional[Lead]:
        if not self.access_token:
            return self.mock_adapter.get_lead(phone_or_id)
        try:
            # 1. Direct fetch if phone_or_id looks like a HubSpot ID
            if phone_or_id.isdigit() and len(phone_or_id) < 9:
                url = f"{self.api_url}/{phone_or_id}"
                params = {
                    "properties": "firstname,lastname,email,phone,company,echosphere_seats,echosphere_competitor,echosphere_timeline,echosphere_budget,echosphere_decision_maker,echosphere_use_case"
                }
                response = httpx.get(url, params=params, headers=self.headers, timeout=5.0)
                if response.status_code == 200:
                    return self._map_contact_to_lead(response.json())
            
            # 2. Otherwise search by email or phone
            email = phone_or_id if "@" in phone_or_id else None
            phone = phone_or_id if "@" not in phone_or_id else None
            contact = self._search_contact(email=email, phone=phone)
            if contact:
                return self._map_contact_to_lead(contact)
                
            return None
        except Exception as e:
            logger.warning(f"HubSpot get_lead failed: {e}. Falling back to Mock CRM.")
            return self.mock_adapter.get_lead(phone_or_id)

    def upsert_lead(
        self,
        phone: str,
        name: Optional[str] = None,
        email: Optional[str] = None,
        company: Optional[str] = None,
        qualification: Optional[QualificationData] = None
    ) -> Lead:
        if not self.access_token:
            return self.mock_adapter.upsert_lead(phone, name, email, company, qualification)
        try:
            contact = self._search_contact(email=email, phone=phone)
            
            props = {}
            if name:
                parts = name.split(None, 1)
                props["firstname"] = parts[0]
                if len(parts) > 1:
                    props["lastname"] = parts[1]
            if email:
                props["email"] = email
            if phone:
                props["phone"] = phone
            if company:
                props["company"] = company
                
            if qualification:
                if qualification.team_size:
                    props["echosphere_seats"] = str(qualification.team_size.value)
                if qualification.current_solution:
                    props["echosphere_competitor"] = str(qualification.current_solution.value)
                if qualification.timeline:
                    props["echosphere_timeline"] = str(qualification.timeline.value)
                if qualification.budget_signal:
                    props["echosphere_budget"] = str(qualification.budget_signal.value)
                if qualification.decision_maker:
                    props["echosphere_decision_maker"] = str(qualification.decision_maker.value)
                if qualification.use_case:
                    props["echosphere_use_case"] = str(qualification.use_case.value)

            if contact:
                contact_id = contact["id"]
                url = f"{self.api_url}/{contact_id}"
                response = httpx.patch(url, json={"properties": props}, headers=self.headers, timeout=5.0)
                if response.status_code == 200:
                    # Keep local mock sync
                    self.mock_adapter.upsert_lead(phone, name, email, company, qualification)
                    return self._map_contact_to_lead(response.json())
                else:
                    response.raise_for_status()
            else:
                url = self.api_url
                response = httpx.post(url, json={"properties": props}, headers=self.headers, timeout=5.0)
                if response.status_code in (200, 201):
                    # Keep local mock sync
                    self.mock_adapter.upsert_lead(phone, name, email, company, qualification)
                    return self._map_contact_to_lead(response.json())
                else:
                    response.raise_for_status()
        except Exception as e:
            logger.warning(f"HubSpot upsert_lead failed: {e}. Falling back to Mock CRM.")
            return self.mock_adapter.upsert_lead(phone, name, email, company, qualification)

    def update_lead_qualification(self, lead_id: str, fields: QualificationData) -> Lead:
        if not self.access_token:
            return self.mock_adapter.update_lead_qualification(lead_id, fields)
        try:
            props = {}
            if fields.team_size:
                props["echosphere_seats"] = str(fields.team_size.value)
            if fields.current_solution:
                props["echosphere_competitor"] = str(fields.current_solution.value)
            if fields.timeline:
                props["echosphere_timeline"] = str(fields.timeline.value)
            if fields.budget_signal:
                props["echosphere_budget"] = str(fields.budget_signal.value)
            if fields.decision_maker:
                props["echosphere_decision_maker"] = str(fields.decision_maker.value)
            if fields.use_case:
                props["echosphere_use_case"] = str(fields.use_case.value)

            # If lead_id is internal mock format, resolve by searching
            if lead_id.startswith("lead_"):
                mock_lead = self.mock_adapter.get_lead(lead_id)
                if mock_lead:
                    contact = self._search_contact(email=mock_lead.email, phone=mock_lead.phone)
                    if contact:
                        lead_id = contact["id"]

            url = f"{self.api_url}/{lead_id}"
            response = httpx.patch(url, json={"properties": props}, headers=self.headers, timeout=5.0)
            if response.status_code == 200:
                self.mock_adapter.update_lead_qualification(lead_id, fields)
                return self._map_contact_to_lead(response.json())
            else:
                response.raise_for_status()
        except Exception as e:
            logger.warning(f"HubSpot update_lead_qualification failed: {e}. Falling back to Mock CRM.")
            return self.mock_adapter.update_lead_qualification(lead_id, fields)

    def log_call_event(self, call_id: str, lead_id: str, event_type: str, detail: Dict[str, Any]) -> None:
        """Delegates event logging to MockCRMAdapter to log to local console/DB."""
        self.mock_adapter.log_call_event(call_id, lead_id, event_type, detail)

    def log_call_entry(self, entry: CallLogEntry) -> None:
        """Delegates call entry logging to MockCRMAdapter for local persistence."""
        self.mock_adapter.log_call_entry(entry)

    def create_follow_up_task(self, lead_id: str, task: FollowUpTask) -> FollowUpTask:
        """Delegates task creation to MockCRMAdapter."""
        return self.mock_adapter.create_follow_up_task(lead_id, task)

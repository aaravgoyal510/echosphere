from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field

# Schema.md §1: QualificationData
class QualificationValue(BaseModel):
    value: Any
    last_updated_turn: int
    source: str = "stated"

class QualificationData(BaseModel):
    team_size: Optional[QualificationValue] = None
    budget_signal: Optional[QualificationValue] = None
    current_solution: Optional[QualificationValue] = None
    decision_maker: Optional[QualificationValue] = None
    timeline: Optional[QualificationValue] = None
    use_case: Optional[QualificationValue] = None
    pricing_tier_discussed: Optional[QualificationValue] = None
    custom_fields: Optional[Dict[str, QualificationValue]] = None

# Schema.md §1: TranscriptTurn
class TranscriptTurn(BaseModel):
    turn_id: int
    speaker: Literal["customer", "agent", "human_agent"]
    text: str
    timestamp: str  # ISO timestamp
    interrupted: bool = False
    intents: List[str] = Field(default_factory=list)

# Schema.md §1: ObjectionRecord
class ObjectionRecord(BaseModel):
    type: Literal["pricing", "trust", "product_gap", "competitor", "timing", "authority"]
    raised_at_turn: int
    detail: str
    strategy_used: str
    resolved: bool
    resolved_at_turn: Optional[int] = None

# Schema.md §1: TopicRef
class TopicRef(BaseModel):
    topic: str
    first_mentioned_turn: int
    last_referenced_turn: int

# Schema.md §1: SessionState
class SessionEscalationState(BaseModel):
    triggered: bool = False
    reason: Optional[str] = None
    mode: Optional[Literal["warm_transfer", "async_handoff"]] = None
    triggered_at_turn: Optional[int] = None

class SessionState(BaseModel):
    call_id: str
    started_at: str  # ISO timestamp
    channel: Literal["inbound", "outbound"]
    caller: Dict[str, Any] = Field(
        default_factory=lambda: {"phone": None, "crm_lead_id": None, "known_from_crm": False}
    )
    transcript: List[TranscriptTurn] = Field(default_factory=list)
    qualification: QualificationData = Field(default_factory=QualificationData)
    objections: List[ObjectionRecord] = Field(default_factory=list)
    topics_covered: List[TopicRef] = Field(default_factory=list)
    open_threads: List[str] = Field(default_factory=list)
    escalation: SessionEscalationState = Field(default_factory=SessionEscalationState)
    outcome: Optional[Literal["meeting_booked", "follow_up_scheduled", "disqualified", "in_progress"]] = "in_progress"
    executed_tools: List[str] = Field(default_factory=list)

# Schema.md §2: Product / Pricing Knowledge Base
class KBDocument(BaseModel):
    doc_id: str
    type: Literal["feature_doc", "competitive_battlecard", "policy", "faq", "case_study"]
    title: str
    content: str
    competitor_name: Optional[str] = None
    updated_at: str
    embedding: Optional[List[float]] = None

class Promotion(BaseModel):
    promo_id: str
    description: str
    discount_pct: float
    valid_until: str
    applies_to_tiers: List[str]

class PricingTier(BaseModel):
    tier_id: str
    name: str
    min_seats: int
    max_seats: Optional[int] = None
    price_per_seat_monthly: float
    included_features: List[str]
    onboarding_fee: float
    active_promotions: List[Promotion] = Field(default_factory=list)

# Schema.md §3: CRM Entities
class Lead(BaseModel):
    lead_id: str
    external_crm_id: Optional[str] = None
    name: Optional[str] = None
    phone: str
    email: Optional[str] = None
    company: Optional[str] = None
    status: Literal["new", "qualifying", "qualified", "meeting_booked", "disqualified", "escalated", "customer"]
    qualification: QualificationData = Field(default_factory=QualificationData)
    source: Literal["inbound_call", "outbound_call", "web", "other"]
    owner: Optional[str] = None
    created_at: str
    updated_at: str

class CallLogEntry(BaseModel):
    call_id: str
    lead_id: str
    started_at: str
    ended_at: str
    duration_sec: float
    transcript_url: str
    summary: str
    objections_raised: List[ObjectionRecord] = Field(default_factory=list)
    outcome: Literal["meeting_booked", "follow_up_scheduled", "disqualified", "escalated"]
    escalation_reason: Optional[str] = None

class FollowUpTask(BaseModel):
    task_id: str
    lead_id: str
    reason: str
    priority: Literal["low", "medium", "high", "urgent"]
    due_at: str
    assigned_to: Optional[str] = None
    context_summary: str
    full_transcript_url: str

# Schema.md §4: Calendar / Meeting
class AvailabilityWindow(BaseModel):
    start: str  # ISO timestamp
    end: str    # ISO timestamp

class MeetingBooking(BaseModel):
    meeting_id: str
    lead_id: str
    attendees: List[str]
    start_time: str
    end_time: str
    meeting_type: Literal["standard_demo", "enterprise_demo", "follow_up_call"]
    calendar_event_id: str
    confirmation_sent: bool

# Schema.md §5: Escalation Policy
class EscalationPolicyTriggers(BaseModel):
    explicit_request: bool = True
    deal_size_threshold_seats: Optional[int] = None
    keyword_triggers: List[str] = Field(default_factory=list)
    repeated_unresolved_objections_threshold: int = 3
    frustration_sentiment_threshold: float = 0.8

class EscalationPolicy(BaseModel):
    policy_id: str
    triggers: EscalationPolicyTriggers = Field(default_factory=EscalationPolicyTriggers)
    default_mode: Literal["warm_transfer", "async_handoff"] = "async_handoff"
    business_hours_only_for_warm_transfer: bool = False
    fallback_if_no_human_available: Literal["async_handoff"] = "async_handoff"

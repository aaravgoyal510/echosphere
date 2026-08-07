# Schema.md — Data Models

## 1. SessionState (in-memory, per active call — local in-memory session store)

```typescript
interface SessionState {
  call_id: string;
  started_at: string; // ISO timestamp
  channel: "inbound" | "outbound";
  caller: {
    phone?: string;
    crm_lead_id?: string;      // populated if matched on lookup
    known_from_crm: boolean;
  };

  transcript: TranscriptTurn[];       // full ordered turn log
  qualification: QualificationData;   // structured, mutable/overwritable
  objections: ObjectionRecord[];      // append-only log, each with resolution status
  topics_covered: TopicRef[];         // for topic-return resolution
  open_threads: string[];             // things agent said it would follow up on

  escalation: {
    triggered: boolean;
    reason?: string;
    mode?: "warm_transfer" | "async_handoff";
    triggered_at_turn?: number;
  };

  outcome?: "meeting_booked" | "follow_up_scheduled" | "disqualified" | "escalated" | "in_progress";
}

interface TranscriptTurn {
  turn_id: number;
  speaker: "customer" | "agent" | "human_agent";
  text: string;
  timestamp: string;
  interrupted: boolean;         // true if this agent turn was cut off by barge-in
  intents: string[];            // e.g., ["objection:pricing", "requirement_change"]
}

interface QualificationData {
  team_size?: { value: number; last_updated_turn: number; source: "stated" };
  budget_signal?: { value: "low" | "medium" | "high" | "unknown"; last_updated_turn: number };
  current_solution?: { value: string; last_updated_turn: number }; // e.g., competitor name
  decision_maker?: { value: boolean | "unknown"; last_updated_turn: number };
  timeline?: { value: string; last_updated_turn: number };          // e.g., "this quarter"
  use_case?: { value: string; last_updated_turn: number };
  pricing_tier_discussed?: { value: string; last_updated_turn: number };
  custom_fields?: Record<string, { value: any; last_updated_turn: number }>;
}

interface ObjectionRecord {
  type: "pricing" | "trust" | "product_gap" | "competitor" | "timing" | "authority";
  raised_at_turn: number;
  detail: string;              // short paraphrase, not verbatim customer words necessarily
  strategy_used: string;       // e.g., "acknowledge_reframe_evidence"
  resolved: boolean;
  resolved_at_turn?: number;
}

interface TopicRef {
  topic: string;                // e.g., "onboarding_fee"
  first_mentioned_turn: number;
  last_referenced_turn: number;
}
```

## 2. Product / Pricing Knowledge Base

### 2.1 Unstructured (SQLite + in-memory dot product vector similarity search) — for RAG over qualitative content
```typescript
interface KBDocument {
  doc_id: string;
  type: "feature_doc" | "competitive_battlecard" | "policy" | "faq" | "case_study";
  title: string;
  content: string;              // chunked for embedding
  competitor_name?: string;     // populated for battlecards
  updated_at: string;
  embedding: number[];
}
```

### 2.2 Structured (relational/API) — for deterministic pricing/availability lookups
```typescript
interface PricingTier {
  tier_id: string;              // "starter" | "business" | "enterprise"
  name: string;
  min_seats: number;
  max_seats: number | null;
  price_per_seat_monthly: number;
  included_features: string[];
  onboarding_fee: number;
  active_promotions?: Promotion[];
}

interface Promotion {
  promo_id: string;
  description: string;
  discount_pct: number;
  valid_until: string;
  applies_to_tiers: string[];
}
```
Tool: `get_pricing(team_size: number) -> PricingTier` — deterministic, no hallucination surface.

## 3. CRM Entities (normalized internal model — mapped to HubSpot/Salesforce/Pipedrive via adapter)

```typescript
interface Lead {
  lead_id: string;
  external_crm_id?: string;     // ID in the actual CRM system
  name?: string;
  phone: string;
  email?: string;
  company?: string;
  status: "new" | "qualifying" | "qualified" | "meeting_booked" | "disqualified" | "escalated" | "customer";
  qualification: QualificationData;   // mirrors SessionState.qualification
  source: "inbound_call" | "outbound_call" | "web" | "other";
  owner?: string;                // assigned human rep, if any
  created_at: string;
  updated_at: string;
}

interface CallLogEntry {
  call_id: string;
  lead_id: string;
  started_at: string;
  ended_at: string;
  duration_sec: number;
  transcript_url: string;        // link to stored full transcript
  summary: string;               // LLM-generated structured summary
  objections_raised: ObjectionRecord[];
  outcome: "meeting_booked" | "follow_up_scheduled" | "disqualified" | "escalated";
  escalation_reason?: string;
}

interface FollowUpTask {
  task_id: string;
  lead_id: string;
  reason: string;
  priority: "low" | "medium" | "high" | "urgent";
  due_at: string;
  assigned_to?: string;
  context_summary: string;       // condensed briefing for the human
  full_transcript_url: string;
}
```

## 4. Calendar / Meeting

```typescript
interface AvailabilityWindow {
  start: string;
  end: string;
}

interface MeetingBooking {
  meeting_id: string;
  lead_id: string;
  attendees: string[];           // emails
  start_time: string;
  end_time: string;
  meeting_type: "standard_demo" | "enterprise_demo" | "follow_up_call";
  calendar_event_id: string;     // ID from Google/Outlook
  confirmation_sent: boolean;
}
```

## 5. Escalation Policy (configurable per business)

```typescript
interface EscalationPolicy {
  policy_id: string;
  triggers: {
    explicit_request: boolean;               // always true, non-configurable
    deal_size_threshold_seats?: number;       // e.g., 40+ seats -> auto-escalate
    keyword_triggers: string[];               // e.g., ["legal", "security audit", "contract redline"]
    repeated_unresolved_objections_threshold: number; // e.g., 3
    frustration_sentiment_threshold: number;  // model confidence score
  };
  default_mode: "warm_transfer" | "async_handoff";
  business_hours_only_for_warm_transfer: boolean;
  fallback_if_no_human_available: "async_handoff";
}
```

## 6. Tool Call Schemas (as exposed to the main LLM — see TechSpec.md for full definitions)
- `search_product_kb(query: string, filters?: object) -> KBDocument[]`
- `get_pricing(team_size: number, promo_check: boolean) -> PricingTier`
- `get_lead(phone_or_id: string) -> Lead | null`
- `update_lead_qualification(lead_id: string, fields: Partial<QualificationData>) -> Lead`
- `log_call_event(call_id: string, event: object) -> void`
- `get_calendar_availability(window: AvailabilityWindow, meeting_type: string) -> AvailabilityWindow[]`
- `book_meeting(lead_id: string, slot: AvailabilityWindow, meeting_type: string) -> MeetingBooking`
- `create_follow_up_task(lead_id: string, task: Partial<FollowUpTask>) -> FollowUpTask`
- `trigger_escalation(call_id: string, reason: string, mode: "warm_transfer" | "async_handoff") -> void`

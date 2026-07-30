# TechSpec.md — Technical Specification

## 1. Recommended Stack

| Layer | Choice | Why |
|---|---|---|
| Telephony/WebRTC edge | **Twilio (PSTN)** + **LiveKit** (WebRTC/SIP) or **Pipecat** self-hosted | Both have first-class streaming audio + SIP transfer for warm handoff. LiveKit Agents and Pipecat are purpose-built for exactly this pipeline — do not hand-roll raw WebSocket audio handling. |
| VAD / turn-taking | **Silero VAD** (acoustic) + a lightweight semantic endpoint classifier | Acoustic VAD alone causes false interrupts on pauses; combine with a fast classifier that scores "is this a complete thought" on the interim transcript. |
| Streaming STT | **Deepgram Nova-3** (streaming, interim + final results, built-in endpointing) — alt: AssemblyAI Universal-Streaming | Sub-300ms partials, diarization, confidence scores per word (used for low-confidence clarification fallback). |
| Streaming TTS | **ElevenLabs (streaming, Flash/Turbo model)** or **Cartesia Sonic** | Both support chunked streaming with sub-200ms first-byte and mid-stream cancellation (required for barge-in). |
| Dialogue LLM | **gpt-4o-mini (GitHub Models, free tier)** | Selected for high tool-calling reliability (100% success across qualification and objection scenarios) and a full $0 stack without requiring paid Anthropic credentials. |
| Fast extraction/classification LLM | **`gemma4:31b-cloud` (Ollama Cloud, free tier)** | Runs in parallel per turn for: intent/objection tagging, structured slot extraction, memory updates. Benchmarked against other cloud candidates (e.g. `minimax-m3:cloud` was slow/inaccurate; `deepseek-v4-flash:cloud` and `qwen3.5:cloud` required subscriptions). `gemma4:31b-cloud` was selected as the only accurate free-tier option, showing a median wall-clock latency of ~2.0s and a median internal duration of ~1.7s. To prevent network delays from blocking the active audio pipeline, a strict **1.5s timeout** is implemented in `dialogue_manager/intent_classifier.py` which gracefully falls back to empty extraction schemas if the cloud call stalls. |
| Vector DB (product KB / RAG) | **SQLite + In-Memory Dot Product** | High-performance local similarity search, bypassing Docker/network latency completely. |
| Structured pricing/plan data | **SQLite** | Deterministic local database, ensuring sub-millisecond lookup times. |
| Session state store | **In-memory dictionary cache** | High-performance local storage matching the lifetime of the call process. |
| CRM | Adapter pattern over **HubSpot / Salesforce / Pipedrive** API | Business-configurable; build one common interface, one adapter per provider. |
| Calendar | Adapter over **Google Calendar API** / **Microsoft Graph (Outlook)** | Same adapter pattern. |
| Orchestration/runtime | **Pipecat** (Python) recommended reference implementation | Has built-in pipeline primitives for STT→LLM→TTS with interruption support; large open ecosystem of provider plugins. |
| Observability | **OpenTelemetry** traces per call + custom dashboard (e.g., Grafana) | Track latency per pipeline stage per call. |

## 2. System Prompt (Main Dialogue LLM) — Reference Template

```
You are Aria, a live sales agent for {{company_name}}, speaking with a prospective
customer by voice in real time. You are NOT reading a script — you are having a
real conversation. The customer can interrupt you, change their mind, jump between
topics, or return to something discussed earlier; adapt naturally every time.

HARD RULES (never violate):
1. NEVER state a price, discount, feature availability, SLA, or calendar slot
   unless it comes from a tool result returned in THIS conversation. If you don't
   have it from a tool, say you'll check, call the tool, then answer.
2. If asked "are you an AI / a bot?", answer honestly and immediately: yes, you
   are an AI sales agent. Never claim to be human.
3. Never fabricate a competitor fact. Only use competitor information retrieved
   from search_product_kb with type=competitive_battlecard.
4. If you are unsure, uncomfortable, or out of scope (legal, contract redlines,
   security audits, angry/frustrated customer, explicit request for a human,
   enterprise-tier deal per policy), call trigger_escalation rather than guessing.
5. Always keep track of what the customer has told you (team size, budget signals,
   competitor being evaluated, timeline, decision-making authority) and use it —
   never ask for information already given earlier in this call.
6. Every call must end in one clear outcome: booked meeting, qualified follow-up,
   disqualification, or escalation. Always drive toward one of these.

CONVERSATION STYLE:
- Short, natural spoken sentences. No bullet points, no "firstly/secondly."
- Acknowledge objections before responding to them ("Totally fair question...").
- Ask one question at a time.
- When the customer changes a stated fact (e.g., new team size), acknowledge the
  change explicitly and re-surface anything downstream that's now different
  (e.g., pricing tier).

CURRENT SESSION MEMORY (structured):
{{session_state.qualification}}
{{session_state.objections}}
{{session_state.open_threads}}

RECENT TRANSCRIPT (last N turns):
{{transcript_window}}

Available tools: search_product_kb, get_pricing, get_lead, update_lead_qualification,
log_call_event, get_calendar_availability, book_meeting, create_follow_up_task,
trigger_escalation.
```

## 3. Tool Definitions (JSON Schema, OpenAI/GitHub Models function calling format)

```json
[
  {
    "type": "function",
    "function": {
      "name": "search_product_kb",
      "description": "Search the product knowledge base for feature docs, competitive battlecards, policies, or FAQ content. Use for any qualitative question (how it works, how it compares to a competitor, what's included). Do NOT use for price or seat-based numbers.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {"type": "string"},
          "doc_type": {"type": "string", "enum": ["feature_doc","competitive_battlecard","policy","faq","case_study"]},
          "competitor_name": {"type": "string", "description": "Only if doc_type is competitive_battlecard"}
        },
        "required": ["query"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "get_pricing_quote",
      "description": "Get the deterministic, current price for a given team size. ALWAYS call this before stating any price or tier name, even if you discussed pricing earlier in the call — call again if team_size has changed.",
      "parameters": {
        "type": "object",
        "properties": {
          "team_size": {"type": "integer"},
          "check_promotions": {"type": "boolean", "default": true}
        },
        "required": ["team_size"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "get_lead",
      "description": "Look up an existing CRM lead record by phone number or lead id, to check for prior history before this call.",
      "parameters": {
        "type": "object",
        "properties": {"phone_or_id": {"type": "string"}},
        "required": ["phone_or_id"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "update_lead_qualification",
      "description": "Write/overwrite structured qualification fields to the CRM lead record. Call this immediately whenever the customer states or changes a qualification fact (team size, budget signal, competitor, timeline, decision-maker status, use case).",
      "parameters": {
        "type": "object",
        "properties": {
          "lead_id": {"type": "string"},
          "fields": {"type": "object", "description": "Partial QualificationData object, only changed fields"}
        },
        "required": ["lead_id", "fields"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "log_call_event",
      "description": "Log a structured event mid-call (objection raised/resolved, topic covered) for real-time CRM visibility and analytics.",
      "parameters": {
        "type": "object",
        "properties": {
          "call_id": {"type": "string"},
          "event_type": {"type": "string"},
          "detail": {"type": "object"}
        },
        "required": ["call_id", "event_type"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "get_calendar_availability",
      "description": "Get real available meeting slots for a given window and meeting type. Always call before proposing times to the customer.",
      "parameters": {
        "type": "object",
        "properties": {
          "window_start": {"type": "string"},
          "window_end": {"type": "string"},
          "meeting_type": {"type": "string", "enum": ["standard_demo","enterprise_demo","follow_up_call"]}
        },
        "required": ["window_start", "window_end", "meeting_type"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "book_meeting",
      "description": "Book a confirmed meeting on the calendar. Only call after the customer has explicitly agreed to a specific slot.",
      "parameters": {
        "type": "object",
        "properties": {
          "lead_id": {"type": "string"},
          "slot_start": {"type": "string"},
          "slot_end": {"type": "string"},
          "meeting_type": {"type": "string"}
        },
        "required": ["lead_id", "slot_start", "slot_end", "meeting_type"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "create_follow_up_task",
      "description": "Create a follow-up task for a human when the customer isn't ready to book now but is qualified/interested.",
      "parameters": {
        "type": "object",
        "properties": {
          "lead_id": {"type": "string"},
          "reason": {"type": "string"},
          "priority": {"type": "string", "enum": ["low","medium","high","urgent"]},
          "due_at": {"type": "string"},
          "context_summary": {"type": "string"}
        },
        "required": ["lead_id", "reason", "priority", "context_summary"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "trigger_escalation",
      "description": "Escalate the call to a human agent, either warm transfer (live) or async handoff. Use whenever policy rules are met or you are out of your depth.",
      "parameters": {
        "type": "object",
        "properties": {
          "call_id": {"type": "string"},
          "reason": {"type": "string"},
          "mode": {"type": "string", "enum": ["warm_transfer","async_handoff"]}
        },
        "required": ["call_id", "reason", "mode"]
      }
    }
  }
]
```

## 4. Extraction/Classification Design and Benchmarks (Folded vs. Parallel)

To keep conversational response times fast, we target a sub-500ms budget. We benchmarked multiple free-tier and low-cost models to determine if a separate parallel extraction call was viable:
- **Google Gemini (`gemini-1.5-flash` / `gemini-2.0-flash`):** Failed with `429 Resource Exhausted` (due to free-tier quota limits of `limit: 0` for new keys) or `404 Not Found`.
- **GitHub Models (`gpt-4o-mini`):** Warmed up median wall-clock latency of **~1.9s** (range 1.5s–2.7s).
- **GitHub Models (`phi-4`):** Warmed up median wall-clock latency of **~2.0s** (range 1.7s–2.3s).
- **Ollama Cloud (`gemma4:31b-cloud`):** Warmed up median wall-clock latency of **~2.0s** (range 1.5s–2.2s).

**Final Architectural Decision:**
Because none of the candidate models could comfortably clear the ~500ms budget (all averaging ~1.7s to ~2.3s), running a separate parallel network call would consistently exceed our latency budget or trigger defensive timeouts. 

Therefore, we have **folded the intent classification and slot extraction tasks directly into the main dialogue LLM (Claude Sonnet) turn call** (or as part of its structured output/tool-calling flow). This eliminates the secondary network round-trip overhead entirely. The `dialogue_manager/intent_classifier.py` is kept as a reference and local fallback option.

## 5. Barge-In / Interruption Implementation Detail
1. STT streams interim transcripts continuously, even while agent TTS is playing (full-duplex audio required — not push-to-talk).
2. VAD flags "speech energy detected in customer channel" while agent is speaking.
3. Semantic gate: if interim transcript length > ~2 words AND not matched against a backchannel whitelist (`"mm-hmm"`, `"yeah"`, `"okay"`, `"right"`, `"got it"` — configurable list), classify as a real interrupt.
4. On real interrupt:
   - Send immediate `stop`/`clear` command to TTS stream.
   - Cancel in-flight LLM generation (stop token stream consumption).
   - Mark the in-progress agent turn `interrupted: true` in the transcript with whatever text was actually spoken (truncated, not the full planned text).
   - Begin processing the new customer utterance as the next turn.
5. On backchannel (whitelist match, short utterance): do NOT interrupt; append as a non-turn-taking event, agent continues speaking.

## 6. Guardrail: Anti-Hallucination Check for Numeric Claims
Before TTS/output, run a lightweight regex + semantic check on the LLM's draft response:
- If response contains a price, percentage, seat-count-based tier name, or date/time slot, verify a corresponding tool call+result exists in the current turn's context.
- If not traceable → discard draft, re-prompt the LLM with an explicit instruction: "You included a factual claim not backed by a tool call. Call the appropriate tool first." → regenerate.
- This is a deterministic safety net on top of prompt instructions, not a replacement for them.

## 7. Escalation Mechanics
- **Warm transfer**: telephony provider's conference/transfer primitive (e.g., Twilio `<Dial>` into a queue, or LiveKit SIP transfer) bridges a human agent in. Before/at bridge time, push a "briefing card" (JSON payload rendered in a simple internal web UI, or posted to Slack/Teams) containing: caller info, `QualificationData`, `ObjectionRecord[]`, recommended next action, and a link to the live transcript.
- **Async handoff**: call ends with a spoken close ("Someone from our team will reach out by [time]"), `create_follow_up_task` fires with `priority` derived from escalation reason + qualification data (e.g., large deal size = `urgent`).
- Escalation decision logic (rule engine, evaluated after every turn):
```python
def should_escalate(session_state, policy):
    if session_state.customer_explicitly_requested_human:
        return True, "explicit_request"
    if session_state.qualification.team_size and \
       session_state.qualification.team_size.value >= policy.triggers.deal_size_threshold_seats:
        return True, "deal_size_threshold"
    if any(kw in session_state.last_turn_text.lower() for kw in policy.triggers.keyword_triggers):
        return True, "keyword_trigger"
    unresolved = [o for o in session_state.objections if not o.resolved]
    if len(unresolved) >= policy.triggers.repeated_unresolved_objections_threshold:
        return True, "repeated_unresolved_objections"
    if session_state.frustration_score >= policy.triggers.frustration_sentiment_threshold:
        return True, "frustration_detected"
    return False, None
```

## 8. Compliance & Consent
- Call start: spoken disclosure ("This call may be recorded for quality purposes, and you're speaking with an AI assistant" — jurisdiction-dependent wording) before any qualification begins; store consent flag on `CallLogEntry`.
- DND/consent-to-call scrubbing happens upstream of this system (telephony/dialer layer), out of scope per PRD §3.
- Data retention: transcripts stored per company data-retention policy (configurable), PII fields flagged for redaction in analytics exports.

## 9. Reference Repo Structure (for the coding agent to scaffold)
```
/voice-sales-agent
  /pipeline/            # Pipecat/LiveKit pipeline setup: STT, VAD, TTS wiring
  /dialogue_manager/
    session_state.py    # SessionState class + Redis persistence
    intent_classifier.py
    escalation_engine.py
    tool_orchestrator.py
  /llm/
    system_prompt.py
    tools_schema.py
    guardrails.py        # anti-hallucination numeric-claim checker
  /integrations/
    crm/                 # base adapter + hubspot.py, salesforce.py, pipedrive.py
    calendar/            # base adapter + google.py, outlook.py
    kb/                  # pgvector search wrapper
    pricing/             # deterministic pricing lookup service
  /telephony/
    twilio_adapter.py
    livekit_adapter.py
  /admin_console/        # catalog/pricing/playbook/escalation-policy CRUD UI
  /analytics/            # dashboard + QA sampling pipeline
  tests/
    scenarios/
      example_scenario_test.py   # automates PRD §6 acceptance test via simulated audio/text turns
```

## 10. Testing Strategy
- **Unit**: tool schemas validated against mock responses; guardrail regex/semantic checks; escalation rule engine (table-driven tests per trigger).
- **Integration**: full pipeline test harness feeding pre-recorded/synthesized audio turns (including simulated barge-in timing) through the whole stack, asserting on final `SessionState` and CRM writes.
- **Acceptance**: automate the PRD §6 / AppFlow §3 example scenario end-to-end as a regression test — must pass before any release.
- **Latency load test**: concurrent simulated calls, assert P50/P95 against the budget in Design.md §3.
- **Red-team**: adversarial prompts trying to get the agent to state unverified prices, claim to be human, or ignore escalation triggers.

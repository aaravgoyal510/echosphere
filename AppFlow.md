# AppFlow.md — Conversation & System Flow

## 1. High-Level Call Lifecycle

```
INCOMING CALL / OUTBOUND DIAL
        │
        ▼
  [Call Setup] ── load caller context if known (CRM lookup by phone/CID)
        │
        ▼
  [Greeting + Consent/Disclosure]
        │
        ▼
  [Open Discovery] ←──────────────┐
        │                          │  (loop: customer can
        ▼                          │   redirect at any point)
  [Qualification]                  │
        │                          │
        ▼                          │
  [Product / Pricing Q&A] ─────────┤
        │                          │
        ▼                          │
  [Objection Handling] ────────────┘
        │
        ▼
  [Escalation Check] ── yes ──► [Human Handoff Flow] ──► END
        │ no
        ▼
  [Close / Next-Step Determination]
        │
        ├── Meeting requested/qualified ──► [Calendar Booking Flow] ──► END
        ├── Not ready, but qualified ──────► [Follow-up Scheduling Flow] ──► END
        └── Disqualified ──────────────────► [Graceful Close] ──► END
        │
        ▼
  [CRM Write-back + Call Summary] (always happens, on every path)
```

**Key property: this is not a linear pipeline.** Every state can transition to every other state at any time based on customer input. The state machine is *event-driven*, not *sequence-driven*. A dedicated "Dialogue Manager" component (see `Design.md`) is what allows jumping from Objection Handling straight to Calendar Booking, or from Close back to Product Q&A, without breaking.

## 2. Turn-Level Flow (applies to every single exchange)

```
1. Customer audio stream in
2. Streaming STT → partial transcripts (continuously updated)
3. Turn-taking model watches partial transcript + VAD + prosody
     → decides: "customer still talking" / "customer done" / "customer interrupting agent"
4. IF interrupting agent mid-utterance:
     a. Kill TTS output immediately (< 250ms)
     b. Flush any in-flight LLM generation for the old turn
     c. Mark old utterance as "interrupted" in transcript (partial credit — agent may
        resume that thought later if still relevant, but doesn't repeat it verbatim)
5. Finalized customer transcript → Dialogue Manager
6. Dialogue Manager updates Session Memory (see Schema.md: SessionState)
     - extract/merge structured fields (qualification data, stated requirements)
     - detect intent(s): question / objection / requirement-change / topic-return /
       escalation-trigger / closing-signal
7. Dialogue Manager decides: does this need a tool call before responding?
     - pricing/feature/availability question → Retrieval/RAG tool
     - "book a demo" → Calendar tool
     - CRM state check → CRM tool
8. LLM (with tools + full session memory + retrieved context) generates next utterance
     - streamed token-by-token to TTS as soon as a sentence boundary is safe to speak
9. TTS streams audio back to customer
10. Loop
```

## 3. Detailed Flow: Example Scenario Walkthrough

This maps directly to the PRD §6 acceptance scenario, showing which system component fires at each step.

| # | Customer says | System behavior |
|---|---|---|
| 1 | "Hi, can you tell me about your pricing?" | Intent: pricing question. → Retrieval tool queries pricing KB with `{team_size: unknown}` → returns tiered pricing table. Agent gives general tiers, asks team size to narrow down (qualification + product Q&A merged naturally). |
| 2 | *(while agent is mid-sentence)* "Wait — how is this different from [Competitor]?" | Barge-in detected → agent TTS halted <250ms. New intent: competitor comparison. Retrieval tool queries competitive-positioning doc for named competitor. Agent answers factually, non-disparagingly, then re-offers to finish the pricing thread if relevant. |
| 3 | "Actually we'd need this for about 50 people." | Intent: qualification update (requirement change). Session Memory field `team_size` updated 10→50 (or unknown→50). Dialogue Manager flags "pricing tier likely changed" → re-invokes Retrieval/pricing tool with new team_size → agent proactively surfaces new tier and price. |
| 4 | "Earlier you mentioned an onboarding fee — does that still apply at this size?" | Topic-return / co-reference. Dialogue Manager resolves "that" and "earlier" against Session Memory transcript log + extracted facts (finds the onboarding-fee mention from turn 1). Retrieval confirms fee policy at new tier. Agent answers directly — no "could you repeat that?" |
| 5 | "Can we set up an enterprise demo?" | Intent: closing/demo-request signal + enterprise-tier flag (50 seats + "enterprise" keyword) → Escalation Check triggers (enterprise deals route to human AE per policy) AND Calendar Booking Flow triggers in parallel. Agent checks calendar availability, proposes 2–3 slots. |
| 6 | "Tuesday at 2pm works." | Calendar tool books the event, sends invite, confirms verbally. Escalation flow simultaneously creates a "warm lead — enterprise, 50 seats, demo booked" task for the AE with full transcript + structured summary attached. |
| 7 | *(call end)* | CRM write-back: lead status = "Meeting Booked", fields: team_size=50, competitor_mentioned=[X], objections=[pricing tier confusion — resolved, competitor comparison — resolved], next_step="AE demo Tue 2pm", full transcript attached. |

## 4. Escalation Flow (Detail)

```
Escalation trigger detected (rule engine + LLM classifier, see TechSpec.md)
        │
        ▼
  Determine mode: WARM TRANSFER vs ASYNC HANDOFF
        │
   ┌────┴─────┐
   ▼          ▼
WARM         ASYNC
TRANSFER     HANDOFF
   │            │
   │            ▼
   │      Agent tells customer a human will follow up with X by Y time
   │      → CRM task created (priority = escalation reason)
   │      → Call ends gracefully
   │
   ▼
Check human agent availability (presence/status API)
   │
   ├── Available → dial/bridge human in; push structured "briefing card"
   │               (caller info, qualification snapshot, objections handled,
   │               live transcript feed) to human's screen BEFORE audio bridges
   │               → AI either goes silent (muted, still transcribing for the
   │               human's UI) or does a 10-second spoken handoff summary,
   │               then human takes over
   │
   └── Unavailable → fall back to ASYNC HANDOFF, tell customer a callback
                      window, book a specific callback time if possible
```

## 5. State Ownership Summary
- **Session Memory**: owned by Dialogue Manager, mutable throughout the call, source of truth for "what has been said/decided."
- **CRM Record**: eventually-consistent mirror, updated incrementally (not just at call end) so a mid-call escalation always has fresh data available to whoever picks up.
- **Calendar**: only written to on confirmed booking (never tentative holds without confirmation, to avoid clutter/no-shows).

See `Schema.md` for exact data structures and `TechSpec.md` for the component-level architecture and latency budget behind this flow.

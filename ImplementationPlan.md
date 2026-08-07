# ImplementationPlan.md — Phased Build Plan

## Phase 0 — Foundations (Week 1-2)
- Stand up telephony/WebRTC edge (Twilio or LiveKit) with a basic echo bot (prove audio in/out works, get baseline latency numbers).
- Wire streaming STT + streaming TTS through the pipeline (Pipecat recommended) with a hardcoded "hello world" response — validate barge-in cancel mechanics work at the transport level before any LLM logic exists.
- Stand up Postgres (leads, pricing tiers) + Redis (session state) + pgvector (KB).
- **Exit criteria**: can place a call, agent speaks a fixed line, customer can interrupt it and the audio stops within 250ms.

## Phase 1 — Core Dialogue Loop (Week 2-4)
- Implement `SessionState` (Schema.md §1) in Redis with turn-by-turn transcript logging.
- Implement main dialogue LLM call with system prompt (TechSpec.md §2) but **no tools yet** — free-form conversation only, to validate turn-taking + memory feel natural.
- Implement the parallel fast-model extraction/classification call (TechSpec.md §4) and wire its output into `SessionState.qualification` and `.objections`.
- **Exit criteria**: can hold a multi-turn conversation where the agent correctly recalls something said 5+ turns earlier without re-asking.

## Phase 2 — Retrieval & Grounding (Week 4-6)
- Load product KB into pgvector; implement `search_product_kb` tool.
- Build deterministic pricing table + `get_pricing` tool.
- Implement the anti-hallucination guardrail (TechSpec.md §6).
- **Exit criteria**: agent never states a price/feature fact without a tool call in that turn; red-team test suite passes (0 unverified numeric claims in 50 adversarial test calls).

## Phase 3 — Qualification & Objection Handling (Week 6-8)
- Implement `update_lead_qualification` + CRM adapter (start with one provider, e.g., HubSpot) and `get_lead`.
- Build objection-handling strategy library (acknowledge → reframe/evidence → check-in → advance) as guidance injected into the system prompt / retrieved playbook content, not hardcoded scripted replies.
- Implement requirement-change handling: verify that changing `team_size` mid-call triggers a re-`get_pricing` call and the agent proactively surfaces the new tier (this is the core "no fixed script" proof point).
- **Exit criteria**: PRD §6 / AppFlow §3 example scenario passes manually end-to-end.

## Phase 4 — Calendar, Escalation, Outcomes (Week 8-10)
- Implement calendar adapter (`get_calendar_availability`, `book_meeting`).
- Implement `EscalationPolicy` engine + `trigger_escalation` tool + warm transfer (SIP bridge) and async handoff (`create_follow_up_task`) flows.
- Implement outcome resolution: every call path must set `SessionState.outcome` and write a final `CallLogEntry` before teardown.
- **Exit criteria**: automated test suite covers all 4+ outcome paths (meeting booked, follow-up scheduled, disqualified, escalated) with correct CRM/calendar writes for each.

## Phase 5 — Hardening & Admin Console (Week 10-12)
- Build admin console: CRUD for pricing tiers, KB documents, escalation policy, qualification framework fields.
- Add reconnect/resume handling (call drop mid-session), provider failover (secondary STT/TTS/LLM).
- Observability: OpenTelemetry tracing per call, latency dashboard, objection-trend analytics, QA sampling pipeline.
- Load test: concurrent call simulation against latency budget (Design.md §3).
- **Exit criteria**: P95 latency within budget under target concurrent-call load; all success metrics in PRD §7 measured on a QA sample.

## Phase 6 — Pilot & Iterate (Week 12+)
- Run limited pilot (small % of real inbound/outbound calls) alongside human reps, human-reviewed transcripts for qualification accuracy and escalation precision/recall.
- Tune escalation thresholds, objection-handling prompt guidance, and turn-taking sensitivity based on real-call data.
- Expand CRM/calendar provider adapters as needed.

## Testing Checklist (carried from TechSpec.md §10, tracked per phase)
- [x] Barge-in stop latency < 250ms (Phase 0)
- [x] Memory recall across 10+ turn gap (Phase 1)
- [x] Zero unverified numeric claims in red-team suite (Phase 2)
- [x] Requirement-change re-pricing works live (Phase 3)
- [x] All 4 outcome paths automated and passing (Phase 4)
- [x] Full PRD §6 example scenario passes as an automated regression test (Phase 4, gate for Phase 5 start)
- [ ] P95 end-to-end latency within budget at target load (Phase 5)
- [ ] Escalation precision ≥85%, recall ≥90% on labeled test set (Phase 5/6)

## Suggested Team & Sequencing Notes
- Voice pipeline work (Phase 0-1) and KB/pricing data modeling (Phase 2 prep) can happen in parallel from day one.
- Do not start Phase 3 (objection handling tuning) until Phase 2's grounding guardrail is solid — otherwise objection responses will be built/tested on top of a hallucination-prone base.
- Admin console (Phase 5) can be deprioritized to after pilot if the team is resource-constrained — pricing/KB can be seeded directly via DB scripts for the pilot.

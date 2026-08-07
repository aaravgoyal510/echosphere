# PRD.md — Real-Time Voice AI Sales Agent

## 1. Product Summary
Build a real-time, speech-to-speech AI sales agent that conducts full outbound/inbound sales calls — qualification, product Q&A, objection handling, and next-step conversion (meeting booking, lead qualification, or follow-up) — without relying on a fixed script. The agent must sound and behave like a live human rep: it can be interrupted mid-sentence, it remembers everything said earlier in the call, it adapts when the customer changes requirements, and it knows when to hand off to a human.

This is NOT a scripted IVR bot and NOT a simple chatbot with TTS bolted on. The core differentiator is **conversational adaptivity under real-time constraints** (sub-800ms response latency, natural barge-in, persistent working memory).

## 2. Goals
- Conduct a full sales conversation end-to-end by voice, handling: greeting → discovery/qualification → product explanation → objection handling → next-step close.
- Feel natural: no rigid scripts, no "please hold while I look that up" dead air, no talking over the customer.
- Adapt live: if the customer changes stated requirements (e.g., team size 10 → 50), the agent must re-qualify and re-price on the fly.
- Ground every factual claim (price, features, availability, SLAs) in a retrieval system — never hallucinate numbers.
- Log everything to CRM in real time, with structured qualification data (BANT/MEDDIC-style fields).
- Escalate to a human seamlessly, with a full context handoff (transcript + extracted structured summary), when trust/complexity/emotion thresholds are hit.
- End every call with one of three explicit outcomes: **meeting booked**, **lead qualified for follow-up**, or **disqualified/no-fit** — never an ambiguous end state.

## 3. Non-Goals
- Not building a general-purpose voice assistant.
- Not handling outbound dialing/compliance (TCPA, DNC list scrubbing) in v1 — assume calls are initiated by a telephony layer that already handles consent/compliance; the agent plugs into that layer.
- Not building the CRM/calendar itself — integrating with existing systems (HubSpot/Salesforce/Pipedrive, Google/Outlook Calendar) via API.
- Not supporting multi-party calls (agent + multiple humans) in v1 — single customer per call.
- Not doing payment processing or contract signing on-call in v1.

## 4. Target Users
- **Primary**: SMB/mid-market SaaS or product companies running high-volume top-of-funnel sales calls (inbound demo requests, outbound cold/warm calls) where human SDR time is the bottleneck.
- **Secondary**: Sales ops / RevOps teams who need consistent qualification data and CRM hygiene.
- **Internal user**: Human sales reps who receive escalated/warm-handed-off calls and need instant context.

## 5. Core Functional Requirements

### FR1 — Natural Turn-Taking & Interruption Handling
- Detect end-of-turn using semantic + acoustic signals, not just silence timeout.
- Support **barge-in**: if the customer speaks while the agent is talking, the agent stops speaking within ~200ms, listens, and responds to the new input — discarding or deferring the rest of its planned utterance.
- Handle backchannels ("mm-hmm", "right", "okay") without treating them as a full turn / interruption.
- Handle overlapping speech and false starts gracefully.

### FR2 — Spoken Customer Qualification
- Elicit and extract structured qualification data conversationally (not via rigid Q&A): company size, use case, current solution/competitor, budget signal, timeline, decision-maker status, number of users/seats.
- Qualification framework: **BANT** (Budget, Authority, Need, Timeline) extended with company size / seat count as a custom field, configurable per business.
- Must be able to partially qualify, then re-qualify later in the same call if the customer revises an answer (e.g., "actually we'd need this for 50 people, not 10").

### FR3 — Session Memory
- Full working memory of everything said earlier in the current call — the agent must correctly recall and reference facts from minutes earlier ("earlier you mentioned you're evaluating Competitor X — how does this compare for your 50-seat use case?").
- Memory must support **updates/overwrites**, not just accumulation (revised user count replaces, doesn't duplicate, prior value).
- Memory persists across a warm transfer/escalation (human agent sees it; if call resumes with AI, AI still has it).
- No cross-call memory required in v1 beyond the CRM record (i.e., no long-term personal memory across separate calls, except what's stored in CRM/lead profile and re-hydrated at call start).

### FR4 — Objection Handling (Pricing, Trust, Product)
- Detect objection *type* (pricing, trust/credibility, feature gap, competitor comparison, timing, authority) from open-ended speech, not keyword matching alone.
- Respond with a *strategy*, not a scripted line: acknowledge → reframe/evidence → check-in → advance. Strategy selection is dynamic based on conversation state (e.g., don't discount immediately; probe the "why" behind price objections first).
- Support multiple objections in sequence without resetting conversation state.
- De-escalate trust objections (data security, "are you an AI", company legitimacy) with honest, non-deceptive disclosure — the agent always truthfully identifies itself as an AI when asked.

### FR5 — Retrieval-Grounded Product/Pricing/Availability Answers
- All factual answers (pricing tiers, feature availability, integrations, SLAs, current promotions, demo slot availability) come from a retrieval layer (RAG over a product knowledge base + live pricing/availability API), not from the LLM's parametric knowledge.
- If information isn't in the knowledge base, the agent says so honestly and offers to follow up or escalate — never fabricates.
- Retrieval must be fast enough not to break conversational flow (target <500ms retrieval latency, run in parallel with agent "thinking out loud" filler if needed).

### FR6 — CRM / Calendar / Lead-Management Integration
- Real-time bi-directional sync with CRM (create/update lead record, log call transcript + summary, update qualification fields, set lead status/stage).
- Calendar integration to check real availability and book meetings during the call (not just "someone will reach out") — read free/busy, propose slots conversationally, confirm booking, send invite.
- Idempotent writes (call drops/reconnects must not create duplicate leads/events).

### FR7 — Human Escalation with Context
- Trigger escalation on: explicit customer request ("let me talk to a person"), high-value/enterprise deal signals, repeated failed objection handling, detected frustration/anger, compliance-sensitive questions (legal, security audits, contracts), or low-confidence/out-of-scope situations.
- Escalation modes: **warm transfer** (live human joins the call, AI briefs them via a side-channel or a spoken/whispered summary, then either stays silent or exits), and **async handoff** (AI ends call gracefully, creates a flagged CRM task with full structured summary + transcript + recommended next step for a human to follow up).
- Human receiving the handoff must get, within seconds: caller identity, qualification summary, objections raised and how handled, stated needs, and recommended next action.

### FR8 — Explicit Outcomes
- Every call must resolve into exactly one of:
  1. **Meeting booked** — calendar event created & confirmed, confirmation sent.
  2. **Lead qualified, follow-up scheduled** — CRM task/sequence created with reason and timing.
  3. **Disqualified / no next step** — CRM updated with disqualification reason.
  4. **Escalated to human** (can be a superset state layered on top of 1–3, i.e., escalation *leads to* one of the above, driven by the human).
- Outcome + full structured summary written to CRM before call teardown completes.

## 6. Example Scenario (Acceptance Test)
> Customer calls in asking about pricing. Mid-explanation, customer interrupts to compare with a named competitor. Customer then changes stated team size from a smaller number to a much larger one. Customer later asks to go back to something discussed earlier (e.g., re-asks about a feature). Customer ultimately requests an enterprise demo.

**Expected agent behavior:**
1. Answers initial pricing question grounded in retrieval (correct tier for the currently-known team size).
2. Detects barge-in within ~200ms, stops talking, listens to the competitor comparison ask.
3. Answers the competitor comparison using retrieved competitive-positioning content, without being dismissive of the competitor (trust-preserving, factual).
4. Detects the team-size change as a qualification update, re-runs pricing against the new tier, proactively surfaces that this changes the calculation ("Since you're now at 50 seats, that actually puts you in our Business tier...").
5. When customer returns to an earlier topic, resolves the reference correctly using session memory (no "can you repeat your question").
6. Recognizes "enterprise demo" as a strong buying signal + escalation-worthy (enterprise deals routed to human AE), checks calendar availability, proposes real slots, books the meeting, and/or escalates with full context to the enterprise sales team.
7. Full transcript + structured qualification (50 seats, competitor being evaluated, pricing tier discussed, demo booked) written to CRM.

## 7. Success Metrics
- **Task success rate**: % of calls reaching an explicit outcome (target >95%, no "hung state" calls).
- **Latency**: median agent response latency (end of customer speech → start of agent speech) < 800ms; barge-in stop latency < 250ms.
- **Qualification accuracy**: % of structured CRM fields matching ground-truth (human-reviewed transcript) > 90%.
- **Hallucination rate on factual claims**: < 1% of pricing/feature statements unsupported by retrieval.
- **Escalation precision/recall**: human review confirms escalations were warranted > 85% precision; missed-escalation rate (should have escalated, didn't) < 5%.
- **Booking conversion**: % of qualified calls resulting in booked meeting (business KPI, tracked but not an engineering gate).
- **Customer-perceived naturalness**: post-call survey / sampled human review score.

## 8. Constraints & Risks
- Real-time voice AI requires a low-latency pipeline (streaming STT, streaming LLM, streaming TTS) — architectural risk if any leg is synchronous/blocking.
- Must never let the LLM output raw pricing/feature numbers without a tool call to verified data — risk of hallucinated commitments.
- Regulatory: call recording/consent notices, data retention, PII handling (especially if selling into regulated industries).
- Must disclose AI identity when directly asked ("Am I talking to a bot?") — non-negotiable, no deceptive design.
- Telephony/WebRTC reliability (packet loss, jitter) affects STT accuracy and must be handled gracefully (reconnect, resume context).

## 9. Deliverables for This Build
See companion documents: `AppFlow.md`, `Design.md`, `Schema.md`, `TechSpec.md`, `ImplementationPlan.md`.
tota.aarav1510@gmail.com
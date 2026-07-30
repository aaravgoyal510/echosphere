# Design.md — System Architecture

## 1. Architecture Overview

```
┌─────────────┐     ┌──────────────────────────────────────────────────────┐
│  Telephony /  │    │                  VOICE ORCHESTRATION LAYER            │
│  WebRTC edge  │◄──►│   (Pipecat / LiveKit Agents / self-managed pipeline)  │
│ (Twilio /     │    │                                                        │
│  LiveKit)     │    │  ┌────────┐  ┌──────────┐  ┌─────┐  ┌────────┐        │
└─────────────┘     │  │  VAD /  │─►│ Streaming│─►│ LLM │─►│Streaming│       │
                     │  │Turn-take│  │   STT    │  │Brain│  │  TTS    │       │
                     │  └────────┘  └──────────┘  └──┬──┘  └────────┘       │
                     └───────────────────────────────┼───────────────────────┘
                                                       │
                                          ┌────────────▼─────────────┐
                                          │      DIALOGUE MANAGER     │
                                          │  - Session Memory store   │
                                          │  - Intent/objection       │
                                          │    classifier             │
                                          │  - Qualification extractor│
                                          │  - Escalation rule engine │
                                          │  - Tool-call orchestrator │
                                          └────────────┬─────────────┘
                                                       │
                    ┌──────────────────┬───────────────┼───────────────┬─────────────────┐
                    ▼                  ▼               ▼               ▼                 ▼
              ┌───────────┐    ┌──────────────┐  ┌───────────┐  ┌────────────┐   ┌───────────────┐
              │  Product   │    │   Pricing /   │  │   CRM     │  │  Calendar   │   │ Human Presence /│
              │  KB / RAG  │    │  Availability │  │  Adapter  │  │  Adapter    │   │  Escalation Bus │
              │ (vector DB)│    │     API       │  │(HubSpot/  │  │(Google/     │   │  (SIP transfer/ │
              │            │    │               │  │Salesforce)│  │ Outlook)    │   │  Slack/agent UI)│
              └───────────┘    └──────────────┘  └───────────┘  └────────────┘   └───────────────┘
```

## 2. Core Components

### 2.1 Voice Orchestration Layer (real-time media pipeline)
Responsible for the low-latency audio path. Recommended: **Pipecat** or **LiveKit Agents** framework (both purpose-built for this; avoid hand-rolling WebRTC + audio buffering).

- **Turn-taking/VAD**: hybrid of acoustic VAD (e.g., Silero VAD or the STT provider's built-in endpointing) + a lightweight semantic "is this utterance complete" classifier so the agent doesn't cut in on a pause mid-thought ("I want... um... about—") but does react instantly to genuine interruptions.
- **Streaming STT**: provider with low-latency streaming + interim results (e.g., Deepgram Nova, AssemblyAI streaming, or Whisper-streaming variants). Interim transcripts feed the turn-taking model; only finalized transcripts feed the Dialogue Manager.
- **Streaming TTS**: low-latency, interruptible streaming TTS (e.g., ElevenLabs streaming, Cartesia). Must support **instant cancel** mid-stream for barge-in.
- **Barge-in mechanism**: as soon as turn-taking model flags "customer interrupting," send a cancel signal to the TTS socket and stop consuming further LLM tokens for the in-flight response; do not just lower volume — fully stop.

### 2.2 Dialogue Manager (the "brain's" control logic — not the LLM itself)
A stateful service that sits between raw transcript and the LLM call. This is what makes the system non-scripted:

- **Session Memory Store**: an in-memory (per-call) structured object (see `Schema.md: SessionState`) holding the full turn-by-turn transcript AND a continuously-updated structured extraction (qualification fields, objections raised/resolved, topics covered, open threads). Updated after every finalized customer turn via a fast structured-extraction LLM call (small/cheap model, e.g., Haiku-class) running in parallel with the main response generation.
- **Intent & Objection Classifier**: lightweight classifier (can be a Haiku-class call with function-calling / structured output, or a fine-tuned small model) tagging each turn with one or more of: `question`, `objection:pricing`, `objection:trust`, `objection:product_gap`, `objection:competitor`, `requirement_change`, `topic_return`, `closing_signal`, `escalation_request`, `small_talk`.
- **Escalation Rule Engine**: combination of deterministic rules (keyword/intent triggers like "talk to a human", deal-size thresholds from qualification data) and a model-based judgment call (repeated failed objection resolution, sentiment/frustration detection from prosody + language). Rules are configurable per business (see `Schema.md: EscalationPolicy`).
- **Tool-Call Orchestrator**: decides which external tool(s) the main LLM should call before/while generating a response, and merges tool results back into the LLM context. Runs tool calls in parallel with "thinking" filler generation where latency requires it (e.g., agent says "Let me check that for you..." only if retrieval will take >~600ms; otherwise stay silent and just answer once ready).

### 2.3 Main Conversational LLM ("the sales agent")
- Model: Claude (Sonnet-class for main dialogue; a faster/cheaper model like Haiku-class for the parallel extraction/classification calls to keep those from adding latency).
- Invoked with: system prompt (persona + sales methodology + escalation policy summary), full session memory (structured + relevant transcript window), and a tool schema (see `TechSpec.md §Tools`).
- **Never allowed to state a price, feature availability, or slot-availability fact without a preceding tool call in that turn's context** — enforced by prompt instruction + a response-time validator that checks any numeric/price claim in the output against the tool-result content actually present in context; if a claim isn't traceable to a tool result, it's blocked and regenerated with an explicit instruction to use retrieval.

### 2.4 Retrieval Layer (Product/Pricing/Availability)
- **Product KB**: vector DB (e.g., pgvector, Pinecone, Weaviate) over product docs, competitive battlecards, objection-handling playbooks, FAQ.
- **Pricing/Availability**: NOT purely vector search — pricing and seat-based tiers should be a **structured lookup/API** (deterministic), not retrieved from unstructured text, to avoid drift/error. Vector search is for qualitative content (features, positioning, policies); structured API/DB lookup for anything numeric (price, discounts, current promo, plan limits).
- Both exposed to the LLM as callable tools, not pre-stuffed into the prompt, so retrieval is query-specific and current.

### 2.5 CRM Adapter
- Thin integration service normalizing calls to HubSpot/Salesforce/Pipedrive (whichever the customer uses) behind a common internal interface: `upsert_lead()`, `log_call_event()`, `update_qualification_fields()`, `create_task()`.
- Writes incrementally during the call (not just at the end) so an escalation mid-call always has current data.

### 2.6 Calendar Adapter
- Common interface over Google Calendar / Outlook / Calendly-style scheduling APIs: `get_availability(window)`, `book_meeting(slot, attendees)`, `send_invite()`.
- Availability checks must be genuinely live (no stale cached slots offered verbally).

### 2.7 Human Escalation Bus
- For **warm transfer**: SIP/PSTN transfer via the telephony provider (e.g., Twilio `<Dial>`/conference bridge, or LiveKit SIP) PLUS a parallel data channel pushing a "briefing card" (structured summary) to the receiving human agent's screen (a simple internal web UI or a Slack/Teams message) before or as the audio bridges.
- For **async handoff**: creates a CRM task/ticket with priority + full summary + transcript link; optionally notifies via Slack/email.
- Human agent's live view during a warm transfer shows: caller info, qualification snapshot, objection history, and (optionally) a live transcript feed so they can keep listening after taking over.

## 3. Latency Budget (target end-to-end: customer stops speaking → agent starts speaking)

| Stage | Target |
|---|---|
| End-of-turn detection | ≤150ms |
| STT finalization | ≤150ms (already streaming, minimal extra) |
| Dialogue Manager (memory update + intent classify) | ≤150ms (parallelizable, small model) |
| Tool call (retrieval/CRM/calendar), if needed | ≤400ms, run concurrently with LLM "thinking," use filler phrase only if exceeding ~600ms |
| Main LLM first-token latency | ≤250ms |
| TTS first-audio-chunk latency | ≤150ms |
| **Total (no tool call needed)** | **~700-800ms** |
| **Total (with tool call)** | **~900ms-1.1s**, masked with natural filler if needed |
| Barge-in stop latency | ≤250ms |

## 4. Failure & Degradation Modes
- **STT low confidence / noisy audio**: agent asks a clarifying question rather than guessing ("Sorry, I caught part of that — could you repeat the team size?").
- **Tool call failure/timeout**: agent honestly says it can't confirm that detail right now and offers to follow up or escalate, rather than guessing.
- **Call drop/reconnect**: Session Memory persisted in a durable store keyed by call ID (Redis with short TTL + CRM as durable backstop) so a reconnect within a grace window resumes with full context.
- **Model/provider outage**: fallback to a secondary LLM/STT/TTS provider (design adapters to be provider-agnostic) or graceful async handoff ("I'm having a technical issue, let me have someone call you back at X").

## 5. Observability
- Full structured logging per call: transcript, intents, tool calls + results, latency per stage, escalation decisions, final outcome.
- Real-time dashboard: live calls, escalation queue, latency percentiles, objection-type frequency, conversion funnel.
- Sampled human QA review pipeline against the success metrics in `PRD.md §7`.

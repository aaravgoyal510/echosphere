import asyncio
from typing import Tuple
import logging
import sys
import os
import re
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dialogue_manager.models import SessionState, QualificationData
from dialogue_manager.session_state import SessionStateManager
from dialogue_manager.dialogue_manager import DialogueManager
from integrations.db_manager import DBManager
from integrations.crm.mock import MockCRMAdapter
from integrations.calendar.mock import MockCalendarAdapter
from telephony.mock import MockTelephonyAdapter
from integrations.seed import seed_database

# Custom log handler to count guardrail regenerations
class RegenerationCounterHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.count = 0
    def emit(self, record):
        if "Guardrail violation detected" in record.getMessage():
            self.count += 1

# Setup logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ChatSimulator")

# Initialize and attach custom regeneration handler
regen_counter = RegenerationCounterHandler()
logging.getLogger().addHandler(regen_counter)

async def simulate_turn(customer_text: str, state: SessionState, manager: DialogueManager) -> Tuple[str, SessionState]:
    """Helper to execute a turn, measure latency, track retries, and output metrics."""
    regen_counter.count = 0
    start = time.perf_counter()
    reply, next_state = await manager.handle_turn(customer_text, state)
    dur_ms = (time.perf_counter() - start) * 1000
    
    print(f"\nCUSTOMER: \"{customer_text}\"")
    print(f"ARIA:     \"{reply}\"")
    print(f"[METRIC]  Latency: {dur_ms:.1f}ms | Guardrail Regenerations: {regen_counter.count}")
    return reply, next_state

def check_transcript_grounding(state: SessionState) -> bool:
    """Verifies that all agent turns in the final transcript are fully grounded."""
    from dialogue_manager.guardrails import verify_response_grounding
    for turn in state.transcript:
        if turn.speaker == "agent":
            # Since these are final responses, no new tools are active in this check.
            # We verify against the session's historical executed tools.
            is_grounded, _ = verify_response_grounding(turn.text, [], state.executed_tools)
            if not is_grounded:
                return False
    return True

async def run_simulation():
    load_dotenv(override=True)
    
    if not os.getenv("AICREDITS_API_KEY"):
        print("ERROR: AICREDITS_API_KEY not set in .env file. Please set it before running the simulator.")
        sys.exit(1)
        
    print("\n======================================================================")
    print("STARTING END-TO-END ACCEPTANCE FLOW SIMULATION (PRD §6 / AppFlow §3)")
    print("======================================================================\n")

    # 1. Initialize and Seed database (Force Postgres or fall back)
    # Check if Postgres is reachable, if not print a warning
    db_file = "echosphere_chat_sim.db"
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except Exception:
            pass
    db_manager = DBManager(sqlite_path=db_file)
    db_manager.initialize_tables()
    await seed_database(db_manager)
    
    db_mode = "SQLite File" if db_manager.use_sqlite else "PostgreSQL Database"
    print(f"Database target active: {db_mode}")

    # 2. Instantiate adapters and manager
    session_manager = SessionStateManager()
    crm_adapter = MockCRMAdapter(db_manager)
    calendar_adapter = MockCalendarAdapter()
    telephony_adapter = MockTelephonyAdapter()

    dialogue_manager = DialogueManager(
        db_manager=db_manager,
        session_manager=session_manager,
        crm_adapter=crm_adapter,
        calendar_adapter=calendar_adapter,
        telephony_adapter=telephony_adapter
    )

    # 3. Create a test lead
    test_phone = "+15550212"
    lead = crm_adapter.upsert_lead(
        phone=test_phone,
        name="Sarah Jenkins",
        email="sarah@enterprisecorp.com",
        company="Enterprise Corp"
    )

    # 4. Initialize session state
    call_id = f"call_{int(datetime.now(timezone.utc).timestamp())}"
    session_state = SessionState(
        call_id=call_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        channel="inbound"
    )
    session_state.caller["phone"] = test_phone
    session_state.caller["crm_lead_id"] = lead.lead_id
    session_state.caller["known_from_crm"] = True
    session_manager.save_session(session_state)

    # --- TURN 1: Customer asks about general pricing ---
    current_state = session_manager.get_session(call_id)
    agent_reply, current_state = await simulate_turn(
        "Hi, I'm calling to find out how much your telephony system costs.",
        current_state,
        dialogue_manager
    )
    
    # --- SIMULATE BARGE-IN INTERRUPTION MID-REPLY ---
    interrupted_text = "Our pricing is tiered based on the size of your team. We offer a Starter plan,"
    dialogue_manager.interrupt_agent_turn(current_state, interrupted_text)
    print(f"\n[SYSTEM] Customer barge-in detected! Truncating Aria's response to: \"{interrupted_text}...\"")
    
    # --- TURN 2: Customer interrupts to compare against HubSpot ---
    agent_reply, current_state = await simulate_turn(
        "Actually, we are comparing you to HubSpot. How are you different?",
        current_state,
        dialogue_manager
    )
    print(f"[METRIC]  Objections logged: {[obj.type for obj in current_state.objections]}")
    
    # --- TURN 3: Customer states team size (25 seats) ---
    agent_reply, current_state = await simulate_turn(
        "We are currently a team of 25 seats.",
        current_state,
        dialogue_manager
    )
    print(f"[METRIC]  Team size qualification: {current_state.qualification.team_size.value if current_state.qualification.team_size else 'None'} seats")
    
    # --- TURN 4: Customer CHANGES team size to 45 seats ---
    agent_reply, current_state = await simulate_turn(
        "Wait, actually, I made a mistake. We will need 45 seats next month. How much is that?",
        current_state,
        dialogue_manager
    )
    print(f"[METRIC]  Team size qualification: {current_state.qualification.team_size.value if current_state.qualification.team_size else 'None'} seats")

    # --- TURN 5: Customer asks about the onboarding fee (memory check) ---
    agent_reply, current_state = await simulate_turn(
        "And is there an onboarding fee?",
        current_state,
        dialogue_manager
    )
    
    # --- TURN 6: Customer asks to book a demo ---
    agent_reply, current_state = await simulate_turn(
        "That sounds good. Let's schedule an enterprise demo for next Monday.",
        current_state,
        dialogue_manager
    )

    # --- TURN 7: Customer books the slot ---
    slots = calendar_adapter.get_calendar_availability(
        datetime.now(timezone.utc).isoformat(), 
        (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(), 
        "enterprise_demo"
    )
    chosen_slot = slots[0].start if slots else "2026-08-03T10:00:00"
    
    agent_reply, current_state = await simulate_turn(
        f"Perfect. Let's book the slot starting at {chosen_slot}.",
        current_state,
        dialogue_manager
    )

    # --- FINAL VALIDATIONS & ASSERTIONS ---
    print("\n======================================================================")
    print("SIMULATION SUCCESSFUL! PERFORMING ASSERTIONS")
    print("======================================================================")
    
    crm_lead = crm_adapter.get_lead(test_phone)
    assert crm_lead is not None, "Lead not found in database. Make sure the database was seeded and not deleted concurrently."
    assert crm_lead.qualification is not None, "Lead has no qualification record."
    
    print(f"Asserting team size in CRM is 45...")
    assert crm_lead.qualification.team_size is not None, "Lead qualification team_size was not updated (remained None)."
    assert crm_lead.qualification.team_size.value == 45, f"Expected 45 seats, got {crm_lead.qualification.team_size.value}"
    
    print(f"Asserting competitor HubSpot is logged in CRM...")
    assert crm_lead.qualification.current_solution is not None, "Lead qualification current_solution was not updated (remained None)."
    assert "hubspot" in crm_lead.qualification.current_solution.value.lower(), "Expected HubSpot to be saved as current_solution"
    
    print(f"Asserting competitor objection was logged in SessionState...")
    assert any(o.type == "competitor" for o in current_state.objections), "Expected competitor objection in SessionState"
    
    print(f"Asserting final outcome is 'meeting_booked'...")
    assert current_state.outcome == "meeting_booked", f"Expected meeting_booked outcome, got {current_state.outcome}"
    
    print(f"Asserting first turn is flagged as interrupted...")
    assert current_state.transcript[1].interrupted == True, "Expected Turn 1 (agent) to be flagged as interrupted"
    
    print(f"Asserting anti-hallucination validation passed for all turns...")
    assert check_transcript_grounding(current_state) == True, "Expected all final transcript turns to be fully grounded"
    
    print("\n[SUCCESS] All continuous flow assertions PASSED!")
    print("======================================================================\n")

if __name__ == "__main__":
    asyncio.run(run_simulation())

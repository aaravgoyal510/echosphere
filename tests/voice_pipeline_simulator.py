import asyncio
import logging
import sys
import os
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
from pipeline.simulated_pipeline import SimulatedSTTAdapter, SimulatedTTSAdapter, TurnTakingManager
from pipeline.pipeline_coordinator import PipelineCoordinator
from integrations.seed import seed_database

# Setup logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("VoicePipelineSimulator")

# Global session state pointer to share between callbacks
current_state = None

async def run_simulation():
    global current_state
    load_dotenv(override=True)
    
    if not os.getenv("AICREDITS_API_KEY"):
        print("ERROR: AICREDITS_API_KEY not set in .env file.")
        sys.exit(1)
        
    print("\n======================================================================")
    print("STARTING END-TO-END VOICE PIPELINE INTEGRATION TEST (PHASE 2)")
    print("======================================================================\n")

    # 1. Initialize DB and Seed
    db_file = "echosphere_voice_sim.db"
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except Exception:
            pass
    db_manager = DBManager(sqlite_path=db_file)
    db_manager.initialize_tables()
    await seed_database(db_manager)

    # 2. Instantiate adapters
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

    # 3. Instantiate pipeline adapters
    stt_adapter = SimulatedSTTAdapter()
    
    tts_chunks = []
    def on_tts_chunk(chunk: str):
        tts_chunks.append(chunk)
        # Output speech text dynamically
        sys.stdout.write(chunk)
        sys.stdout.flush()

    tts_adapter = SimulatedTTSAdapter(on_audio_chunk=on_tts_chunk)
    turn_taking_manager = TurnTakingManager(stt_adapter, tts_adapter)

    coordinator = PipelineCoordinator(
        dialogue_manager=dialogue_manager,
        stt=stt_adapter,
        tts=tts_adapter,
        turn_taking_manager=turn_taking_manager
    )

    # 4. Set up final customer transcript handler
    # When STT completes a final transcription, we process it as a turn
    async def on_customer_final(text: str):
        # We start the coordinator turn in a background task
        asyncio.create_task(coordinator.process_customer_utterance(text))

    await stt_adapter.start_listening(
        on_interim=turn_taking_manager.handle_customer_interim_transcript,
        on_final=on_customer_final
    )

    # 5. Create a test lead and initialize call state
    test_phone = "+15550212"
    lead = crm_adapter.upsert_lead(
        phone=test_phone,
        name="Sarah Jenkins",
        email="sarah@enterprisecorp.com",
        company="Enterprise Corp"
    )

    call_id = f"call_{int(datetime.now(timezone.utc).timestamp())}"
    current_state = SessionState(
        call_id=call_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        channel="inbound"
    )
    current_state.caller["phone"] = test_phone
    current_state.caller["crm_lead_id"] = lead.lead_id
    current_state.caller["known_from_crm"] = True
    session_manager.save_session(current_state)
    
    # Initialize the coordinator call ID
    coordinator.start_call(call_id)

    # --- TURN 1: Customer asks about general pricing ---
    print("CUSTOMER: \"Hi, I'm calling to find out how much your telephony system costs.\"")
    # Simulate speaking
    await stt_adapter.simulate_customer_speech("Hi, I'm calling to find out how much your telephony system costs.", word_delay=0.01)
    
    # Wait for agent to start speaking and yield some words
    await asyncio.sleep(4.0)

    # --- TURN 2: Customer barge-in mid-reply ---
    # Customer interrupts: "Actually, we are comparing you to HubSpot. How are you different?"
    print("\n\n(Customer starts interrupting...)")
    interruption_start = time.perf_counter()
    
    # We simulate customer speaking. The first few words should instantly cancel active agent TTS.
    await stt_adapter.simulate_customer_speech("Actually, we are comparing you to HubSpot. How are you different?", word_delay=0.01)
    
    # Wait for the next agent turn to compile and start speaking
    await asyncio.sleep(12.0)
    print("\n")

    # --- TURN 3: Customer states team size (25 seats) ---
    print("\nCUSTOMER: \"We are currently a team of 25 seats.\"")
    await stt_adapter.simulate_customer_speech("We are currently a team of 25 seats.", word_delay=0.01)
    await asyncio.sleep(10.0)
    print("\n")

    # --- TURN 4: Customer CHANGES team size to 45 seats ---
    print("\nCUSTOMER: \"Wait, actually, I made a mistake. We will need 45 seats next month. How much is that?\"")
    await stt_adapter.simulate_customer_speech("Wait, actually, I made a mistake. We will need 45 seats next month. How much is that?", word_delay=0.01)
    await asyncio.sleep(10.0)
    print("\n")

    # --- TURN 5: Customer asks about the onboarding fee (memory check) ---
    print("\nCUSTOMER: \"And is there an onboarding fee?\"")
    await stt_adapter.simulate_customer_speech("And is there an onboarding fee?", word_delay=0.01)
    await asyncio.sleep(8.0)
    print("\n")

    # --- TURN 6: Customer asks to book a demo ---
    print("\nCUSTOMER: \"That sounds good. Let's schedule an enterprise demo for next Monday.\"")
    await stt_adapter.simulate_customer_speech("That sounds good. Let's schedule an enterprise demo for next Monday.", word_delay=0.01)
    await asyncio.sleep(8.0)
    print("\n")

    # --- TURN 7: Customer books the slot ---
    slots = calendar_adapter.get_calendar_availability(
        datetime.now(timezone.utc).isoformat(), 
        (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(), 
        "enterprise_demo"
    )
    chosen_slot = slots[0].start if slots else "2026-08-03T10:00:00+00:00"
    
    print(f"\nCUSTOMER: \"Perfect. Let's book the slot starting at {chosen_slot}.\"")
    await stt_adapter.simulate_customer_speech(f"Perfect. Let's book the slot starting at {chosen_slot}.", word_delay=0.01)
    await asyncio.sleep(10.0)
    print("\n")

    # --- FINAL VALIDATIONS & ASSERTIONS ---
    print("\n======================================================================")
    print("SIMULATION SUCCESSFUL! PERFORMING ASSERTIONS")
    print("======================================================================")
    
    # Reload session state from DB to assert final values
    final_state = session_manager.get_session(call_id)
    crm_lead = crm_adapter.get_lead(test_phone)
    assert crm_lead is not None, "Lead not found in database. Make sure database was seeded and not deleted concurrently."
    assert crm_lead.qualification is not None, "Lead has no qualification record."
    
    print("Asserting team size in CRM is 45...")
    assert crm_lead.qualification.team_size is not None, "Lead qualification team_size was not updated (remained None)."
    assert crm_lead.qualification.team_size.value == 45, f"Expected 45 seats, got {crm_lead.qualification.team_size.value}"
    
    print("Asserting competitor HubSpot is logged in CRM...")
    assert crm_lead.qualification.current_solution is not None, "Lead qualification current_solution was not updated (remained None)."
    assert "hubspot" in crm_lead.qualification.current_solution.value.lower(), "Expected HubSpot to be saved as current_solution"
    
    print("Asserting competitor objection was logged in SessionState...")
    assert any(o.type == "competitor" for o in final_state.objections), "Expected competitor objection in SessionState"
    
    print("Asserting final outcome is 'meeting_booked'...")
    assert final_state.outcome == "meeting_booked", f"Expected meeting_booked outcome, got {final_state.outcome}"
    
    print("Asserting agent TTS was interrupted and stop code received...")
    assert "[TTS_STOPPED_MID_SENTENCE]" in tts_chunks, "Expected TTS stop chunk in output audio stream"
    
    print("Asserting at least one agent turn is flagged as interrupted in transcript...")
    assert any(turn.interrupted for turn in final_state.transcript), "Expected at least one agent turn to be flagged as interrupted"

    print("\n[SUCCESS] All voice pipeline integration assertions PASSED!")
    print("======================================================================\n")

if __name__ == "__main__":
    asyncio.run(run_simulation())

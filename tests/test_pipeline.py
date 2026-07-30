import pytest
import asyncio
import time
from pipeline.simulated_pipeline import SimulatedSTTAdapter, SimulatedTTSAdapter, TurnTakingManager

@pytest.mark.asyncio
async def test_backchannel_no_interruption():
    """Verify that a backchannel does not interrupt the agent."""
    stt = SimulatedSTTAdapter()
    tts = SimulatedTTSAdapter()
    manager = TurnTakingManager(stt, tts)

    # Mock dialogue manager interruption handler
    interrupted_triggered = False
    async def on_interruption():
        nonlocal interrupted_triggered
        interrupted_triggered = True
    
    manager.register_interruption_handler(on_interruption)

    # Set up simulated stream of agent speech
    async def agent_speech_stream():
        yield "This is a very long explanation about the pricing tiers "
        await asyncio.sleep(0.1)
        yield "which starts at twenty dollars per seat per month."

    # Start agent speaking
    speak_task = asyncio.create_task(tts.speak(agent_speech_stream()))
    await asyncio.sleep(0.02)  # Let it start speaking

    # Register STT callbacks
    await stt.start_listening(
        on_interim=manager.handle_customer_interim_transcript,
        on_final=lambda x: None
    )

    # Simulate backchannel "mm-hmm"
    await stt.simulate_customer_speech("mm-hmm", word_delay=0.01)

    # Wait for agent speech to finish
    await speak_task

    assert tts.speaking is False
    assert tts.interrupted is False
    assert interrupted_triggered is False


@pytest.mark.asyncio
async def test_barge_in_interruption_and_latency():
    """Verify that a real customer turn interrupts the agent and stops TTS under 250ms."""
    stt = SimulatedSTTAdapter()
    
    chunks_received = []
    def on_audio(chunk):
        chunks_received.append(chunk)

    tts = SimulatedTTSAdapter(on_audio_chunk=on_audio)
    manager = TurnTakingManager(stt, tts)

    interrupted_triggered = False
    async def on_interruption():
        nonlocal interrupted_triggered
        interrupted_triggered = True
    
    manager.register_interruption_handler(on_interruption)

    # Set up simulated agent speech
    async def agent_speech_stream():
        for word in ["Hello", "we", "offer", "multiple", "plans", "that", "range", "from"]:
            yield word + " "
            await asyncio.sleep(0.1)

    # Start speaking
    speak_task = asyncio.create_task(tts.speak(agent_speech_stream()))
    await asyncio.sleep(0.05)  # Let it speak a few words

    await stt.start_listening(
        on_interim=manager.handle_customer_interim_transcript,
        on_final=lambda x: None
    )

    # Measure stop latency
    start_time = time.perf_counter()
    
    # Simulate real customer interruption
    await stt.simulate_customer_speech("wait, how does this compare?", word_delay=0.01)

    # Wait for the speak task to complete (it should end immediately due to cancellation)
    await speak_task
    
    stop_latency = (time.perf_counter() - start_time) * 1000

    assert tts.speaking is False
    assert tts.interrupted is True
    assert interrupted_triggered is True
    assert stop_latency < 250.0  # Limit is 250ms
    assert chunks_received[-1] == "[TTS_STOPPED_MID_SENTENCE]"


@pytest.mark.asyncio
async def test_tool_call_timeout_no_partial_update():
    """Verify that if a tool-calling query fails or is cancelled, state is not partially updated."""
    from dialogue_manager.models import SessionState, QualificationData
    from dialogue_manager.session_state import SessionStateManager
    from dialogue_manager.dialogue_manager import DialogueManager
    from integrations.db_manager import DBManager
    from integrations.crm.mock import MockCRMAdapter
    
    db_manager = DBManager(sqlite_path="echosphere_test.db")
    db_manager.initialize_tables()
    
    session_manager = SessionStateManager()
    crm_adapter = MockCRMAdapter(db_manager)
    
    # Pre-populate lead in CRM
    test_phone = "+15559999"
    lead = crm_adapter.upsert_lead(
        phone=test_phone,
        name="Test Lead"
    )
    assert lead.qualification.team_size is None
    
    # Initialize state
    call_id = "call_test_timeout"
    state = SessionState(
        call_id=call_id,
        started_at="2026-07-30T10:00:00",
        channel="inbound"
    )
    state.caller["phone"] = test_phone
    state.caller["crm_lead_id"] = lead.lead_id
    session_manager.save_session(state)
    
    # Instantiate DialogueManager
    manager = DialogueManager(
        db_manager=db_manager,
        session_manager=session_manager,
        crm_adapter=crm_adapter,
        calendar_adapter=None,
        telephony_adapter=None
    )
    
    # Mock client query to raise CancelledError (simulating barge-in / timeout)
    class MockTimeoutClient:
        async def query(self, *args, **kwargs):
            raise asyncio.CancelledError("Simulated timeout/cancellation")
            
    manager.claude_client = MockTimeoutClient()
    
    # Run turn. Since client queries raise CancelledError, it propagates or is handled
    with pytest.raises(asyncio.CancelledError):
        await manager.handle_turn("Actually we need 45 seats", state)
        
    # Reload CRM lead and check that team_size is STILL None (no partial/corrupted update)
    crm_lead = crm_adapter.get_lead(test_phone)
    assert crm_lead.qualification.team_size is None, "CRM must not be updated if turn failed"


@pytest.mark.asyncio
async def test_real_piper_stop_latency():
    """Verify that the real Piper TTS adapter stops speaking and cancels within 250ms."""
    import os
    from pipeline.piper_adapter import PiperTTSAdapter
    
    # Instantiate the real Piper adapter
    tts = PiperTTSAdapter()
    
    # Check if ONNX model exists. If not, download/skip to avoid breaking test run.
    if not os.path.exists(tts.onnx_path):
        pytest.skip("Piper voice ONNX model not downloaded. Skipping real stop latency test.")
        
    await tts.initialize()
    
    # Create text stream
    async def long_stream():
        yield "This is a very long sentence that will generate lots of audio data. "
        yield "We want to make sure the Piper voice engine can be interrupted immediately."
        await asyncio.sleep(0.5)
        yield "More text just in case."
        
    # Start speaking in a background task
    speak_task = asyncio.create_task(tts.speak(long_stream()))
    await asyncio.sleep(0.2)  # Let it generate and play some audio
    
    assert tts.speaking is True
    
    # Measure stop latency
    start_time = time.perf_counter()
    await tts.stop_speaking()
    await speak_task  # Wait for speech to terminate
    
    stop_latency = (time.perf_counter() - start_time) * 1000
    
    assert tts.speaking is False
    assert tts.interrupted is True
    assert stop_latency < 250.0, f"Expected Piper stop latency < 250ms, got {stop_latency:.1f}ms"

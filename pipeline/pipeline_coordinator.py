import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, AsyncIterator
from dialogue_manager.dialogue_manager import DialogueManager
from dialogue_manager.models import SessionState, CallLogEntry
from pipeline.simulated_pipeline import SimulatedSTTAdapter, SimulatedTTSAdapter, TurnTakingManager

logger = logging.getLogger(__name__)

class PipelineCoordinator:
    """
    Coordinates real-time audio pipeline events.
    Integrates DialogueManager with TurnTakingManager and manages interruptible task lifecycles.
    """
    def __init__(
        self,
        dialogue_manager: DialogueManager,
        stt: SimulatedSTTAdapter,
        tts: SimulatedTTSAdapter,
        turn_taking_manager: TurnTakingManager
    ):
        self.dialogue_manager = dialogue_manager
        self.stt = stt
        self.tts = tts
        self.ttm = turn_taking_manager
        
        self.call_id: Optional[str] = None
        self.active_turn_task: Optional[asyncio.Task] = None
        self.current_state: Optional[SessionState] = None
        
        # Wire interruption callback from TurnTakingManager to our handler
        self.ttm.register_interruption_handler(self.on_interruption)

    def start_call(self, call_id: str) -> None:
        """Initializes the active call ID for the coordinator."""
        self.call_id = call_id

    async def process_customer_utterance(self, text: str) -> None:
        """
        Processes a customer utterance. Starts an asynchronous dialogue turn task.
        If a prior turn is active, it cancels it.
        Loads the latest session state from DB to prevent stale state overrides.
        """
        if not self.call_id:
            logger.error("Cannot process customer utterance: call_id is not set. Call start_call() first.")
            return

        # Load freshest state from DB
        state = self.dialogue_manager.session_manager.get_session(self.call_id)
        if not state:
            logger.error(f"Session state not found for call_id: {self.call_id}")
            return
            
        self.current_state = state
        
        if self.active_turn_task and not self.active_turn_task.done():
            logger.info("New customer utterance arrived before previous finished. Cancelling old task.")
            self.active_turn_task.cancel()
            
        self.active_turn_task = asyncio.create_task(self._run_turn(text))
        try:
            await self.active_turn_task
        except asyncio.CancelledError:
            logger.info("Active dialogue turn task was cancelled.")
        except Exception as e:
            logger.error(f"Active dialogue turn failed: {e}. Escalating call.")
            await self._handle_coordinator_failure(e)
        finally:
            self.active_turn_task = None

    async def _handle_coordinator_failure(self, error: Exception) -> None:
        """Gracefully escalates the active session state when LLM services fail."""
        if self.current_state:
            self.current_state.outcome = "escalated"
            self.current_state.escalation.triggered = True
            self.current_state.escalation.reason = f"LLM provider outage: {str(error)}"
            self.dialogue_manager.session_manager.save_session(self.current_state)
            
        fallback_msg = "I'm sorry, I'm having a technical issue looking up that information. Let me transfer you to a human team member."
        async def fallback_stream() -> AsyncIterator[str]:
            yield fallback_msg
            
        await self.tts.speak(fallback_stream())

    async def _run_turn(self, text: str) -> None:
        """Executes the dialogue manager and pipes the output stream to the TTS adapter."""
        if not self.current_state:
            return
            
        # Run dialogue manager (which queries the LLM and runs tools)
        agent_reply, updated_state = await self.dialogue_manager.handle_turn(text, self.current_state)
        self.current_state = updated_state
        
        # TTS Adapter speak stream simulator
        async def reply_stream() -> AsyncIterator[str]:
            words = agent_reply.split()
            for word in words:
                yield word + " "
                
        await self.tts.speak(reply_stream())

    async def on_interruption(self) -> None:
        """
        Interruption callback triggered by TurnTakingManager.
        Cancels the in-flight dialogue turn task and truncates the transcript turn.
        """
        logger.info("[PipelineCoordinator] Handling customer barge-in interruption...")
        
        # 1. Cancel in-flight dialogue turn task
        if self.active_turn_task and not self.active_turn_task.done():
            self.active_turn_task.cancel()
            
        # 2. Get spoken text and truncate transcript in the database
        if self.call_id:
            fresh_state = self.dialogue_manager.session_manager.get_session(self.call_id)
            if fresh_state:
                spoken_words = "".join(self.tts.spoken_text).strip()
                if not spoken_words:
                    spoken_words = "..."
                    
                self.dialogue_manager.interrupt_agent_turn(fresh_state, spoken_words)
                self.current_state = fresh_state
                logger.info(f"[PipelineCoordinator] Truncated agent turn. Spoken portion: '{spoken_words}'")

    def end_call(self, default_outcome: str = "disqualified", graceful: bool = True) -> None:
        """
        Explicitly ends the active call session. 
        Ensures a CallLogEntry is generated and logged to the CRM and local DB.
        """
        if not self.call_id or not self.current_state:
            return
            
        try:
            state = self.current_state
            
            # Map outcome: if outcome is "in_progress", default it to default_outcome
            if state.outcome == "in_progress" or not state.outcome:
                if state.escalation.triggered or not graceful:
                    state.outcome = "escalated"
                    if not graceful and not state.escalation.triggered:
                        state.escalation.triggered = True
                        state.escalation.reason = "Abrupt call disconnection / dropped line"
                else:
                    state.outcome = default_outcome
            
            # Save latest state outcome
            self.dialogue_manager.session_manager.save_session(state)
            self.current_state = state
            
            # Enforce CallLogEntry writing
            now_str = datetime.now(timezone.utc).isoformat()
            
            # Calculate duration
            started = datetime.fromisoformat(state.started_at.replace('Z', '+00:00'))
            ended = datetime.now(timezone.utc)
            duration_sec = (ended - started).total_seconds()
            
            # Transcript summary
            summary_txt = f"Call completed. Transcript contains {len(state.transcript)} turns."
            if state.transcript:
                summary_txt += f" Last turn: '{state.transcript[-1].text}'"
                
            entry = CallLogEntry(
                call_id=state.call_id,
                lead_id=state.caller.get("crm_lead_id") or "lead_unknown",
                started_at=state.started_at,
                ended_at=now_str,
                duration_sec=duration_sec,
                transcript_url="http://example.com/transcripts/" + state.call_id,
                summary=summary_txt,
                objections_raised=state.objections,
                outcome=state.outcome,
                escalation_reason=state.escalation.reason if state.escalation.triggered else None
            )
            
             # Log call entry to CRM (which also updates local mock DB)
            self.dialogue_manager.crm_adapter.log_call_entry(entry)
            logger.info(f"[PipelineCoordinator] Saved CallLogEntry for call {self.call_id} with outcome: {state.outcome}")

            # Persist deterministic call stats
            objections_raised = len(state.objections)
            objections_resolved = len([obj for obj in state.objections if obj.resolved])
            guardrail_triggers = getattr(state, "guardrail_trigger_count", 0)
            
            competitors = []
            for obj in state.objections:
                if obj.type == "competitor" and obj.detail:
                    competitors.append(obj.detail)
            competitors_str = ",".join(competitors) if competitors else None
            
            team_size = None
            if state.qualification.team_size and state.qualification.team_size.value is not None:
                try:
                    team_size = int(state.qualification.team_size.value)
                except Exception:
                    pass
                    
            self.dialogue_manager.db.save_call_stats(
                call_id=state.call_id,
                timestamp=state.started_at,
                outcome=state.outcome,
                objections_raised=objections_raised,
                objections_resolved=objections_resolved,
                guardrail_triggers=guardrail_triggers,
                team_size=team_size,
                competitors_mentioned=competitors_str,
                duration_seconds=duration_sec
            )

            # Generate and save lead summary for repeat customer context
            lead_id = state.caller.get("crm_lead_id")
            if lead_id and lead_id != "lead_unknown" and state.outcome != "disqualified":
                asyncio.create_task(self._generate_and_save_lead_summary(state))

        except Exception as e:
            logger.error(f"[PipelineCoordinator] Error ending call: {e}")

    async def _generate_and_save_lead_summary(self, state: SessionState) -> None:
        try:
            lead_id = state.caller.get("crm_lead_id")
            if not lead_id or not state.transcript:
                return
            
            transcript_txt = ""
            for turn in state.transcript:
                transcript_txt += f"{turn.speaker}: {turn.text}\n"
                
            prompt = (
                "Summarize the following sales call transcript in 2-3 sentences. "
                "Highlight key requirements, objections raised/resolved, and agreed next steps.\n\n"
                f"Transcript:\n{transcript_txt}"
            )
            
            system_prompt = (
                "You are an expert CRM assistant. Write a brief, punchy call summary for the lead's profile. "
                "Do not include greeting or introductory text. Speak directly in 2-3 sentences."
            )
            response_text, _ = await self.dialogue_manager.dialogue_llm_client.query(
                system_prompt=system_prompt,
                user_message=prompt
            )
            
            self.dialogue_manager.db.save_lead_summary(lead_id, response_text.strip())
            logger.info(f"[PipelineCoordinator] Saved lead summary for {lead_id}: {response_text.strip()}")
        except Exception as e:
            logger.error(f"[PipelineCoordinator] Error generating lead summary: {e}")

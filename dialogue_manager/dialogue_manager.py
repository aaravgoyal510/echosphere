import json
import asyncio
import logging
from typing import Tuple, List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone

from dialogue_manager.models import (
    SessionState,
    TranscriptTurn,
    QualificationData,
    QualificationValue,
    ObjectionRecord,
    FollowUpTask,
    CallLogEntry,
    SessionEscalationState
)
from dialogue_manager.session_state import SessionStateManager
from dialogue_manager.guardrails import verify_response_grounding
from dialogue_manager.escalation_engine import EscalationPolicy
from llm.dialogue_llm_client import DialogueLLMClient
from llm.system_prompt import get_system_prompt
from llm.tools_schema import TOOLS
from integrations.db_manager import DBManager
from integrations.pricing.pricing_service import PricingService
from integrations.kb.kb_search import KBSearchService
from integrations.crm.base import CRMAdapter
from integrations.calendar.base import CalendarAdapter
from telephony.base import TelephonyAdapter

logger = logging.getLogger(__name__)

def transcript_to_llm_messages(transcript: List[TranscriptTurn]) -> List[Dict[str, Any]]:
    """Converts transcript turns into OpenAI compatible message format, capped to the last 6 turns."""
    llm_msgs = []
    for turn in transcript[-6:]:
        role = "user" if turn.speaker in ("customer", "human_agent") else "assistant"
        content = turn.text
        
        # Merge consecutive turns of the same role
        if llm_msgs and llm_msgs[-1]["role"] == role:
            # Handle list/dict structure in content
            if isinstance(llm_msgs[-1]["content"], str):
                llm_msgs[-1]["content"] += "\n" + content
            else:
                # If content is a list of blocks, append a text block
                llm_msgs[-1]["content"].append({"type": "text", "text": content})
        else:
            llm_msgs.append({"role": role, "content": content})
    return llm_msgs

class DialogueManager:
    def __init__(
        self,
        db_manager: DBManager,
        session_manager: SessionStateManager,
        crm_adapter: CRMAdapter,
        calendar_adapter: CalendarAdapter,
        telephony_adapter: TelephonyAdapter,
        model_name: Optional[str] = None
    ):
        self.db = db_manager
        self.session_manager = session_manager
        self.crm_adapter = crm_adapter
        self.calendar_adapter = calendar_adapter
        self.telephony_adapter = telephony_adapter
        
        self.pricing_service = PricingService(db_manager)
        self.kb_search = KBSearchService(db_manager)
        self.dialogue_llm_client = DialogueLLMClient(model_name=model_name)
        
        self.get_system_prompt = get_system_prompt
        self.tools_definition = TOOLS
        self.escalation_policy = EscalationPolicy()

    async def _get_active_objection_playbook(self, state: SessionState) -> Optional[str]:
        """Finds the first active objection and retrieves its playbook from the KB (with static fallback)."""
        active_objections = [obj for obj in state.objections if not obj.resolved]
        if not active_objections:
            return None
            
        obj_type = active_objections[0].type
        try:
            docs = await self.kb_search.search_product_kb(
                query=f"{obj_type} objection",
                doc_type="playbook",
                limit=1
            )
            if docs:
                doc = docs[0]
                if obj_type in doc.title.lower() or obj_type in doc.content.lower():
                    return doc.content
        except Exception as e:
            logger.warning(f"Failed to query objection playbook from KB: {e}")
            
        # Standard structural playbooks (Acknowledge -> Reframe -> Check-in -> Advance) with NO digits to prevent guardrail trips
        static_playbooks = {
            "pricing": (
                "Structure for handling pricing objections: First, Acknowledge: Validate the customer's concern about cost. "
                "Second, Reframe/Evidence: Highlight the value of the platform, such as the natural conversation flow, "
                "automated qualifications, and custom onboarding fee waivers for larger teams. "
                "Third, Check-in: Ask if the explanation makes sense. "
                "Fourth, Advance: Propose the next logical step, such as booking a demo to see the system in action."
            ),
            "competitor": (
                "Structure for handling competitor comparisons: First, Acknowledge: Validate the customer's choice to compare options. "
                "Second, Reframe/Evidence: Introduce our key differentiators: natural turn-taking with low latency, real-time barge-in, "
                "and custom onboarding fee waivers for large teams. "
                "Third, Check-in: Ask if they would like to explore these features. "
                "Fourth, Advance: Suggest scheduling a demo to see the direct CRM integrations."
            ),
            "timeline": (
                "Structure for handling timeline/delay objections: First, Acknowledge: Validate that timing is important. "
                "Second, Reframe/Evidence: Explain that setting up early prevents deployment bottlenecks and allows immediate qualification. "
                "Third, Check-in: Ask if they have a specific target start date. "
                "Fourth, Advance: Propose scheduling a brief demo or follow-up call."
            )
        }
        return static_playbooks.get(obj_type)

    async def handle_turn(
        self,
        customer_text: str,
        state: SessionState
    ) -> Tuple[str, SessionState]:
        """
        Processes a single conversation turn from the customer.
        Runs the LLM loop, executes any requested tools, runs guardrails, and saves/returns the new state.
        """
        try:
            # Fetch objection playbook if active
            objection_playbook = await self._get_active_objection_playbook(state)

            # 1. Record customer turn in transcript
            turn_id = len(state.transcript) + 1
            state.transcript.append(TranscriptTurn(
                turn_id=turn_id,
                speaker="customer",
                text=customer_text,
                timestamp=datetime.now(timezone.utc).isoformat()
            ))

            # Run escalation policy check
            should_esc, esc_reason, esc_mode = self.escalation_policy.evaluate(
                state=state,
                last_customer_text=customer_text,
                guardrail_failures_this_turn=0
            )
            if should_esc:
                logger.info(f"EscalationPolicy triggered: {esc_reason} (mode: {esc_mode})")
                await self.execute_tool("trigger_escalation", {
                    "call_id": state.call_id,
                    "reason": esc_reason,
                    "mode": esc_mode
                }, state)
                
                if esc_mode == "warm_transfer":
                    esc_msg = "I'll be happy to get a human specialist to assist you further. Let me transfer you now."
                else:
                    esc_msg = "I'll have a senior member of our team follow up with you by email or phone first thing tomorrow to help with these details. Thank you!"
                    
                state.transcript.append(TranscriptTurn(
                    turn_id=len(state.transcript) + 1,
                    speaker="agent",
                    text=esc_msg,
                    timestamp=datetime.now(timezone.utc).isoformat()
                ))
                self.session_manager.save_session(state)
                return esc_msg, state
            
            # 2. Build Anthropic prompt message context
            llm_messages = transcript_to_llm_messages(state.transcript)
            
            all_tool_calls_this_turn = []
            max_loops = 5
            guardrail_failures = 0
            
            for loop_idx in range(max_loops):
                # Query LLM
                assistant_text, tool_calls = await self.dialogue_llm_client.query(
                    system_prompt=self.get_system_prompt(state, objection_playbook=objection_playbook),
                    messages=llm_messages,
                    tools=self.tools_definition
                )
                
                # Scenario A: No tool calls. This is the final text response.
                if not tool_calls:
                    final_text = assistant_text or "I understand."
                    
                    # Check anti-hallucination guardrail
                    is_grounded, reprompt_msg = verify_response_grounding(final_text, all_tool_calls_this_turn, state.executed_tools)
                    if not is_grounded and reprompt_msg:
                        guardrail_failures += 1
                        if guardrail_failures >= self.escalation_policy.guardrail_blocks_threshold:
                            logger.warning("Guardrail blocks threshold reached. Triggering escalation.")
                            esc_mode = self.escalation_policy.determine_escalation_mode(state)
                            await self.execute_tool("trigger_escalation", {
                                "call_id": state.call_id,
                                "reason": "repeated_guardrail_blocks",
                                "mode": esc_mode
                            }, state)
                            
                            if esc_mode == "warm_transfer":
                                esc_msg = "I'll be happy to get a human specialist to assist you further. Let me transfer you now."
                            else:
                                esc_msg = "I'll have a senior member of our team follow up with you by email or phone first thing tomorrow to help with these details. Thank you!"
                                
                            state.transcript.append(TranscriptTurn(
                                turn_id=len(state.transcript) + 1,
                                speaker="agent",
                                text=esc_msg,
                                timestamp=datetime.now(timezone.utc).isoformat()
                            ))
                            self.session_manager.save_session(state)
                            return esc_msg, state

                        # Guardrail failed: Append draft and reprompt to history, then re-query
                        llm_messages.append({"role": "assistant", "content": final_text})
                        llm_messages.append({"role": "user", "content": reprompt_msg})
                        logger.warning("Guardrail violation detected! Requesting model to regenerate...")
                        continue
                        
                    # Guardrail passed: Save final agent response in transcript
                    state.transcript.append(TranscriptTurn(
                        turn_id=len(state.transcript) + 1,
                        speaker="agent",
                        text=final_text,
                        timestamp=datetime.now(timezone.utc).isoformat()
                    ))
                    
                    self.session_manager.save_session(state)
                    return final_text, state
                    
                # Scenario B: Tool calls present. Execute them.
                # Format OpenAI-compatible assistant message containing tool calls
                openai_tool_calls = []
                for tc in tool_calls:
                    openai_tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"])
                        }
                    })
                
                assistant_msg = {"role": "assistant"}
                if assistant_text:
                    assistant_msg["content"] = assistant_text
                else:
                    assistant_msg["content"] = None
                assistant_msg["tool_calls"] = openai_tool_calls
                llm_messages.append(assistant_msg)
                
                # Execute tools and append outcomes
                for tc in tool_calls:
                    all_tool_calls_this_turn.append(tc)
                    # Shield tool execution to prevent partial updates and data loss on barge-in
                    result = await asyncio.shield(self.execute_tool(tc["name"], tc["input"], state))
                    
                    # If tool succeeded, record in history
                    if isinstance(result, dict) and "error" not in result:
                        if tc["name"] not in state.executed_tools:
                            state.executed_tools.append(tc["name"])
                            
                    llm_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "content": json.dumps(result)
                    })
                
            # Fallback if loop exceeded max execution turns
            fallback_msg = "Let me make sure I have all the correct info. How can I help you next?"
            state.transcript.append(TranscriptTurn(
                turn_id=len(state.transcript) + 1,
                speaker="agent",
                text=fallback_msg,
                timestamp=datetime.now(timezone.utc).isoformat()
            ))
            self.session_manager.save_session(state)
            return fallback_msg, state
        except asyncio.CancelledError:
            logger.info("handle_turn was cancelled mid-flight. Saving current session state to DB.")
            self.session_manager.save_session(state)
            raise

    async def execute_tool(
        self,
        name: str,
        args: Dict[str, Any],
        state: SessionState
    ) -> Dict[str, Any]:
        """Routes and executes a specific tool, updating lead details and local SessionState on the fly."""
        try:
            if name == "search_product_kb":
                query = args.get("query", "")
                doc_type = args.get("type")
                docs = await self.kb_search.search_product_kb(query=query, doc_type=doc_type)
                return {
                    "documents": [
                        {"title": doc.title, "content": doc.content, "type": doc.type}
                        for doc in docs
                    ]
                }
                
            elif name == "get_pricing_quote":
                team_size = args.get("team_size", 0)
                tier = self.pricing_service.get_pricing(team_size)
                if tier:
                    return {
                        "tier_id": tier.tier_id,
                        "name": tier.name,
                        "price_per_seat_monthly": tier.price_per_seat_monthly,
                        "included_features": tier.included_features,
                        "onboarding_fee": tier.onboarding_fee,
                        "active_promotions": [
                            {"description": p.description, "discount_pct": p.discount_pct}
                            for p in tier.active_promotions
                        ]
                    }
                return {"error": f"No pricing tier found for team size {team_size}"}
                
            elif name == "get_lead":
                phone_or_id = args.get("phone_or_id", "")
                lead = self.crm_adapter.get_lead(phone_or_id)
                if lead:
                    # Update state caller details
                    state.caller["crm_lead_id"] = lead.lead_id
                    state.caller["known_from_crm"] = True
                    return lead.model_dump()
                return {"message": "No lead found"}
                
            elif name == "update_lead_qualification":
                lead_id = args.get("lead_id")
                fields_dict = args.get("fields", {})
                
                # Construct Pydantic QualificationData with current turn tracking
                turn_num = len(state.transcript)
                rebuilt_qual = QualificationData()
                for key, val in fields_dict.items():
                    if val is not None:
                        qual_val = QualificationValue(
                            value=val,
                            last_updated_turn=turn_num,
                            source="stated"
                        )
                        setattr(rebuilt_qual, key, qual_val)
                        # Also sync local session state directly
                        setattr(state.qualification, key, qual_val)
                        
                lead = self.crm_adapter.update_lead_qualification(lead_id, rebuilt_qual)
                return {"status": "success", "lead_id": lead.lead_id, "updated_fields": list(fields_dict.keys())}
                
            elif name == "log_call_event":
                call_id = args.get("call_id")
                event_type = args.get("event_type")
                detail = args.get("detail", {})
                lead_id = state.caller.get("crm_lead_id") or "lead_unknown"
                
                self.crm_adapter.log_call_event(call_id, lead_id, event_type, detail)
                
                # Check if this logs an objection event
                turn_num = len(state.transcript)
                if event_type == "objection_raised":
                    obj_type = detail.get("type", "pricing")
                    # Avoid duplicate active objections of same type
                    if not any(o.type == obj_type and not o.resolved for o in state.objections):
                        state.objections.append(ObjectionRecord(
                            type=obj_type,
                            raised_at_turn=turn_num,
                            detail=detail.get("detail", ""),
                            strategy_used=detail.get("strategy_used", ""),
                            resolved=False
                        ))
                elif event_type == "objection_resolved":
                    obj_type = detail.get("type", "pricing")
                    for obj in state.objections:
                        if obj.type == obj_type and not obj.resolved:
                            obj.resolved = True
                            obj.resolved_at_turn = turn_num
                            
                return {"status": "success"}
                
            elif name == "get_calendar_availability":
                window_start = args.get("window_start")
                window_end = args.get("window_end")
                meeting_type = args.get("meeting_type")
                slots = self.calendar_adapter.get_calendar_availability(window_start, window_end, meeting_type)
                return {"available_slots": [s.model_dump() if hasattr(s, "model_dump") else s for s in slots]}
                
            elif name == "book_meeting":
                lead_id = args.get("lead_id")
                slot_start = args.get("slot_start")
                slot_end = args.get("slot_end")
                meeting_type = args.get("meeting_type")
                
                booking = self.calendar_adapter.book_meeting(
                    lead_id=lead_id,
                    slot_start=slot_start,
                    slot_end=slot_end,
                    meeting_type=meeting_type,
                    notes="Attendees: customer@example.com"
                )
                state.outcome = "meeting_booked"
                return booking.model_dump() if hasattr(booking, "model_dump") else booking
                
            elif name == "create_follow_up_task":
                lead_id = args.get("lead_id")
                reason = args.get("reason")
                priority = args.get("priority")
                due_at = args.get("due_at", "")
                context_summary = args.get("context_summary")
                
                if not due_at:
                    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
                    
                task = FollowUpTask(
                    task_id=f"tsk_{int(datetime.now(timezone.utc).timestamp())}",
                    lead_id=lead_id,
                    reason=reason,
                    priority=priority,
                    due_at=due_at,
                    context_summary=context_summary,
                    full_transcript_url="http://example.com/transcripts"
                )
                self.crm_adapter.create_follow_up_task(lead_id, task)
                state.outcome = "follow_up_scheduled"
                return task.model_dump()
                
            elif name == "trigger_escalation":
                call_id = args.get("call_id")
                reason = args.get("reason")
                mode = args.get("mode")
                
                briefing_card = {
                    "call_id": call_id,
                    "lead_id": state.caller.get("crm_lead_id"),
                    "reason": reason,
                    "qualification": state.qualification.model_dump(),
                    "objections": [obj.model_dump() for obj in state.objections]
                }
                
                if mode == "warm_transfer":
                    self.telephony_adapter.initiate_warm_transfer(
                        call_id=call_id,
                        human_phone_or_sip="sip:human_agent@echosphere",
                        briefing_card=briefing_card
                    )
                elif mode == "async_handoff":
                    priority = "high"
                    if state.qualification.team_size and state.qualification.team_size.value and state.qualification.team_size.value >= 50:
                        priority = "urgent"
                    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
                    
                    transcript_summary = "\n".join([f"{t.speaker}: {t.text}" for t in state.transcript[-10:]])
                    context_summary = (
                        f"Escalation Reason: {reason}. "
                        f"Qualification: {state.qualification.model_dump()}. "
                        f"Objections: {[o.model_dump() for o in state.objections]}. "
                        f"Recent turns:\n{transcript_summary}"
                    )
                    
                    task = FollowUpTask(
                        task_id=f"tsk_{int(datetime.now(timezone.utc).timestamp())}",
                        lead_id=state.caller.get("crm_lead_id") or "lead_unknown",
                        reason=reason,
                        priority=priority,
                        due_at=due_at,
                        context_summary=context_summary,
                        full_transcript_url="http://example.com/transcripts"
                    )
                    self.crm_adapter.create_follow_up_task(state.caller.get("crm_lead_id") or "lead_unknown", task)
                
                state.escalation = SessionEscalationState(
                    triggered=True,
                    reason=reason,
                    mode=mode,
                    triggered_at_turn=len(state.transcript)
                )
                state.outcome = "escalated"
                return {"status": "escalated", "mode": mode}
                
            return {"error": f"Tool '{name}' is not supported."}
        except Exception as err:
            logger.error(f"Error executing tool '{name}': {err}")
            return {"error": str(err)}

    def interrupt_agent_turn(self, state: SessionState, actual_spoken_text: str):
        """
        Interrupts the active agent turn, truncating the spoken response
        and flagging it as interrupted in the transcript history.
        """
        if state.transcript and state.transcript[-1].speaker == "agent":
            state.transcript[-1].text = actual_spoken_text
            state.transcript[-1].interrupted = True
            self.session_manager.save_session(state)
            logger.info("Agent turn truncated and marked as interrupted.")

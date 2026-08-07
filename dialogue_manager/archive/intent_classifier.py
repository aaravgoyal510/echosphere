import os
import json
import logging
import httpx
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from dialogue_manager.models import SessionState, QualificationData, TranscriptTurn

logger = logging.getLogger(__name__)

class IntentClassifier:
    def __init__(self, model_name: Optional[str] = None):
        # Load environment variables
        load_dotenv(override=True)
        self.api_key = os.getenv("OLLAMA_API_KEY", "")
        self.model = model_name or os.getenv("OLLAMA_MODEL", "gemma4:31b-cloud")
        self.url = "http://localhost:11434/api/chat"

    def classify_turn(
        self,
        current_turn: str,
        history: List[TranscriptTurn],
        qualification_snapshot: QualificationData
    ) -> Dict[str, Any]:
        """
        Runs the parallel intent classification and structured data extraction call.
        Utilizes Ollama Cloud via the local proxy.
        Includes a strict 1.5-second timeout and fallback mechanism to ensure
        uninterrupted conversational flow during network delays or service hiccups.
        """
        # Format the context (last ~6 turns)
        context_turns = history[-6:]
        context_str = ""
        for turn in context_turns:
            context_str += f"{turn.speaker.upper()}: {turn.text}\n"

        # Format current qualification state
        qual_dict = {}
        if qualification_snapshot.team_size:
            qual_dict["team_size"] = qualification_snapshot.team_size.value
        if qualification_snapshot.budget_signal:
            qual_dict["budget_signal"] = qualification_snapshot.budget_signal.value
        if qualification_snapshot.current_solution:
            qual_dict["current_solution"] = qualification_snapshot.current_solution.value
        if qualification_snapshot.decision_maker:
            qual_dict["decision_maker"] = qualification_snapshot.decision_maker.value
        if qualification_snapshot.timeline:
            qual_dict["timeline"] = qualification_snapshot.timeline.value
        if qualification_snapshot.use_case:
            qual_dict["use_case"] = qualification_snapshot.use_case.value

        system_prompt = (
            "You are a real-time sales qualification intent classifier and entity extractor.\n"
            "Analyze the conversation history, the current qualification state, and the latest customer turn.\n"
            "Determine the customer's intent(s), identify any updates to qualification fields (team_size, budget_signal, "
            "current_solution, decision_maker, timeline, use_case), check for objections raised, and determine if escalation is needed.\n\n"
            "Valid intents include: pricing, competitor, calendar, other, requirement_change, objection:pricing, objection:competitor, objection:trust, objection:timing, objection:authority.\n\n"
            "You MUST output raw valid JSON matching this exact structure, with NO markdown formatting, backticks, or extra text:\n"
            "{\n"
            "  \"intents\": [\"intent1\", \"intent2\"],\n"
            "  \"qualification_updates\": {\"team_size\": value_or_null},\n"
            "  \"objection\": {\"type\": \"objection_type_or_null\", \"detail\": \"objection_detail_or_null\", \"raised\": true_or_false},\n"
            "  \"topic_return_reference\": null,\n"
            "  \"escalation_signal_strength\": 0.0\n"
            "}"
        )

        user_content = (
            f"Current Qualification State: {json.dumps(qual_dict)}\n"
            f"Conversation History:\n{context_str}\n"
            f"Latest Customer Utterance: {current_turn}\n"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "format": "json",
            "stream": False,
            "options": {
                "num_predict": 128,
                "temperature": 0.0,
                "num_ctx": 2048
            }
        }

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Defensive fallback response
        fallback_response = {
            "intents": [],
            "qualification_updates": {},
            "objection": {"type": None, "detail": None, "raised": False},
            "topic_return_reference": None,
            "escalation_signal_strength": 0.0
        }

        try:
            # Set a hard timeout of 1.5 seconds to protect the critical loop latency
            with httpx.Client(timeout=1.5) as client:
                response = client.post(self.url, json=payload, headers=headers)
                
                if response.status_code == 200:
                    res_json = response.json()
                    content_text = res_json.get("message", {}).get("content", "").strip()
                    
                    if content_text.startswith("```"):
                        lines = content_text.splitlines()
                        if lines[0].startswith("```json") or lines[0].startswith("```"):
                            content_text = "\n".join(lines[1:-1]).strip()
                    
                    return json.loads(content_text)
                else:
                    logger.warning(
                        f"IntentClassifier: received bad status code {response.status_code}. "
                        "Using fallback logic."
                    )
                    return fallback_response
        except httpx.TimeoutException:
            logger.warning(
                "IntentClassifier: network call timed out (> 1.5s). "
                "Gracefully falling back to preserve dialogue flow."
            )
            return fallback_response
        except Exception as e:
            logger.warning(
                f"IntentClassifier: unexpected error {e}. Using fallback logic."
            )
            return fallback_response

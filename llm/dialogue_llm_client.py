import os
import logging
import asyncio
import json
from typing import List, Dict, Any, Optional, Tuple
import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class DialogueLLMClient:
    """
    Client wrapper that routes all chat queries to AICredits.in,
    an OpenAI-compatible paid gateway.
    """
    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        load_dotenv(override=True)
        self.last_headers = {}
        
        self.api_key = api_key or os.getenv("AICREDITS_API_KEY")
        self.model = model_name or os.getenv("AICREDITS_MODEL", "openai/gpt-4o-mini")
        self.url = "https://api.aicredits.in/v1/chat/completions"
        self.provider = "AICredits"

        logger.info(f"Initialized LLM client. Endpoint: {self.provider} ({self.model})")

    async def query(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        max_retries: int = 2,
        backoff_factor: float = 1.0
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """
        Queries the AICredits paid gateway.
        Handles wallet credit exhaustion (402) immediately.
        """
        if not self.api_key:
            raise ValueError("AICREDITS_API_KEY is not configured in .env.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        openai_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            openai_messages.append(msg)

        payload = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        if tools:
            payload["tools"] = tools

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Querying {self.provider} ({attempt}/{max_retries})...")
                async with httpx.AsyncClient() as client:
                    response = await client.post(self.url, json=payload, headers=headers, timeout=12.0)
                    self.last_headers = dict(response.headers)
                    
                    if response.status_code == 200:
                        return self._parse_openai_response(response.json())
                        
                    elif response.status_code == 402:
                        logger.error("AICredits wallet balance insufficient, top up needed.")
                        raise RuntimeError("AICredits wallet balance insufficient, top up needed.")
                        
                    elif response.status_code == 429:
                        logger.warning(f"AICredits rate limit exceeded on attempt {attempt}.")
                        if attempt == max_retries:
                            raise RuntimeError("AICredits rate limit exceeded. Check usage tier.")
                            
                    else:
                        # Fallback check for credit/wallet exhaustion in text responses
                        res_text = response.text.lower()
                        if "insufficient" in res_text and ("credit" in res_text or "balance" in res_text or "wallet" in res_text):
                            logger.error("AICredits wallet balance insufficient, top up needed.")
                            raise RuntimeError("AICredits wallet balance insufficient, top up needed.")
                            
                        logger.warning(f"AICredits API failed (Status {response.status_code}): {response.text}")
                        if attempt == max_retries:
                            raise RuntimeError(f"AICredits API failed (Status {response.status_code}): {response.text}")
                            
            except asyncio.CancelledError:
                logger.info("LLM query cancelled mid-flight.")
                raise
            except RuntimeError as e:
                # Instantly raise and don't retry if the wallet is empty
                if "wallet balance insufficient" in str(e):
                    raise e
                if attempt == max_retries:
                    raise e
            except Exception as e:
                logger.warning(f"Error querying {self.provider} on attempt {attempt}: {e}")
                if attempt == max_retries:
                    raise e
            
            # Paced backoff: 1s, 2s
            await asyncio.sleep(attempt * backoff_factor)

        return None, []

    def _parse_openai_response(self, res_json: Dict[str, Any]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Parses OpenAI-compatible response JSON for assistant text and tool calls."""
        choice = res_json["choices"][0]["message"]
        assistant_text = choice.get("content")
        
        tool_calls = []
        raw_tool_calls = choice.get("tool_calls", [])
        for tc in raw_tool_calls:
            func = tc["function"]
            try:
                args = json.loads(func["arguments"])
            except Exception:
                args = func["arguments"]
                
            tool_calls.append({
                "id": tc["id"],
                "name": func["name"],
                "input": args
            })
            
        return assistant_text, tool_calls

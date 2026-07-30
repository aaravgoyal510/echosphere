import os
import logging
import asyncio
import json
from typing import List, Dict, Any, Optional, Tuple
import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class ClaudeClient:
    """
    Client wrapper that dynamically routes chat queries to either Groq or GitHub Models
    based on environment variables, using an OpenAI-compatible REST completions endpoint.
    
    Includes a local fallback path to Ollama (llama3.2:1b) if the primary provider
    returns a 429 Rate Limit Exceeded or encounters a connection error.
    """
    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        load_dotenv(override=True)
        self.last_headers = {}
        
        self.use_groq = os.getenv("USE_GROQ", "false").lower() == "true"
        
        # Primary Config
        if self.use_groq:
            self.provider = "Groq"
            self.api_key = api_key or os.getenv("GROQ_API_KEY")
            self.model = model_name or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            self.url = "https://api.groq.com/openai/v1/chat/completions"
        else:
            self.provider = "GitHub Models"
            self.api_key = api_key or os.getenv("GITHUB_TOKEN")
            self.model = model_name or os.getenv("GITHUB_MODEL", "gpt-4o-mini")
            self.url = "https://models.inference.ai.azure.com/chat/completions"

        # Local Fallback Config
        self.fallback_provider = "Local Ollama"
        self.fallback_url = "http://localhost:11434/v1/chat/completions"
        self.fallback_model = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")

        logger.info(f"Initialized LLM client. Primary: {self.provider} ({self.model}) | Fallback: {self.fallback_provider} ({self.fallback_model})")

    async def query(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        max_retries: int = 3,
        backoff_factor: float = 1.5
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """
        Queries the primary LLM provider.
        If it encounters a 429 or connection failure, falls back to local Ollama.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key or ''}",
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

        # Try Primary Provider
        try:
            if not self.api_key:
                token_var = "GROQ_API_KEY" if self.use_groq else "GITHUB_TOKEN"
                raise ValueError(f"Primary provider key {token_var} not configured. Tripping fallback.")

            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(f"Querying {self.provider} ({attempt}/{max_retries})...")
                    async with httpx.AsyncClient() as client:
                        response = await client.post(self.url, json=payload, headers=headers, timeout=12.0)
                        self.last_headers = dict(response.headers)
                        
                        if response.status_code == 200:
                            return self._parse_openai_response(response.json())
                        elif response.status_code == 429:
                            logger.warning(f"[429] {self.provider} Rate Limit Exceeded. Falling back immediately...")
                            break
                        else:
                            logger.warning(f"{self.provider} API failed (Status {response.status_code}): {response.text}")
                            if attempt == max_retries:
                                raise RuntimeError(f"{self.provider} failed: {response.text}")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"Error querying {self.provider} on attempt {attempt}: {e}")
                    if attempt == max_retries:
                        raise e
                
                await asyncio.sleep(backoff_factor ** attempt)
                
        except asyncio.CancelledError:
            logger.info(f"LLM query cancelled mid-flight.")
            raise
        except Exception as e:
            logger.warning(f"Primary provider {self.provider} failed completely: {e}. Attempting local fallback...")

        # If primary failed or hit 429, call Local Ollama
        logger.info(f"Routing query to local fallback: {self.fallback_provider} ({self.fallback_model})...")
        try:
            fallback_payload = {
                "model": self.fallback_model,
                "messages": openai_messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            # Only send tools if the local model is expected to handle them (note: llama3.2:1b is too small for robust tool-calling, but we pass it just in case)
            if tools:
                fallback_payload["tools"] = tools

            async with httpx.AsyncClient() as client:
                response = await client.post(self.fallback_url, json=fallback_payload, timeout=45.0)
                if response.status_code == 200:
                    logger.info(f"Local fallback response returned successfully from {self.fallback_model}.")
                    return self._parse_openai_response(response.json())
                else:
                    raise RuntimeError(f"Ollama API failed (Status {response.status_code}): {response.text}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Local fallback to Ollama failed: {e}")
            raise RuntimeError(f"All LLM providers exhausted. Primary error: {e}")

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

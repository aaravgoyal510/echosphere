import os
import json
import time
import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# 1. Open-AI / GitHub Models Tool Schema Definitions (lowercase types, type: function structure)
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_product_kb",
            "description": "Query the product knowledge base (vector similarity search) for competitive positioning battlecards, onboarding fee policies, and standard FAQs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query (e.g. competitor name)"},
                    "type": {
                        "type": "string",
                        "enum": ["feature_doc", "competitive_battlecard", "policy", "faq", "case_study"]
                    }
                },
                "required": ["query", "type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_pricing_quote",
            "description": "Get the monthly pricing quote and promotions for a given team size.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team_size": {"type": "integer", "description": "The number of seats"}
                },
                "required": ["team_size"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_lead_qualification",
            "description": "Write qualification updates to the CRM record (team size, competitor, timeline, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string"},
                    "fields": {
                        "type": "object",
                        "properties": {
                            "team_size": {"type": "integer"},
                            "current_solution": {"type": "string"},
                            "timeline": {"type": "string"}
                        }
                    }
                },
                "required": ["lead_id", "fields"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "log_call_event",
            "description": "Log an event (objection raised/resolved, topic covered) mid-call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {"type": "string"},
                    "event_type": {"type": "string"},
                    "detail": {"type": "object"}
                },
                "required": ["call_id", "event_type"]
            }
        }
    }
]

# 2. Gemini Tool Schema Definitions (uppercase parameter types under functionDeclarations)
GEMINI_TOOLS = [
    {
        "functionDeclarations": [
            {
                "name": "search_product_kb",
                "description": "Query the product knowledge base (vector similarity search) for competitive positioning battlecards, onboarding fee policies, and standard FAQs.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {"type": "STRING", "description": "The search query (e.g. competitor name)"},
                        "type": {
                            "type": "STRING",
                            "enum": ["feature_doc", "competitive_battlecard", "policy", "faq", "case_study"]
                        }
                    },
                    "required": ["query", "type"]
                }
            },
            {
                "name": "get_pricing_quote",
                "description": "Get the monthly pricing quote and promotions for a given team size.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "team_size": {"type": "INTEGER", "description": "The number of seats"}
                    },
                    "required": ["team_size"]
                }
            },
            {
                "name": "update_lead_qualification",
                "description": "Write qualification updates to the CRM record (team size, competitor, timeline, etc.).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "lead_id": {"type": "STRING"},
                        "fields": {
                            "type": "OBJECT",
                            "properties": {
                                "team_size": {"type": "INTEGER"},
                                "current_solution": {"type": "STRING"},
                                "timeline": {"type": "STRING"}
                            }
                        }
                    },
                    "required": ["lead_id", "fields"]
                }
            },
            {
                "name": "log_call_event",
                "description": "Log an event (objection raised/resolved, topic covered) mid-call.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "call_id": {"type": "STRING"},
                        "event_type": {"type": "STRING"},
                        "detail": {"type": "OBJECT"}
                    },
                    "required": ["call_id", "event_type"]
                }
            }
        ]
    }
]

SYSTEM_PROMPT = (
    "You are Aria, a phone sales agent. You have tools available. "
    "You MUST execute tools when information (like pricing, competitors, or team size updates) is asked or mentioned. "
    "Use update_lead_qualification with lead_id='lead_123' and log_call_event with call_id='call_999'."
)

TEST_SCENARIOS = [
    ("Scenario A (Pricing)", "Hi, how much does your plan cost for a team of 30 seats?"),
    ("Scenario B (Competitor)", "We are evaluating Salesforce, how do you compare?"),
    ("Scenario C (Team size update)", "Our team is actually 120 seats now."),
    ("Scenario D (No tool)", "That sounds great, thank you.")
]

def test_github_tool_calling(client, model_name="gpt-4o-mini"):
    print(f"\n=========================================")
    print(f"BENCHMARKING GITHUB: {model_name} (Tool Calling)")
    print("=========================================")
    if not GITHUB_TOKEN:
        print("Skipping: GITHUB_TOKEN not set.")
        return
        
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Content-Type": "application/json"}
    url = "https://models.inference.ai.azure.com/chat/completions"
    
    for label, text in TEST_SCENARIOS:
        print(f"\n* TESTING: {label} -> Input: '{text}'")
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            "tools": OPENAI_TOOLS,
            "temperature": 0.0
        }
        
        start = time.perf_counter()
        try:
            res = client.post(url, json=payload, headers=headers, timeout=10.0)
            wall_ms = (time.perf_counter() - start) * 1000
            if res.status_code == 200:
                data = res.json()
                choice = data["choices"][0]["message"]
                text_out = choice.get("content")
                tool_calls = choice.get("tool_calls", [])
                
                print(f"  Took: {wall_ms:.1f}ms")
                if text_out:
                    print(f"  Spoken Output: \"{text_out.strip()}\"")
                if tool_calls:
                    print("  Tool Calls Detected:")
                    for tc in tool_calls:
                        func = tc["function"]
                        print(f"    - Name: {func['name']} | Args: {func['arguments']}")
                else:
                    print("  Tool Calls Detected: NONE")
            else:
                print(f"  Failed status {res.status_code}: {res.text}")
        except Exception as e:
            print(f"  Exception: {e}")

def test_gemini_tool_calling(client, model_name="gemini-2.0-flash"):
    print(f"\n=========================================")
    print(f"BENCHMARKING GEMINI: {model_name} (Tool Calling)")
    print("=========================================")
    if not GEMINI_API_KEY:
        print("Skipping: GEMINI_API_KEY not set.")
        return
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    
    for label, text in TEST_SCENARIOS:
        print(f"\n* TESTING: {label} -> Input: '{text}'")
        
        # Format for Gemini v1beta REST API
        payload = {
            "contents": [
                {
                    "parts": [{"text": text}]
                }
            ],
            "systemInstruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "tools": GEMINI_TOOLS,
            "generationConfig": {
                "temperature": 0.0
            }
        }
        
        start = time.perf_counter()
        try:
            res = client.post(url, json=payload, timeout=10.0)
            wall_ms = (time.perf_counter() - start) * 1000
            if res.status_code == 200:
                data = res.json()
                print(f"  Took: {wall_ms:.1f}ms")
                
                # Check response parts
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    has_tool = False
                    for part in parts:
                        if "text" in part:
                            print(f"  Spoken Output: \"{part['text'].strip()}\"")
                        if "functionCall" in part:
                            has_tool = True
                            func = part["functionCall"]
                            print(f"    - Name: {func['name']} | Args: {func.get('args')}")
                    if not has_tool:
                        print("  Tool Calls Detected: NONE")
                else:
                    print("  Response contained no candidates.")
            else:
                print(f"  Failed status {res.status_code}: {res.text}")
        except Exception as e:
            print(f"  Exception: {e}")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

def test_groq_tool_calling(client, model_name="llama-3.3-70b-versatile"):
    print(f"\n=========================================")
    print(f"BENCHMARKING GROQ: {model_name} (Tool Calling)")
    print("=========================================")
    if not GROQ_API_KEY:
        print("Skipping: GROQ_API_KEY not set.")
        return
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    for label, text in TEST_SCENARIOS:
        print(f"\n* TESTING: {label} -> Input: '{text}'")
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            "tools": OPENAI_TOOLS,
            "temperature": 0.0
        }
        
        start = time.perf_counter()
        try:
            res = client.post(url, json=payload, headers=headers, timeout=10.0)
            wall_ms = (time.perf_counter() - start) * 1000
            if res.status_code == 200:
                data = res.json()
                choice = data["choices"][0]["message"]
                print(f"  Took: {wall_ms:.1f}ms")
                text_out = choice.get("content")
                if text_out:
                    print(f"  Spoken Output: \"{text_out.strip()}\"")
                tool_calls = choice.get("tool_calls", [])
                if tool_calls:
                    print("  Tool Calls Detected:")
                    for tc in tool_calls:
                        func = tc["function"]
                        print(f"    - Name: {func['name']} | Args: {func['arguments']}")
                else:
                    print("  Tool Calls Detected: NONE")
            else:
                print(f"  Failed status {res.status_code}: {res.text}")
        except Exception as e:
            print(f"  Exception: {e}")

if __name__ == "__main__":
    with httpx.Client() as client:
        # Run Groq
        test_groq_tool_calling(client, "llama-3.3-70b-versatile")
        # Run Gemini
        test_gemini_tool_calling(client, "gemini-2.0-flash")
        # Run GitHub
        test_github_tool_calling(client, "gpt-4o-mini")

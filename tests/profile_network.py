import os
import time
import json
import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Target endpoints
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
GITHUB_MODELS_URL = "https://models.inference.ai.azure.com/chat/completions"

SYSTEM_PROMPT = (
    "You are a real-time sales qualification intent classifier and entity extractor. "
    "Analyze the latest customer utterance and extract structural information.\n\n"
    "You MUST output raw valid JSON matching this exact structure, with NO markdown formatting, backticks, or extra text:\n"
    "{\n"
    "  \"intents\": [\"intent1\", \"intent2\"],\n"
    "  \"qualification_updates\": {\"team_size\": integer_value_or_null},\n"
    "  \"objection\": {\"type\": \"objection_type_or_null\", \"detail\": \"objection_detail_or_null\", \"raised\": true_or_false},\n"
    "  \"topic_return_reference\": null,\n"
    "  \"escalation_signal_strength\": 0.0\n"
    "}\n\n"
    "Intents: pricing, competitor, calendar, other, objection:pricing, objection:competitor.\n"
    "Objection Types: pricing, competitor, trust, timing, authority."
)

TEST_UTTERANCES = {
    "pricing": "Can you tell me how much it costs for about fifty people?",
    "competitor": "We are evaluating HubSpot, how are you different?",
    "team_size": "Actually, our team size is 120 seats now.",
    "off_topic": "What is your favorite color?"
}

def benchmark_gemini(client, model_name):
    print(f"\n=========================================")
    print(f"BENCHMARKING GEMINI: {model_name}")
    print("=========================================")
    if not GEMINI_API_KEY:
        print("Skipping: GEMINI_API_KEY not found in environment.")
        return
    
    url = GEMINI_URL_TEMPLATE.format(model=model_name, key=GEMINI_API_KEY)
    
    # 1. Latency run
    print("\n--- Latency Test (5 Sequential Calls) ---")
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": TEST_UTTERANCES["pricing"]}
                ]
            }
        ],
        "systemInstruction": {
            "parts": [
                {"text": SYSTEM_PROMPT}
            ]
        },
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0,
            "maxOutputTokens": 128
        }
    }
    
    success_count = 0
    for i in range(1, 6):
        start = time.perf_counter()
        try:
            response = client.post(url, json=payload, timeout=10.0)
            wall_ms = (time.perf_counter() - start) * 1000
            if response.status_code == 200:
                print(f"  Call #{i}: Wall-Clock = {wall_ms:.1f}ms")
                success_count += 1
            else:
                print(f"  Call #{i}: Failed with status {response.status_code}: {response.text}")
        except Exception as e:
            print(f"  Call #{i}: Exception: {e}")
            
    if success_count == 0:
        print(f"Skipping accuracy checks for Gemini {model_name} (unsupported/failed).")
        return

    # 2. Accuracy run
    print("\n--- Accuracy Test (Utterance Evaluations) ---")
    for label, text in TEST_UTTERANCES.items():
        payload_acc = {
            "contents": [
                {
                    "parts": [
                        {"text": f"Customer says: '{text}'"}
                    ]
                }
            ],
            "systemInstruction": {
                "parts": [
                    {"text": SYSTEM_PROMPT}
                ]
            },
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.0,
                "maxOutputTokens": 128
            }
        }
        try:
            start_acc = time.perf_counter()
            response = client.post(url, json=payload_acc, timeout=10.0)
            wall_ms = (time.perf_counter() - start_acc) * 1000
            if response.status_code == 200:
                res_data = response.json()
                content = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                print(f"\n* UTTERANCE: '{text}' (took {wall_ms:.1f}ms)")
                print("  Output Content:")
                print(content)
                try:
                    json.loads(content)
                    print("  Status: VALID JSON")
                except Exception as json_err:
                    print(f"  Status: INVALID JSON: {json_err}")
            else:
                print(f"* UTTERANCE: '{text}' -> FAILED status {response.status_code}")
        except Exception as e:
            print(f"* UTTERANCE: '{text}' -> EXCEPTION: {e}")

def benchmark_github(client, model_name):
    print(f"\n=========================================")
    print(f"BENCHMARKING GITHUB MODELS: {model_name}")
    print("=========================================")
    if not GITHUB_TOKEN:
        print("Skipping: GITHUB_TOKEN not found in environment.")
        return
        
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": TEST_UTTERANCES["pricing"]}
        ],
        "temperature": 0.0,
        "max_tokens": 128,
        "response_format": {"type": "json_object"}
    }
    
    # 1. Latency run
    print("\n--- Latency Test (5 Sequential Calls) ---")
    for i in range(1, 6):
        start = time.perf_counter()
        try:
            response = client.post(GITHUB_MODELS_URL, json=payload, headers=headers, timeout=10.0)
            wall_ms = (time.perf_counter() - start) * 1000
            if response.status_code == 200:
                print(f"  Call #{i}: Wall-Clock = {wall_ms:.1f}ms")
            else:
                print(f"  Call #{i}: Failed with status {response.status_code}: {response.text}")
        except Exception as e:
            print(f"  Call #{i}: Exception: {e}")
            
    # 2. Accuracy run
    print("\n--- Accuracy Test (Utterance Evaluations) ---")
    for label, text in TEST_UTTERANCES.items():
        payload_acc = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Customer says: '{text}'"}
            ],
            "temperature": 0.0,
            "max_tokens": 128,
            "response_format": {"type": "json_object"}
        }
        try:
            start_acc = time.perf_counter()
            response = client.post(GITHUB_MODELS_URL, json=payload_acc, headers=headers, timeout=10.0)
            wall_ms = (time.perf_counter() - start_acc) * 1000
            if response.status_code == 200:
                res_data = response.json()
                content = res_data["choices"][0]["message"]["content"].strip()
                print(f"\n* UTTERANCE: '{text}' (took {wall_ms:.1f}ms)")
                print("  Output Content:")
                print(content)
                try:
                    json.loads(content)
                    print("  Status: VALID JSON")
                except Exception as json_err:
                    print(f"  Status: INVALID JSON: {json_err}")
            else:
                print(f"* UTTERANCE: '{text}' -> FAILED status {response.status_code}")
        except Exception as e:
            print(f"* UTTERANCE: '{text}' -> EXCEPTION: {e}")

if __name__ == "__main__":
    with httpx.Client() as client:
        # Benchmark Gemini Models
        benchmark_gemini(client, "gemini-1.5-flash")
        benchmark_gemini(client, "gemini-1.5-flash-8b")
        benchmark_gemini(client, "gemini-2.0-flash")
        
        # Benchmark GitHub Models
        benchmark_github(client, "gpt-4o-mini")
        benchmark_github(client, "phi-4")

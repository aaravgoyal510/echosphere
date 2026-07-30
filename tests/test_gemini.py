import os
import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

def test_gemini():
    print("Testing Google AI Studio API key...")
    print(f"Key starts with: {GEMINI_API_KEY[:6]}... (Length: {len(GEMINI_API_KEY)})")
    
    if not GEMINI_API_KEY.startswith("AIzaSy"):
        print("[WARNING] Key does not start with 'AIzaSy'. It is likely invalid for AI Studio.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "Reply with only the word: SUCCESS"}
                ]
            }
        ]
    }
    
    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        if response.status_code == 200:
            res_data = response.json()
            try:
                text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                print(f"API Response: {text}")
                if text == "SUCCESS":
                    print("--> GEMINI API KEY CONFIRMED WORKING!")
                else:
                    print("--> Received unexpected response, but connection succeeded.")
            except Exception as parse_err:
                print(f"--> Connection succeeded but response structure was unexpected: {parse_err}")
                print("Response JSON:", res_data)
        else:
            print(f"--> API Call Failed with status {response.status_code}")
            print("Error Details:", response.text)
    except Exception as e:
        print(f"--> Connection exception: {e}")

if __name__ == "__main__":
    test_gemini()

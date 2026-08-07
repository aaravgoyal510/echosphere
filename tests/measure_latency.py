import asyncio
import time
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llm.dialogue_llm_client import DialogueLLMClient

async def run_benchmark():
    client = DialogueLLMClient()
    
    user_message = [{"role": "user", "content": "Actually, we are comparing you to HubSpot. How are you different?"}]
    
    base_prompt = (
        "You are Aria, an AI sales agent for Echosphere. Speak naturally, keeping responses concise (1-3 sentences). "
        "Do NOT use markdown bolding, lists, or formatting."
    )
    
    playbook_text = (
        "Structure for handling competitor comparisons: First, Acknowledge: Validate the customer's choice to compare options. "
        "Second, Reframe/Evidence: Introduce our key differentiators: natural turn-taking with low latency, real-time barge-in, "
        "and custom onboarding fee waivers for large teams. Third, Check-in: Ask if they would like to explore these features. "
        "Fourth, Advance: Suggest scheduling a demo to see the direct CRM integrations."
    )
    playbook_prompt = f"{base_prompt}\n\n### Objection Playbook Guidance (MUST follow this strategy):\n{playbook_text}"
    
    print("Running benchmark turns against AICredits gateway...\n")
    
    without_times = []
    for i in range(5):
        start = time.perf_counter()
        await client.query(system_prompt=base_prompt, messages=user_message)
        dur = (time.perf_counter() - start) * 1000.0
        without_times.append(dur)
        print(f"[No Playbook] Turn {i+1}: {dur:.1f}ms")
        await asyncio.sleep(1.0)
        
    print("")
    
    with_times = []
    for i in range(5):
        start = time.perf_counter()
        await client.query(system_prompt=playbook_prompt, messages=user_message)
        dur = (time.perf_counter() - start) * 1000.0
        with_times.append(dur)
        print(f"[With Playbook] Turn {i+1}: {dur:.1f}ms")
        await asyncio.sleep(1.0)
        
    avg_without = sum(without_times) / len(without_times)
    avg_with = sum(with_times) / len(with_times)
    delta = avg_with - avg_without
    
    print("\n" + "="*50)
    print("BENCHMARK RESULTS SUMMARY:")
    print("="*50)
    print(f"Average Latency (No Playbook): {avg_without:.1f}ms")
    print(f"Average Latency (With Playbook): {avg_with:.1f}ms")
    print(f"Objection Playbook Latency Delta: {delta:+.1f}ms")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(run_benchmark())

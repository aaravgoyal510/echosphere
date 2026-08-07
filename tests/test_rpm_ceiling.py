import asyncio
import time
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from llm.dialogue_llm_client import DialogueLLMClient

async def worker(worker_id: int, client: DialogueLLMClient):
    prompt = f"Hi, I am customer number {worker_id}. Compare Echosphere and HubSpot briefly."
    user_msg = [{"role": "user", "content": prompt}]
    start = time.perf_counter()
    try:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_product_kb",
                    "description": "Search the knowledge base",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "type": {"type": "string"}
                        },
                        "required": ["query", "type"]
                    }
                }
            }
        ]
        text, tc = await client.query(
            system_prompt="You are a sales rep. If competitor comparison is requested, use search_product_kb.",
            messages=user_msg,
            tools=tools,
            max_tokens=100
        )
        latency = (time.perf_counter() - start) * 1000.0
        print(f"[Worker {worker_id}] SUCCESS in {latency:.1f}ms: {text or 'Tool calls: ' + str(tc)}")
        return latency
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000.0
        print(f"[Worker {worker_id}] FAILED in {latency:.1f}ms: {e}")
        return latency

async def main():
    client = DialogueLLMClient()
    print("Launching worst-case demo simulation (rapid back-to-back queries with tool definitions)...")
    
    tasks = [worker(i, client) for i in range(1, 21)]
    start_total = time.perf_counter()
    latencies = await asyncio.gather(*tasks)
    total_dur = (time.perf_counter() - start_total) * 1000.0
    
    valid_latencies = [l for l in latencies if l is not None]
    worst = max(valid_latencies) if valid_latencies else 0.0
    avg = sum(valid_latencies) / len(valid_latencies) if valid_latencies else 0.0
    
    print("\n" + "="*50)
    print("RPM CEILING TEST RESULTS:")
    print("="*50)
    print(f"Total simulated turns/tasks: 20")
    print(f"Total time elapsed: {total_dur/1000.0:.2f}s")
    print(f"Average latency per turn: {avg:.1f}ms")
    print(f"Worst-case single-turn latency: {worst:.1f}ms")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())

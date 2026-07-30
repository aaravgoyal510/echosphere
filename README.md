# Echosphere Voice Agent Pipeline

Echosphere is a high-performance voice agent pipeline designed for natural, real-time telephony conversations. It integrates automated Speech-to-Text (STT), a structured Dialogue Manager powered by Large Language Models, and Text-to-Speech (TTS) with sub-250ms barge-in interruption latency and strict data-integrity checks.

---

## 1. System Architecture

The following diagram illustrates how raw customer audio chunks, state managers, LLM decision engines, and real-time synthesis are coordinated:

```mermaid
graph TD
    subgraph Audio Pipeline
        CustomerAudio[Customer PCM Stream] -->|16kHz PCM chunks| STT[Whisper STT Adapter]
        STT -->|Final/Interim text| Coordinator[Pipeline Coordinator]
        Coordinator -->|Async reply stream| TTS[Piper TTS Adapter]
        TTS -->|PCM Audio output| OutputAudio[Audio Playback / Phone Bridge]
    end

    subgraph Decision & Integrity
        Coordinator -->|Utterance + History| DM[Dialogue Manager]
        DM -->|Prompt + Tools| LLM[LLM Client: gpt-4o-mini]
        LLM -->|Draft response| Guardrail[Anti-Hallucination Guardrail]
        Guardrail -->|Regex & Semantic Check| Validation{Validated?}
        Validation -->|Yes| DM
        Validation -->|No: Regeneate| LLM
        DM -->|Updates| DB[SQLite / Postgres DB]
        DM -->|Qualification| CRM[CRM Adapter / HubSpot]
    end

    subgraph Interruption (Barge-in)
        CustomerAudio -->|Speech detected| TTM[Turn Taking Manager]
        TTM -->|Interrupt trigger| Coordinator
        Coordinator -->|1. Cancel in-flight task| LLM
        Coordinator -->|2. Cancel & Flush| TTS
    end
```

---

## 2. Core Features

1. **Sub-250ms Barge-In Interruption:** When the customer speaks, the `TurnTakingManager` detects the energy/VAD threshold, cancels the active dialogue generation task, and flushes the Piper synthesis queue within ~200ms to stop the agent mid-sentence.
2. **Anti-Hallucination Guardrails:** If the LLM mentions a price, feature, or promotion that was not explicitly returned by a database tool query on the current turn, the guardrail rejects the draft response and forces a regeneration.
3. **Graceful Outage Escalation:** If the LLM connection fails or hits rate limits, the pipeline coordinador catches the error, sets the call state to `"escalated"` in the database to prevent silent state mismatches, and triggers a fallback TTS message: *"I'm sorry, I'm having a technical issue looking up that information. Let me transfer you to a human agent."*
4. **Dynamic LLM Provider & Local Fallback:** Routes queries to **GitHub Models (`gpt-4o-mini`)** or **Groq (`llama-3.3-70b`)**. If rate-limited (429), it automatically routes queries to a local **Ollama (`qwen2.5:1.5b`)** model.

---

## 3. Assets to Save Separately (For Running Locally on a GPU)

Because large weights, databases, and credentials are excluded from Git for privacy and performance reasons, you must download and save the following assets separately to run the project on a local GPU machine:

### A. The `.env` Configuration File
Contains API tokens and configuration settings. Save this in the root folder as `.env`:
```ini
# Primary LLM Configuration
GITHUB_TOKEN=your_github_models_token
GITHUB_MODEL=gpt-4o-mini
USE_GROQ=false
GROQ_API_KEY=your_groq_api_token
GROQ_MODEL=llama-3.3-70b-versatile

# Fallback Configuration
OLLAMA_MODEL=qwen2.5:1.5b

# Telephony Config (For Production Handoff)
TELEPHONY_PROVIDER=mock
```

### B. Piper Voice ONNX Assets (TTS)
Download and place the Piper voice files inside a `.piper/` directory at the project root:
* **Model Weight:** `en_US-amy-low.onnx` (Voice weight)
* **Model Config:** `en_US-amy-low.onnx.json` (Voice configuration)

*Note: AMD or NVIDIA GPUs can execute Piper ONNX synthesis in under 50ms using ONNX Runtime with CUDA execution providers.*

### C. Whisper CTranslate2 Weights (STT)
The `faster-whisper` package automatically downloads models from Hugging Face on first load and caches them in your local system (`~/.cache/huggingface/hub/`). 
* To run on a GPU, ensure you have **CUDA** and **cuDNN** installed on your system.
* You can change the model load parameters in `pipeline/whisper_adapter.py` to specify GPU compute:
  ```python
  self.model = WhisperModel("base.en", device="cuda", compute_type="float16")
  ```

### D. Local Ollama LLM Fallback (Dialogue)
If the primary cloud LLMs are rate-limited, the system falls back to a local Ollama instance:
* Install [Ollama](https://ollama.com).
* Run the following command on your machine to pre-load the lightweight Qwen model:
  ```bash
  ollama pull qwen2.5:1.5b
  ```
  *(If your GPU has >8GB VRAM, you can pull a larger and more coherent model like `qwen2.5:7b-instruct` or `llama3.2:3b` and update `OLLAMA_MODEL` in `.env` accordingly).*

---

## 4. Quickstart Guide

### 1. Installation
Set up your virtual environment and install dependencies:
```bash
# Create venv
python -m venv .venv
.venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 2. Run the Verification Tests
To run all test harnesses and verify pipeline mechanics:
```bash
# Run unit tests (including real Piper 250ms stop latency check)
.venv\Scripts\pytest tests/test_pipeline.py tests/test_db_and_pricing.py

# Run the voice pipeline simulator
.venv\Scripts\python tests/voice_pipeline_simulator.py

# Run the red-team adversarial test suite (15 turns against gpt-4o-mini)
.venv\Scripts\python tests/test_red_team.py
```

# Setup Guide: EchoSphere Voice Agent Platform

This guide outlines the system prerequisites, environment setup, and steps to run the EchoSphere voice agent platform on a fresh machine.

---

## 1. Prerequisites

### System Requirements
* **Python**: Version 3.10 to 3.13 (Python 3.11 is recommended).
* **C++ Build Tools (Windows only)**: Required to build native extensions (like `faster-whisper` and `piper-phonemize`). Download and install **C++ Build Tools** from the official [Visual Studio Build Tools Installer](https://visualstudio.microsoft.com/visual-cpp-build-tools/).

### Optional Services
* **Redis**: Used for persistent session caching. If Redis is not running, the server automatically defaults to an in-memory session cache.
* **PostgreSQL**: Used for production databases. If PostgreSQL environment variables are omitted, the server automatically defaults to a local SQLite database (`echosphere.db`).

---

## 2. Installation Steps

### Step 1: Create a Virtual Environment
Initialize a clean Python virtual environment and activate it:
```bash
# Create the environment
python -m venv .venv

# Activate on Windows:
.venv\Scripts\activate

# Activate on macOS/Linux:
source .venv/bin/activate
```

### Step 2: Install Dependencies
Upgrade pip and install all required python libraries:
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Create a file named `.env` at the root of your workspace:
```bash
copy .env.example .env
```
Fill in the following variables:
```ini
# Dialogue LLM Credentials
AICREDITS_API_KEY=your-api-key-here
AICREDITS_BASE_URL=https://api.aicredits.in/v1

# Optional Database Configurations (Omitting these defaults to SQLite)
# DB_HOST=localhost
# DB_PORT=5432
# DB_NAME=echosphere
# DB_USER=postgres
# DB_PASSWORD=secret

# Optional Redis Configuration (Omitting this defaults to In-Memory session caching)
# REDIS_HOST=localhost
# REDIS_PORT=6379
```

### Step 4: Download TTS Voice Assets
The local Piper synthesis engine requires ONNX voice files. Download these assets automatically by running the latency spike test script:
```bash
python tests/latency_spike.py
```
This downloads `en_US-amy-low.onnx` and `en_US-amy-low.onnx.json` into a `.piper/` directory at the project root.

---

## 3. Verifying the Installation

To verify that the installation is complete and there are no system library issues, run the full automated test suite:
```bash
pytest -v
```
All 55 tests should pass successfully.

---

## 4. Running the Application

1. Start the FastAPI backend and WebSocket server:
   ```bash
   python pipeline/web_server.py
   ```
2. Open your web browser and navigate to:
   ```
   http://127.0.0.1:8000
   ```
3. Allow microphone permissions in your browser.
4. Click **Start Demo Call** to speak with Aria, configure options in the **Admin Console** tab, or run playbooks analyses in the **Pattern Learning** tab.

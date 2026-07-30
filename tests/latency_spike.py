import os
import time
import math
import wave
import struct
import urllib.request
import logging
import httpx
from faster_whisper import WhisperModel
from piper.voice import PiperVoice
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
MODEL_DIR = os.path.join(os.getcwd(), ".piper")
WAV_PATH = "dummy_test.wav"

ONNX_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx"
JSON_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx.json"

ONNX_PATH = os.path.join(MODEL_DIR, "en_US-amy-low.onnx")
JSON_PATH = os.path.join(MODEL_DIR, "en_US-amy-low.onnx.json")

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma4:31b-cloud"

def generate_dummy_wav(filepath: str, duration_sec: float = 1.0, freq: float = 440.0):
    """Generate a simple mono WAV file (16kHz, 16-bit PCM) for STT testing."""
    sample_rate = 16000
    num_samples = int(duration_sec * sample_rate)
    
    with wave.open(filepath, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        
        for i in range(num_samples):
            val = int(32767 * math.sin(2.0 * math.pi * freq * i / sample_rate))
            data = struct.pack("<h", val)
            wav_file.writeframesraw(data)
    logger.info(f"Generated dummy WAV file: {filepath}")

def download_piper_model():
    """Downloads the Piper voice model and config files if not already present."""
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    if not os.path.exists(JSON_PATH):
        logger.info(f"Downloading Piper JSON config from {JSON_URL}...")
        urllib.request.urlretrieve(JSON_URL, JSON_PATH)
        logger.info("JSON config downloaded.")
        
    if not os.path.exists(ONNX_PATH):
        logger.info(f"Downloading Piper ONNX model from {ONNX_URL} (~15MB)...")
        urllib.request.urlretrieve(ONNX_URL, ONNX_PATH)
        logger.info("ONNX model downloaded.")

def query_ollama_extraction(client: httpx.Client, text: str) -> float:
    """Sends a fast extraction query to local Ollama proxy and returns elapsed time."""
    system_prompt = (
        "You are a sales qualification extractor. Analyze the input and output JSON. "
        "Fields: 'intent' (pricing, competitor, calendar, other), 'team_size' (integer or null). "
        "Strictly output only raw valid JSON without markdown wrapping or comments."
    )
    user_prompt = f"Extract from: '{text}'"
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
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
    api_key = os.getenv("OLLAMA_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        
    start_time = time.perf_counter()
    try:
        response = client.post(OLLAMA_URL, json=payload, headers=headers, timeout=5.0)
        if response.status_code == 200:
            res_json = response.json()
            content_text = res_json.get("message", {}).get("content", "").strip()
            logger.info(f"Ollama JSON Output: {content_text}")
        else:
            logger.warning(f"Ollama API failed with status {response.status_code}")
        latency = time.perf_counter() - start_time
        return latency
    except Exception as e:
        logger.error(f"Ollama extraction call failed: {e}")
        return 0.0

def run_latency_spike():
    logger.info("Starting end-to-end Latency Spike (Whisper + Ollama Cloud + Piper)...")
    
    # 1. Download Piper files if needed
    download_piper_model()
    
    # 2. Generate dummy WAV file
    generate_dummy_wav(WAV_PATH)
    
    # 3. Create persistent HTTP client
    with httpx.Client(timeout=10.0) as client:
        # 4. Load Whisper
        logger.info("Loading Whisper 'base.en' model...")
        stt_model = WhisperModel("base.en", device="cpu", compute_type="int8")
        
        # Warmup STT
        list(stt_model.transcribe(WAV_PATH, beam_size=1)[0])
        
        # 5. Load Piper
        logger.info("Loading Piper TTS model...")
        tts_model = PiperVoice.load(ONNX_PATH, config_path=JSON_PATH)
        
        # Warmup TTS
        next(tts_model.synthesize("Warmup"))
        
        # 6. Warmup/Test Ollama Extraction
        logger.info("Warming up/Testing Ollama Cloud Model...")
        query_ollama_extraction(client, "hello")
        
        # 7. Profile STT (Whisper)
        logger.info("Profiling STT (Whisper)...")
        start_stt = time.perf_counter()
        segments, info = stt_model.transcribe(WAV_PATH, beam_size=1)
        transcribed_text = " ".join([seg.text for seg in segments])
        stt_latency = time.perf_counter() - start_stt
        logger.info(f"STT Output: '{transcribed_text}' (Inference: {stt_latency*1000:.1f}ms)")
        
        # 8. Profile Ollama Extraction
        logger.info("Profiling Ollama Cloud Extraction...")
        test_phrase = "Can you tell me how much it costs for about fifty people?"
        ollama_latency = query_ollama_extraction(client, test_phrase)
        logger.info(f"Ollama Cloud Extraction completed in {ollama_latency*1000:.1f}ms.")
        
        # 9. Profile TTS (Piper)
        logger.info("Profiling TTS (Piper)...")
        agent_response = "Pricing starts at fifty dollars per seat for the enterprise tier."
        start_tts = time.perf_counter()
        audio_stream = tts_model.synthesize(agent_response)
        next(audio_stream)
        tts_first_chunk = time.perf_counter() - start_tts
        
        for _ in audio_stream:
            pass
        tts_total = time.perf_counter() - start_tts
        logger.info(f"TTS First Chunk: {tts_first_chunk*1000:.1f}ms, Total: {tts_total*1000:.1f}ms")
        
        # 10. Summary
        estimated_roundtrip = stt_latency + ollama_latency + tts_first_chunk
        logger.info("=========================================")
        logger.info("LATENCY SPIKE PROFILE SUMMARY WITH OLLAMA")
        logger.info("=========================================")
        logger.info(f"STT Inference Time  : {stt_latency*1000:.1f}ms")
        logger.info(f"Ollama Extraction   : {ollama_latency*1000:.1f}ms")
        logger.info(f"TTS First Chunk     : {tts_first_chunk*1000:.1f}ms")
        logger.info(f"TTS Total Synthesis : {tts_total*1000:.1f}ms")
        logger.info(f"Estimated End-to-End: {estimated_roundtrip*1000:.1f}ms (excluding dialogue LLM)")
        logger.info("=========================================")
        
    # Clean WAV
    if os.path.exists(WAV_PATH):
        os.remove(WAV_PATH)

if __name__ == "__main__":
    run_latency_spike()

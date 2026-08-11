let ws = null;
let audioCtx = null;
let micStream = null;
let scriptProcessor = null;
let pcmPlayer = null;
let recordingStream = null;
let mediaRecorder = null;
let recordedChunks = [];
let activeCallId = null;
let isCallActive = false;

// Audio Queue Player for streaming raw PCM
class PCMPlayer {
    constructor() {
        this.audioCtx = null;
        this.nextStartTime = 0;
        this.activeSources = [];
    }

    init() {
        if (!this.audioCtx) {
            this.audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
            this.nextStartTime = this.audioCtx.currentTime;
        }
    }

    play(int16Buffer) {
        this.init();
        const len = int16Buffer.length;
        const float32 = new Float32Array(len);
        for (let i = 0; i < len; i++) {
            float32[i] = int16Buffer[i] / 32768.0;
        }

        const buffer = this.audioCtx.createBuffer(1, float32.length, 16000);
        buffer.copyToChannel(float32, 0);

        const source = this.audioCtx.createBufferSource();
        source.buffer = buffer;
        source.connect(this.audioCtx.destination);

        const currentTime = this.audioCtx.currentTime;
        if (this.nextStartTime < currentTime) {
            this.nextStartTime = currentTime;
        }

        source.start(this.nextStartTime);
        this.activeSources.push(source);
        
        source.onended = () => {
            const idx = this.activeSources.indexOf(source);
            if (idx > -1) {
                this.activeSources.splice(idx, 1);
            }
        };

        this.nextStartTime += buffer.duration;
    }

    cancel() {
        this.activeSources.forEach(src => {
            try {
                src.stop();
            } catch(e) {}
        });
        this.activeSources = [];
        if (this.audioCtx) {
            this.nextStartTime = this.audioCtx.currentTime;
        }
    }
}

// Tab navigation
function switchTab(tabName) {
    document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    
    event.target.classList.add('active');
    document.getElementById(`tab-${tabName}`).classList.add('active');

    if (tabName === 'admin') {
        loadPricingTiers();
        loadKBDocuments();
    } else if (tabName === 'learning') {
        loadStats();
    }
}

// Log transcript to screen
function logToTranscript(speaker, text, interrupted = false) {
    const box = document.getElementById('transcript-box');
    
    // Remove initialization text if present
    const initMsg = box.querySelector('.system-msg');
    if (initMsg) {
        box.innerHTML = '';
    }

    const bubble = document.createElement('div');
    bubble.className = `msg-bubble msg-${speaker}`;
    if (interrupted) {
        bubble.classList.add('msg-interrupted');
    }

    const speakerLbl = speaker === 'customer' ? 'Customer' : 'Aria';
    bubble.innerHTML = `<strong>${speakerLbl}:</strong> ${text}`;
    if (interrupted) {
        bubble.innerHTML += ` <span class="badge badge-error">Interrupted</span>`;
    }

    box.appendChild(bubble);
    box.scrollTop = box.scrollHeight;
}

// Live state visual panel updater
function updateStatePanel(state) {
    document.getElementById('state-seats').innerText = state.qualification?.team_size?.value || '-';
    document.getElementById('state-competitor').innerText = state.qualification?.current_solution?.value || '-';
    document.getElementById('state-timeline').innerText = state.qualification?.timeline?.value || '-';
    
    // Pricing tier
    if (state.qualification?.pricing_tier_discussed?.value) {
        document.getElementById('state-tier').innerText = state.qualification.pricing_tier_discussed.value;
    } else {
        document.getElementById('state-tier').innerText = '-';
    }

    // Objections
    const objList = document.getElementById('objections-list');
    objList.innerHTML = '';
    const objections = state.objections || [];
    if (objections.length === 0) {
        objList.innerHTML = '<div class="no-data">No objections raised yet.</div>';
    } else {
        objections.forEach(obj => {
            const card = document.createElement('div');
            card.className = `obj-card ${obj.resolved ? 'obj-resolved' : 'obj-active'}`;
            card.innerHTML = `
                <div class="obj-header">
                    <span class="obj-title">${obj.type.toUpperCase()}</span>
                    <span class="badge ${obj.resolved ? 'badge-success' : 'badge-warn'}">
                        ${obj.resolved ? 'Resolved' : 'Active'}
                    </span>
                </div>
                <div class="obj-detail">${obj.detail}</div>
            `;
            objList.appendChild(card);
        });
    }

    // Outcome
    const outcomeBadge = document.getElementById('outcome-badge');
    outcomeBadge.className = `outcome-badge outcome-${state.outcome}`;
    outcomeBadge.innerText = (state.outcome || 'IN PROGRESS').replace('_', ' ').toUpperCase();

    // Outage Banner
    const banner = document.getElementById('outage-banner');
    if (state.outcome === 'escalated' && state.escalation?.reason && state.escalation.reason.includes('Outage')) {
        banner.classList.remove('hidden');
    } else {
        banner.classList.add('hidden');
    }
}

// Mic Audio Streaming Capturer
async function startAudioStreaming() {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    
    const source = audioCtx.createMediaStreamSource(micStream);
    scriptProcessor = audioCtx.createScriptProcessor(4096, 1, 1);
    
    scriptProcessor.onaudioprocess = (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        const inputData = e.inputBuffer.getChannelData(0);
        const pcmData = new Int16Array(inputData.length);
        
        for (let i = 0; i < inputData.length; i++) {
            const val = Math.max(-1, Math.min(1, inputData[i]));
            pcmData[i] = val < 0 ? val * 0x8000 : val * 0x7FFF;
        }
        ws.send(pcmData.buffer);
    };

    source.connect(scriptProcessor);
    scriptProcessor.connect(audioCtx.destination);
    console.log("Mic streaming initialized at 16kHz 16-bit PCM");
}

function stopAudioStreaming() {
    if (scriptProcessor) {
        scriptProcessor.disconnect();
        scriptProcessor = null;
    }
    if (audioCtx) {
        audioCtx.close();
        audioCtx = null;
    }
    if (micStream) {
        micStream.getTracks().forEach(track => track.stop());
        micStream = null;
    }
}

// Call Connection Management
function toggleCall() {
    const btn = document.getElementById('btn-call');
    
    if (!isCallActive) {
        // Start Call
        activeCallId = "call_" + Math.random().toString(36).substring(2, 9);
        btn.innerText = "Connecting...";
        btn.className = "btn btn-danger";
        
        pcmPlayer = new PCMPlayer();
        
        ws = new WebSocket(`ws://127.0.0.1:8000/ws/${activeCallId}`);
        ws.binaryType = 'arraybuffer';

        ws.onopen = async () => {
            console.log("WS connection opened");
            btn.innerText = "End Call";
            isCallActive = true;
            document.getElementById('btn-record').disabled = false;
            document.getElementById('chat-input-field').disabled = false;
            document.getElementById('btn-send-chat').disabled = false;
            document.getElementById('transcript-box').innerHTML = '<div class="system-msg">Call Connected. Talk into your mic or type a message below.</div>';
            
            // Start capturing microphone input
            await startAudioStreaming();
        };

        ws.onmessage = (event) => {
            if (event.data instanceof ArrayBuffer) {
                // Play raw binary PCM chunk
                const int16Buffer = new Int16Array(event.data);
                pcmPlayer.play(int16Buffer);
            } else {
                // Parse control text JSON
                const msg = jsonParseSafe(event.data);
                if (!msg) return;

                if (msg.type === 'state_update') {
                    updateStatePanel(msg.state);
                    
                    // Synchronize UI Transcript
                    const box = document.getElementById('transcript-box');
                    box.innerHTML = '';
                    msg.state.transcript.forEach(turn => {
                        logToTranscript(turn.speaker, turn.text, turn.interrupted);
                    });
                } else if (msg.type === 'cancel_audio') {
                    // Instantly flush queue
                    pcmPlayer.cancel();
                } else if (msg.type === 'barge_in') {
                    showBargeInAlert();
                }
            }
        };

        ws.onclose = () => {
            console.log("WS connection closed");
            cleanupCallUI();
        };
        
        ws.onerror = (e) => {
            console.error("WS error:", e);
            cleanupCallUI();
        };

    } else {
        // End Call
        if (ws) {
            ws.send(JSON.stringify({ type: "hangup" }));
            ws.close();
        }
        cleanupCallUI();
    }
}

function cleanupCallUI() {
    isCallActive = false;
    stopAudioStreaming();
    if (pcmPlayer) {
        pcmPlayer.cancel();
        pcmPlayer = null;
    }
    const btn = document.getElementById('btn-call');
    btn.innerText = "Start Demo Call";
    btn.className = "btn btn-primary";
    
    // Stop recording if active
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        toggleRecording();
    }
    document.getElementById('btn-record').disabled = true;
    document.getElementById('chat-input-field').disabled = true;
    document.getElementById('btn-send-chat').disabled = true;
    document.getElementById('chat-input-field').value = '';
}

function sendChatMessage() {
    const input = document.getElementById('chat-input-field');
    const text = input.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
    
    ws.send(JSON.stringify({
        type: 'chat_message',
        text: text
    }));
    
    input.value = '';
}

function showBargeInAlert() {
    const el = document.getElementById('barge-indicator');
    el.classList.add('active');
    setTimeout(() => el.classList.remove('active'), 1200);
}

function jsonParseSafe(str) {
    try {
        return json = JSON.parse(str);
    } catch(e) {
        return null;
    }
}

// Media Recorder (Session tab video + merged microphone capture)
async function toggleRecording() {
    const btn = document.getElementById('btn-record');
    
    if (!mediaRecorder || mediaRecorder.state === 'inactive') {
        // Start Recording
        recordedChunks = [];
        try {
            // 1. Capture screen/tab with output audio
            const displayStream = await navigator.mediaDevices.getDisplayMedia({
                video: true,
                audio: true
            });

            // 2. Mix micStream audio track and display audio track together
            const mixAudioCtx = new AudioContext();
            const dest = mixAudioCtx.createMediaStreamDestination();

            if (micStream) {
                const micSource = mixAudioCtx.createMediaStreamSource(micStream);
                micSource.connect(dest);
            }

            const displayAudioTracks = displayStream.getAudioTracks();
            if (displayAudioTracks.length > 0) {
                const displayAudioStream = new MediaStream([displayAudioTracks[0]]);
                const displaySource = mixAudioCtx.createMediaStreamSource(displayAudioStream);
                displaySource.connect(dest);
            }

            // Combine video track from screen and combined audio track
            recordingStream = new MediaStream([
                ...displayStream.getVideoTracks(),
                ...dest.stream.getAudioTracks()
            ]);

            mediaRecorder = new MediaRecorder(recordingStream, { mimeType: 'video/webm' });
            mediaRecorder.ondataavailable = (e) => {
                if (e.data && e.data.size > 0) {
                    recordedChunks.push(e.data);
                }
            };

            mediaRecorder.onstop = () => {
                const blob = new Blob(recordedChunks, { type: 'video/webm' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `echosphere_demo_${activeCallId}.webm`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                
                // Also export call state as JSON
                exportStateJSON();
            };

            mediaRecorder.start(1000);
            btn.innerText = "🛑 Stop & Export Recording";
            btn.className = "btn btn-danger";
            console.log("Recording started");
        } catch (err) {
            console.error("Screen recording access denied or error:", err);
        }
    } else {
        // Stop Recording
        mediaRecorder.stop();
        if (recordingStream) {
            recordingStream.getTracks().forEach(track => track.stop());
        }
        btn.innerText = "Record Session (Tab+Mic)";
        btn.className = "btn btn-secondary";
        console.log("Recording stopped");
    }
}

async function exportStateJSON() {
    // Fetch latest session details from SQLite
    try {
        const response = await fetch(`/api/pricing`);
        const pricing = await response.json();
        
        const stateData = {
            call_id: activeCallId,
            timestamp: new Date().toISOString(),
            transcript: []
        };
        
        // Grab bubble texts
        document.querySelectorAll('.msg-bubble').forEach(b => {
            const speaker = b.classList.contains('msg-customer') ? 'customer' : 'agent';
            stateData.transcript.push({
                speaker: speaker,
                text: b.innerText.replace(/^(Customer|Aria): /, '')
            });
        });

        const blob = new Blob([JSON.stringify(stateData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `echosphere_state_${activeCallId}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    } catch(e) {
        console.error("State export failed:", e);
    }
}

// CRUD: Pricing Tiers
async function loadPricingTiers() {
    const res = await fetch('/api/pricing');
    const data = await res.json();
    const body = document.getElementById('pricing-table-body');
    body.innerHTML = '';
    data.forEach(tier => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${tier.tier_id}</td>
            <td>${tier.name}</td>
            <td>${tier.min_seats} – ${tier.max_seats || '∞'}</td>
            <td>$${tier.price_per_seat_monthly}</td>
            <td>$${tier.onboarding_fee}</td>
            <td><button class="btn-delete" onclick="deletePricingTier('${tier.tier_id}')">Delete</button></td>
        `;
        body.appendChild(row);
    });
}

async function savePricingTier() {
    const payload = {
        tier_id: document.getElementById('price-id').value,
        name: document.getElementById('price-name').value,
        min_seats: parseInt(document.getElementById('price-min').value),
        max_seats: document.getElementById('price-max').value ? parseInt(document.getElementById('price-max').value) : null,
        price_per_seat_monthly: parseFloat(document.getElementById('price-rate').value),
        onboarding_fee: parseFloat(document.getElementById('price-onboarding').value),
        included_features: document.getElementById('price-features').value.split(',').map(f => f.trim())
    };

    const res = await fetch('/api/pricing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (res.ok) {
        loadPricingTiers();
        // Reset form
        document.getElementById('price-id').value = '';
        document.getElementById('price-name').value = '';
        document.getElementById('price-min').value = '';
        document.getElementById('price-max').value = '';
        document.getElementById('price-rate').value = '';
        document.getElementById('price-onboarding').value = '';
        document.getElementById('price-features').value = '';
    }
}

async function deletePricingTier(id) {
    const res = await fetch(`/api/pricing/${id}`, { method: 'DELETE' });
    if (res.ok) loadPricingTiers();
}

// CRUD: KB Documents
async function loadKBDocuments() {
    const res = await fetch('/api/kb');
    const data = await res.json();
    const body = document.getElementById('kb-table-body');
    body.innerHTML = '';
    data.forEach(doc => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${doc.doc_id}</td>
            <td>${doc.title}</td>
            <td>${doc.type}</td>
            <td>${doc.competitor_name || '-'}</td>
            <td><button class="btn-delete" onclick="deleteKBDocument('${doc.doc_id}')">Delete</button></td>
        `;
        body.appendChild(row);
    });
}

async function saveKBDocument() {
    const payload = {
        doc_id: document.getElementById('kb-id').value,
        title: document.getElementById('kb-title').value,
        type: document.getElementById('kb-type').value,
        competitor_name: document.getElementById('kb-competitor').value || null,
        content: document.getElementById('kb-content').value
    };

    const res = await fetch('/api/kb', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (res.ok) {
        loadKBDocuments();
        document.getElementById('kb-id').value = '';
        document.getElementById('kb-title').value = '';
        document.getElementById('kb-competitor').value = '';
        document.getElementById('kb-content').value = '';
    }
}

async function deleteKBDocument(id) {
    const res = await fetch(`/api/kb/${id}`, { method: 'DELETE' });
    if (res.ok) loadKBDocuments();
}

// Learning tab functions
async function loadStats() {
    const res = await fetch('/api/stats');
    const data = await res.json();
    
    // Set analytics numbers
    document.getElementById('stat-calls').innerText = data.outcomes ? Object.values(data.outcomes).reduce((a,b)=>a+b, 0) : 0;
    document.getElementById('stat-objections').innerText = data.total_objections_raised || 0;
    document.getElementById('stat-guardrails').innerText = data.total_guardrail_triggers || 0;
    
    const raised = data.total_objections_raised || 0;
    const resolved = data.total_objections_resolved || 0;
    const pct = raised > 0 ? Math.round((resolved / raised) * 100) : 0;
    document.getElementById('stat-resolved').innerText = `${pct}%`;
}

async function triggerDistillation() {
    const btn = document.querySelector('.distill-card button');
    const loader = document.getElementById('distill-loader');
    const output = document.getElementById('distill-output');
    
    btn.disabled = true;
    loader.classList.remove('hidden');
    output.innerText = '';

    try {
        const res = await fetch('/api/learning/distill', { method: 'POST' });
        const data = await res.json();
        output.innerText = data.report || "No recommendation was generated.";
    } catch(e) {
        output.innerText = `Error running distillation run: ${e.message}`;
    } finally {
        btn.disabled = false;
        loader.classList.add('hidden');
    }
}

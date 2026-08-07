"""
Agora Telephony Adapter Stub for EchoSphere.

This module serves as a placeholder for the Agora SDK integration. Once Agora API access 
is provided (expected after Round 2 shortlisting), this stub will be fully implemented 
to connect our real-time voice pipeline with Agora's Real-Time Communication (RTC) network, 
Voice AI, and Conversational AI APIs.

Swap-in target:
To activate this adapter, update the instantiation point in your active runner or pipeline
coordinator script (e.g. tests/chat_simulator.py or pipeline_coordinator.py / dialogue_manager.py)
to instantiate AgoraTelephonyAdapter instead of MockTelephonyAdapter.
"""

import logging
from typing import Dict, Any
from telephony.base import TelephonyAdapter

logger = logging.getLogger(__name__)

class AgoraTelephonyAdapter(TelephonyAdapter):
    """
    Stub implementation of the TelephonyAdapter interface for the Agora RTC/Voice AI SDKs.
    Awaiting real API credentials/SDK package installation.
    """
    def __init__(self):
        logger.warning("AgoraTelephonyAdapter initialized in STUB mode. Real Agora calls will raise NotImplementedError.")

    def initiate_warm_transfer(
        self,
        call_id: str,
        human_phone_or_sip: str,
        briefing_card: Dict[str, Any]
    ) -> bool:
        """
        Bridges the current call to a human agent and sends a briefing card.

        Once Agora API is available, this method should:
        1. Access the active Agora RTC engine instance / channel context for the call_id.
        2. Leverage Agora's SIP Gateway or Channel Joining API to bridge the human agent (SIP or phone) 
           into the active conversation as a new participant (three-way conference).
        3. Emit/Push the `briefing_card` JSON to the target human agent console (either via Agora 
           RTM - Real-Time Messaging channel, or a webhook to the CRM/Slack panel).
        4. Once the human agent joins and takes over, mute/disconnect the AI assistant agent.
        """
        raise NotImplementedError(
            "AgoraTelephonyAdapter.initiate_warm_transfer is not implemented yet. "
            "Awaiting Agora RTC/SIP integration access credentials."
        )

    def disconnect_call(self, call_id: str) -> None:
        """
        Gracefully disconnects/ends the active call.

        Once Agora API is available, this method should:
        1. Retrieve the active Agora RTC connection / channel object matching the call_id.
        2. Invoke Agora SDK connection termination APIs (e.g. channel.leave() or rtc_engine.leaveChannel()) 
           to drop the caller from the audio stream.
        3. Release allocated RTC resources, shut down the streaming pipe, and trigger session completion hooks.
        """
        raise NotImplementedError(
            "AgoraTelephonyAdapter.disconnect_call is not implemented yet. "
            "Awaiting Agora SDK channel context/credentials."
        )

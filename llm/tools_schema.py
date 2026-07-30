from typing import List, Dict, Any

# OpenAI / GitHub Models Tool definitions matching TechSpec.md §3

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_product_kb",
            "description": "Query the product knowledge base (vector similarity search) for competitive positioning battlecards, onboarding fee policies, and standard FAQs. Always call this if the customer asks about competitor features, custom comparison, or onboarding fee specifics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (e.g. competitor name, onboarding fee, features)"
                    },
                    "type": {
                        "type": "string",
                        "enum": ["feature_doc", "competitive_battlecard", "policy", "faq", "case_study"],
                        "description": "The type of document to search for"
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
            "description": "Get the monthly pricing quote and promotions for a given team size (number of seats). Never guess, estimate, or calculate pricing manually.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team_size": {
                        "type": "integer",
                        "description": "The number of seats (seats/users) needed"
                    }
                },
                "required": ["team_size"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_lead",
            "description": "Retrieve lead details (CRM record) for a given phone number or email or lead ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_or_id": {
                        "type": "string",
                        "description": "The lead ID, email address, or phone number of the caller"
                    }
                },
                "required": ["phone_or_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_lead_qualification",
            "description": "Write/overwrite structured qualification fields to the CRM lead record. Call this immediately whenever the customer states or changes a qualification fact (team size, budget signal, competitor, timeline, decision-maker status, use case).",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {
                        "type": "string",
                        "description": "The CRM lead ID"
                    },
                    "fields": {
                        "type": "object",
                        "description": "Partial QualificationData object containing only the updated fields.",
                        "properties": {
                            "team_size": {
                                "type": "integer",
                                "description": "The number of seats/users"
                            },
                            "budget_signal": {
                                "type": "string",
                                "description": "Customer budget status/signal (e.g. 'approved', 'none', 'flexible')"
                            },
                            "current_solution": {
                                "type": "string",
                                "description": "Current competitor or solution used by the customer"
                            },
                            "decision_maker": {
                                "type": "string",
                                "description": "Whether the caller is the decision maker ('yes', 'no', 'evaluator')"
                            },
                            "timeline": {
                                "type": "string",
                                "description": "Customer purchase timeline (e.g. 'immediate', '1_month', '3_months')"
                            },
                            "use_case": {
                                "type": "string",
                                "description": "Brief description of the customer's main use case"
                            }
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
            "description": "Log a structured event mid-call (objection raised/resolved, topic covered) for real-time CRM visibility and analytics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {
                        "type": "string",
                        "description": "The current call ID"
                    },
                    "event_type": {
                        "type": "string",
                        "description": "The event category (e.g. 'objection_raised', 'objection_resolved', 'topic_covered')"
                    },
                    "detail": {
                        "type": "object",
                        "description": "Event metadata (e.g., {'type': 'competitor', 'detail': 'Evaluating HubSpot', 'resolved': false})"
                    }
                },
                "required": ["call_id", "event_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_calendar_availability",
            "description": "Get real available meeting slots for a given window and meeting type. Always call before proposing times to the customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window_start": {
                        "type": "string",
                        "description": "ISO start timestamp for the search window"
                    },
                    "window_end": {
                        "type": "string",
                        "description": "ISO end timestamp for the search window"
                    },
                    "meeting_type": {
                        "type": "string",
                        "enum": ["standard_demo", "enterprise_demo", "follow_up_call"],
                        "description": "The type of meeting to schedule"
                    }
                },
                "required": ["window_start", "window_end", "meeting_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_meeting",
            "description": "Book a confirmed meeting on the calendar. Only call after the customer has explicitly agreed to a specific slot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {
                        "type": "string",
                        "description": "The CRM lead ID"
                    },
                    "slot_start": {
                        "type": "string",
                        "description": "ISO start timestamp for the booked slot"
                    },
                    "slot_end": {
                        "type": "string",
                        "description": "ISO end timestamp for the booked slot"
                    },
                    "meeting_type": {
                        "type": "string",
                        "description": "The type of meeting to book"
                    }
                },
                "required": ["lead_id", "slot_start", "slot_end", "meeting_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_follow_up_task",
            "description": "Create a follow-up task for a human when the customer isn't ready to book now but is qualified/interested.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {
                        "type": "string",
                        "description": "The CRM lead ID"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for follow-up"
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                        "description": "Task priority level"
                    },
                    "due_at": {
                        "type": "string",
                        "description": "ISO timestamp for when the task is due"
                    },
                    "context_summary": {
                        "type": "string",
                        "description": "Context summary of the call for the human agent"
                    }
                },
                "required": ["lead_id", "reason", "priority", "context_summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_escalation",
            "description": "Escalate the call to a human agent, either warm transfer (live) or async handoff. Use whenever policy rules are met or you are out of your depth.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {
                        "type": "string",
                        "description": "The call ID"
                    },
                    "reason": {
                        "type": "string",
                        "description": "The reason for escalation"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["warm_transfer", "async_handoff"],
                        "description": "Warm live transfer or asynchronous handoff"
                    }
                },
                "required": ["call_id", "reason", "mode"]
            }
        }
    }
]

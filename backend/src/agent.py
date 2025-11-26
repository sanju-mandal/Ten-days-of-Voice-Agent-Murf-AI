

import logging
import json
import os
import asyncio
from datetime import datetime
from typing import Annotated, Literal, Optional, List
from dataclasses import dataclass, asdict

from dotenv import load_dotenv
from pydantic import Field
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    WorkerOptions,
    cli,
    function_tool,
    RunContext,
)

# 🔌 PLUGINS
from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")
load_dotenv(".env.local")


FAQ_FILE = "store_faq.json"
LEADS_FILE = "leads_db.json"

# Default FAQ data for "Dr. Abhishek Store"
DEFAULT_FAQ = [
    {
        "question": "What do you sell?",
        "answer": "Zomato provides online food delivery, restaurant discovery, reviews, and dining services across India. It connects users with restaurants, cloud kitchens, and delivery partners."
    },
    {
        "question": "How much does the service cost?",
        "answer": "Food prices depend on the restaurant. Delivery charges vary by distance, time, and location. Zomato Gold (membership) usually ranges between ₹149–₹299 per month depending on offers."
    },
    {
        "question": "Do you offer free features?",
        "answer": "Yes! Browsing restaurants, checking menus, reading reviews, and exploring ratings are free. Users only pay for food orders, delivery fees, and optional subscriptions like Zomato Gold."
    },
    {
        "question": "Do you work with businesses or restaurants?",
        "answer": "Absolutely. Zomato partners with restaurants to help them reach customers, increase online orders, and manage operations through the Zomato Partner Platform. Commission rates depend on location and order type."
    }
]


def load_knowledge_base():
    """Generates FAQ file if missing, then loads it."""
    try:
        path = os.path.join(os.path.dirname(__file__), FAQ_FILE)
        if not os.path.exists(path):
            with open(path, "w", encoding='utf-8') as f:
                json.dump(DEFAULT_FAQ, f, indent=4)
        with open(path, "r", encoding='utf-8') as f:
            return json.dumps(json.load(f)) # Return as string for the Prompt
    except Exception as e:
        print(f"⚠️ Error loading FAQ: {e}")
        return ""

STORE_FAQ_TEXT = load_knowledge_base()

# ======================================================
# 💾 2. LEAD DATA STRUCTURE
# ======================================================

@dataclass
class LeadProfile:
    name: str | None = None
    company: str | None = None
    delivery_address: str | None = None
    role: str | None = None
    use_case: str | None = None
    team_size: str | None = None
    timeline: str | None = None
   
    def is_qualified(self):
        """Returns True if we have the minimum info (Name + Email + Use Case)"""
        return all([self.name, self.email, self.use_case])

@dataclass
class Userdata:
    lead_profile: LeadProfile



@function_tool
async def update_lead_profile(
    ctx: RunContext[Userdata],
    name: Annotated[Optional[str], Field(description="Customer's or restaurant owner's name")] = None,
    company: Annotated[Optional[str], Field(description="Restaurant or business name (if they are a partner)")] = None,
    delivery_address: Annotated[Optional[str], Field(description="Customer's delivery address for food orders")] = None,
    role: Annotated[Optional[str], Field(description="Their role, e.g., Restaurant owner, Manager, or Customer")] = None,
    use_case: Annotated[Optional[str], Field(description="What they want help with — food ordering, delivery support, or restaurant partnership")] = None,
    team_size: Annotated[Optional[str], Field(description="Restaurant staff size (for partners) or not applicable for customers")] = None,
    timeline: Annotated[Optional[str], Field(description="When they need support or want to get started (e.g., Immediately, this week)")] = None,
) -> str:
    """
    ✍️ Captures lead details provided by the user during conversation.
    Only call this when the user explicitly provides information.
    """
    profile = ctx.userdata.lead_profile
   
    # Update only fields that are provided (not None)
    if name: profile.name = name
    if company: profile.company = company
    if delivery_address: profile.delivery_address = delivery_address
    if role: profile.role = role
    if use_case: profile.use_case = use_case
    if team_size: profile.team_size = team_size
    if timeline: profile.timeline = timeline
   
    print(f"📝 UPDATING LEAD: {profile}")
    return "Lead profile updated. Continue the conversation."

@function_tool
async def submit_lead_and_end(
    ctx: RunContext[Userdata],
) -> str:
    """
    💾 Saves the lead to the database and signals the end of the call.
    Call this when the user says goodbye or 'that's all'.
    """
    profile = ctx.userdata.lead_profile
   
    # Save to JSON file (Append mode)
    db_path = os.path.join(os.path.dirname(__file__), LEADS_FILE)
   
    entry = asdict(profile)
    entry["timestamp"] = datetime.now().isoformat()
   

    existing_data = []
    if os.path.exists(db_path):
        try:
            with open(db_path, "r") as f:
                existing_data = json.load(f)
        except: pass
   
    existing_data.append(entry)
   
    with open(db_path, "w") as f:
        json.dump(existing_data, f, indent=4)
       
    print(f"✅ LEAD SAVED TO {LEADS_FILE}")
    return f"Lead saved. Summarize the call for the user: 'Thanks {profile.name}, I have your info regarding {profile.use_case}. We will email you at {profile.email}. Goodbye!'"


class SDRAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions=f"""
            You are 'Shaanu', a friendly and professional Sales Development Rep (SDR) for 'Zomato'.
           
            📘 **YOUR KNOWLEDGE BASE (FAQ):**
            {STORE_FAQ_TEXT}
           
            🎯 **YOUR GOAL:**
            1. Answer questions about Zomato’s food delivery, restaurant listings, Zomato Gold, and partner services using the FAQ.
            2. **QUALIFY THE LEAD:** Naturally ask for the following details during the chat:
               - Name
               - Restaurant / Role (or Customer role)
               - Delivery Address (for customers) or Business Address (for partners)
               - What are they looking for? (Use Case — ordering food, support, or partnership)
               - Timeline (When they need the order/support/activation?)
            
            ⚙️ **BEHAVIOR:**
                - **Be Conversational:** Don't interrogate the user. Answer their question, THEN ask for a detail.
                - *Example:* "Delivery charges depend on distance. By the way, may I have your delivery address?"
                - **Capture Data:** Use `update_lead_profile` immediately when the user provides any detail.
                - **Closing:** When the user is done, use `submit_lead_and_end`.
                - **Tone:** Warm, professional, empathetic, and helpful, pace should be moderate.
                
            🚫 **RESTRICTIONS:**
                - If you don't know an answer, say "I'll check with the Zomato support team and update you." (Don't guess pricing or policies).
            """,
            tools=[update_lead_profile, submit_lead_and_end],
        )



def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

   
   
    # 1. Initialize State
    userdata = Userdata(lead_profile=LeadProfile())

    # 2. Setup Agent
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(
            voice="en-US-natalie", # Professional, warm female voice
            style="Promo",        
            text_pacing=True,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        userdata=userdata,
    )
   
    # 3. Start
    await session.start(
        agent=SDRAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            #noise_cancellation=noise_cancellation.BVC()
        ),
    )

    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))

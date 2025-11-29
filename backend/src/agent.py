

import json
import logging
import os
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Annotated

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

from livekit.plugins import murf, silero, google, deepgram, noise_cancellation
from livekit.plugins.turn_detector.multilingual import MultilingualModel

# -------------------------
# Logging
# -------------------------
logger = logging.getLogger("voice_game_master")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(handler)

load_dotenv(".env.local")

# -------------------------
# Simple Game World Definition
# -------------------------
# A compact world with a few scenes and choices forming a mini-arc.
WORLD = {
    "intro": {
        "title": "Crash Site of Astra-9",
        "desc": (
            "You regain consciousness inside the shattered remains of the star-freighter Astra-9. "
            "Warning lights flicker, oxygen hisses from a cracked pipe, and sparks dance across torn metal. "
            "Outside the broken hull, a dense alien jungle glows faintly in shades of blue. "
            "Beside you lies a damaged datapad blinking with a corrupted message."
        ),
        "choices": {
            "inspect_datapad": {
                "desc": "Check the damaged datapad and try to read the message.",
                "result_scene": "datapad",
            },
            "step_outside": {
                "desc": "Leave the wreckage and explore the glowing alien jungle.",
                "result_scene": "jungle_edge",
            },
            "search_cockpit": {
                "desc": "Search the cockpit area for survivors or tools.",
                "result_scene": "cockpit",
            },
        },
    },

    "datapad": {
        "title": "The Damaged Datapad",
        "desc": (
            "The datapad flickers with static before revealing part of a transmission: "
            "'—survivor… find the core shard… beacon offline… danger in the trees.' "
            "A distorted map fragment appears, highlighting a location deeper in the jungle."
        ),
        "choices": {
            "take_datapad": {
                "desc": "Take the datapad with you.",
                "result_scene": "jungle_path",
                "effects": {"add_journal": "Recovered datapad with message: 'Find the core shard.'"},
            },
            "leave_it": {
                "desc": "Leave the datapad and return to the wreckage.",
                "result_scene": "intro",
            },
        },
    },

    "cockpit": {
        "title": "The Broken Cockpit",
        "desc": (
            "The cockpit is crushed, but you spot a flickering emergency console. "
            "A sealed locker sits half-buried beneath debris. Through the cracked viewport, "
            "the jungle pulses with faint bioluminescent shapes."
        ),
        "choices": {
            "open_locker": {
                "desc": "Try to pry open the sealed locker.",
                "result_scene": "locker_fail",
            },
            "check_console": {
                "desc": "Activate the emergency console.",
                "result_scene": "console_scan",
            },
            "return_outside": {
                "desc": "Go back to the crash exit.",
                "result_scene": "intro",
            },
        },
    },

    "locker_fail": {
        "title": "Stuck Locker",
        "desc": (
            "You strain against the locker, but it won't budge. Something metallic rattles inside. "
            "You hear a distant roar from the jungle—something large is nearby."
        ),
        "choices": {
            "give_up_and_leave": {
                "desc": "Leave the cockpit quickly.",
                "result_scene": "intro",
            },
            "try_again": {
                "desc": "Try the locker again despite the noise.",
                "result_scene": "locker_open",
            },
        },
    },

    "locker_open": {
        "title": "Success — A Tool Found",
        "desc": (
            "With a final pull, the locker pops open. Inside is a compact plasma cutter with low charge. "
            "A useful tool, but fragile."
        ),
        "choices": {
            "take_cutter": {
                "desc": "Take the plasma cutter.",
                "result_scene": "intro",
                "effects": {"add_inventory": "plasma_cutter", "add_journal": "Found a plasma cutter in cockpit."},
            },
            "leave_it": {
                "desc": "Leave the cutter, unsure if it's safe.",
                "result_scene": "intro",
            },
        },
    },

    "console_scan": {
        "title": "Emergency Console",
        "desc": (
            "The console sparks to life. It performs a quick scan and displays: "
            "'Energy anomaly detected — core shard located in nearby ruins. "
            "Warning: indigenous lifeforms hostile.'"
        ),
        "choices": {
            "head_to_ruins": {
                "desc": "Set out toward the ruins.",
                "result_scene": "ruins_approach",
            },
            "return_to_crash": {
                "desc": "Return to the crash site.",
                "result_scene": "intro",
            },
        },
    },

    "jungle_edge": {
        "title": "Edge of the Jungle",
        "desc": (
            "Giant trees glow faintly with blue veins of light. Strange insects hum through the air. "
            "A narrow path leads deeper in, while an animal trail branches left."
        ),
        "choices": {
            "take_main_path": {
                "desc": "Walk along the narrow glowing path.",
                "result_scene": "jungle_path",
            },
            "follow_animal_trail": {
                "desc": "Follow the animal trail into the dense undergrowth.",
                "result_scene": "creature_encounter",
            },
            "go_back": {
                "desc": "Return to the wreckage.",
                "result_scene": "intro",
            },
        },
    },

    "jungle_path": {
        "title": "Path of Light",
        "desc": (
            "The path winds between trees and pulses with soft light. Ahead, you hear running water and "
            "catch a glimpse of ancient ruins wrapped in vines."
        ),
        "choices": {
            "approach_ruins": {
                "desc": "Head toward the ruins.",
                "result_scene": "ruins_approach",
            },
            "explore_stream": {
                "desc": "Investigate the sound of flowing water.",
                "result_scene": "stream_area",
            },
            "return_to_crash": {
                "desc": "Turn back to the ship.",
                "result_scene": "intro",
            },
        },
    },

    "creature_encounter": {
        "title": "Hostile Presence",
        "desc": (
            "From the shadows, a four-legged bioluminescent beast emerges, eyes glowing amber. "
            "It growls, blocking your path."
        ),
        "choices": {
            "run": {
                "desc": "Retreat quickly.",
                "result_scene": "jungle_edge",
            },
            "stand_firm": {
                "desc": "Stand your ground and face the creature.",
                "result_scene": "creature_outcome",
            },
        },
    },

    "creature_outcome": {
        "title": "Narrow Escape",
        "desc": (
            "You clap, shout, and wave your arms. The creature pauses, snorts, then slinks away. "
            "It leaves behind glowing tracks pointing toward the ruins."
        ),
        "choices": {
            "follow_tracks": {
                "desc": "Follow the creature's tracks toward the ruins.",
                "result_scene": "ruins_approach",
            },
            "go_back": {
                "desc": "Return to the safer main path.",
                "result_scene": "jungle_path",
            },
        },
    },

    "stream_area": {
        "title": "Bioluminescent Stream",
        "desc": (
            "A glowing stream flows gently. Something metallic lies half-buried near the bank — "
            "it looks like a broken beacon piece."
        ),
        "choices": {
            "collect_piece": {
                "desc": "Collect the broken beacon part.",
                "result_scene": "jungle_path",
                "effects": {"add_inventory": "beacon_fragment", "add_journal": "Recovered beacon fragment near stream."},
            },
            "leave_it": {
                "desc": "Leave the object and return to the path.",
                "result_scene": "jungle_path",
            },
        },
    },

    "ruins_approach": {
        "title": "The Ancient Ruins",
        "desc": (
            "Vines cover circular stone structures etched with alien symbols. At the center lies "
            "a pedestal holding a floating crystalline shard pulsing with energy — the core shard."
        ),
        "choices": {
            "take_shard": {
                "desc": "Reach out and take the core shard.",
                "result_scene": "shard_taken",
                "effects": {"add_inventory": "core_shard", "add_journal": "Recovered the core shard."},
            },
            "inspect_ruins": {
                "desc": "Walk around and study the markings.",
                "result_scene": "ruins_hint",
            },
            "retreat": {
                "desc": "Leave the ruins.",
                "result_scene": "jungle_path",
            },
        },
    },

    "ruins_hint": {
        "title": "Symbols of Warning",
        "desc": (
            "The symbols depict a falling star, a shattered ship, and a figure holding a glowing shard. "
            "The message seems clear: the shard powers something vital."
        ),
        "choices": {
            "take_shard": {
                "desc": "Take the shard now that you understand its importance.",
                "result_scene": "shard_taken",
            },
            "return": {
                "desc": "Return to the main ruin chamber.",
                "result_scene": "ruins_approach",
            },
        },
    },

    "shard_taken": {
        "title": "A Spark of Hope",
        "desc": (
            "As you grasp the shard, energy surges through your arm. The jungle quiets. "
            "Far away, the Astra-9's emergency beacon flickers back to life. "
            "Rescue may finally be possible."
        ),
        "choices": {
            "end_session": {
                "desc": "End the session and return to the jungles' edge — adventure complete.",
                "result_scene": "intro",
            },
            "keep_exploring": {
                "desc": "Stay and explore more of the ruins.",
                "result_scene": "ruins_approach",
            },
        },
    },
}


# -------------------------
# Per-session Userdata
# -------------------------
@dataclass
class Userdata:
    player_name: Optional[str] = None
    current_scene: str = "intro"
    history: List[Dict] = field(default_factory=list)  # list of {'scene', 'action', 'time', 'result_scene'}
    journal: List[str] = field(default_factory=list)
    inventory: List[str] = field(default_factory=list)
    named_npcs: Dict[str, str] = field(default_factory=dict)
    choices_made: List[str] = field(default_factory=list)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

# -------------------------
# Helper functions
# -------------------------
def scene_text(scene_key: str, userdata: Userdata) -> str:
    """
    Build the descriptive text for the current scene, and append choices as short hints.
    Always end with 'What do you do?' so the voice flow prompts player input.
    """
    scene = WORLD.get(scene_key)
    if not scene:
        return "You are in a featureless void. What do you do?"

    desc = f"{scene['desc']}\n\nChoices:\n"
    for cid, cmeta in scene.get("choices", {}).items():
        desc += f"- {cmeta['desc']} (say: {cid})\n"
    # GM MUST end with the action prompt
    desc += "\nWhat do you do?"
    return desc

def apply_effects(effects: dict, userdata: Userdata):
    if not effects:
        return
    if "add_journal" in effects:
        userdata.journal.append(effects["add_journal"])
    if "add_inventory" in effects:
        userdata.inventory.append(effects["add_inventory"])
    # Extendable for more effect keys

def summarize_scene_transition(old_scene: str, action_key: str, result_scene: str, userdata: Userdata) -> str:
    """Record the transition into history and return a short narrative the GM can use."""
    entry = {
        "from": old_scene,
        "action": action_key,
        "to": result_scene,
        "time": datetime.utcnow().isoformat() + "Z",
    }
    userdata.history.append(entry)
    userdata.choices_made.append(action_key)
    return f"You chose '{action_key}'."

# -------------------------
# Agent Tools (function_tool)
# -------------------------

@function_tool
async def start_adventure(
    ctx: RunContext[Userdata],
    player_name: Annotated[Optional[str], Field(description="Player name", default=None)] = None,
) -> str:
    """Initialize a new adventure session for the player and return the opening description."""
    userdata = ctx.userdata
    if player_name:
        userdata.player_name = player_name
    userdata.current_scene = "intro"
    userdata.history = []
    userdata.journal = []
    userdata.inventory = []
    userdata.named_npcs = {}
    userdata.choices_made = []
    userdata.session_id = str(uuid.uuid4())[:8]
    userdata.started_at = datetime.utcnow().isoformat() + "Z"

    opening = (
        f"Greetings {userdata.player_name or 'traveler'}. Welcome to '{WORLD['intro']['title']}'.\n\n"
        + scene_text("intro", userdata)
    )
    # Ensure GM prompt present
    if not opening.endswith("What do you do?"):
        opening += "\nWhat do you do?"
    return opening

@function_tool
async def get_scene(
    ctx: RunContext[Userdata],
) -> str:
    """Return the current scene description (useful for 'remind me where I am')."""
    userdata = ctx.userdata
    scene_k = userdata.current_scene or "intro"
    txt = scene_text(scene_k, userdata)
    return txt

@function_tool
async def player_action(
    ctx: RunContext[Userdata],
    action: Annotated[str, Field(description="Player spoken action or the short action code (e.g., 'inspect_box' or 'take the box')")],
) -> str:
    """
    Accept player's action (natural language or action key), try to resolve it to a defined choice,
    update userdata, advance to the next scene and return the GM's next description (ending with 'What do you do?').
    """
    userdata = ctx.userdata
    current = userdata.current_scene or "intro"
    scene = WORLD.get(current)
    action_text = (action or "").strip()

    # Attempt 1: match exact action key (e.g., 'inspect_box')
    chosen_key = None
    if action_text.lower() in (scene.get("choices") or {}):
        chosen_key = action_text.lower()

    # Attempt 2: fuzzy match by checking if action_text contains the choice key or descriptive words
    if not chosen_key:
        # try to find a choice whose description words appear in action_text
        for cid, cmeta in (scene.get("choices") or {}).items():
            desc = cmeta.get("desc", "").lower()
            if cid in action_text.lower() or any(w in action_text.lower() for w in desc.split()[:4]):
                chosen_key = cid
                break

    # Attempt 3: fallback by simple keyword matching against choice descriptions
    if not chosen_key:
        for cid, cmeta in (scene.get("choices") or {}).items():
            for keyword in cmeta.get("desc", "").lower().split():
                if keyword and keyword in action_text.lower():
                    chosen_key = cid
                    break
            if chosen_key:
                break

    if not chosen_key:
        # If we still can't resolve, ask a clarifying GM response but keep it short and end with prompt.
        resp = (
            "I didn't quite catch that action for this situation. Try one of the listed choices or use a simple phrase like 'inspect the box' or 'go to the tower'.\n\n"
            + scene_text(current, userdata)
        )
        return resp

    # Apply the chosen choice
    choice_meta = scene["choices"].get(chosen_key)
    result_scene = choice_meta.get("result_scene", current)
    effects = choice_meta.get("effects", None)

    # Apply effects (inventory/journal, etc.)
    apply_effects(effects or {}, userdata)

    # Record transition
    _note = summarize_scene_transition(current, chosen_key, result_scene, userdata)

    # Update current scene
    userdata.current_scene = result_scene

    # Build narrative reply: echo a short confirmation, then describe next scene
    next_desc = scene_text(result_scene, userdata)

    # A small flourish so the GM sounds more persona-driven
    persona_pre = (
        "The Game Master (a calm, slightly mysterious narrator) replies:\n\n"
    )
    reply = f"{persona_pre}{_note}\n\n{next_desc}"
    # ensure final prompt present
    if not reply.endswith("What do you do?"):
        reply += "\nWhat do you do?"
    return reply

@function_tool
async def show_journal(
    ctx: RunContext[Userdata],
) -> str:
    userdata = ctx.userdata
    lines = []
    lines.append(f"Session: {userdata.session_id} | Started at: {userdata.started_at}")
    if userdata.player_name:
        lines.append(f"Player: {userdata.player_name}")
    if userdata.journal:
        lines.append("\nJournal entries:")
        for j in userdata.journal:
            lines.append(f"- {j}")
    else:
        lines.append("\nJournal is empty.")
    if userdata.inventory:
        lines.append("\nInventory:")
        for it in userdata.inventory:
            lines.append(f"- {it}")
    else:
        lines.append("\nNo items in inventory.")
    lines.append("\nRecent choices:")
    for h in userdata.history[-6:]:
        lines.append(f"- {h['time']} | from {h['from']} -> {h['to']} via {h['action']}")
    lines.append("\nWhat do you do?")
    return "\n".join(lines)

@function_tool
async def restart_adventure(
    ctx: RunContext[Userdata],
) -> str:
    """Reset the userdata and start again."""
    userdata = ctx.userdata
    userdata.current_scene = "intro"
    userdata.history = []
    userdata.journal = []
    userdata.inventory = []
    userdata.named_npcs = {}
    userdata.choices_made = []
    userdata.session_id = str(uuid.uuid4())[:8]
    userdata.started_at = datetime.utcnow().isoformat() + "Z"
    greeting = (
        "The world resets. A new tide laps at the shore. You stand once more at the beginning.\n\n"
        + scene_text("intro", userdata)
    )
    if not greeting.endswith("What do you do?"):
        greeting += "\nWhat do you do?"
    return greeting

# -------------------------
# The Agent (GameMasterAgent)
# -------------------------
class GameMasterAgent(Agent):
    def __init__(self):
        # System instructions define Universe, Tone, Role
        instructions = """
        You are 'Shaan', the Game Master (GM) for a voice-only sci-fi survival adventure called **Echoes of Astra-9**.

        Universe: A mysterious alien jungle on a distant planet after the crash of star-freighter Astra-9.
        Tone: Atmospheric, cinematic, slightly tense, but always encouraging and player-friendly.
        Role: You narrate scenes, guide the story, remember the player's past choices, track inventory & journal, 
            and ALWAYS end with the prompt: 'What do you do?'

        Rules:
            - Use the provided tools to start the adventure, get the current scene, accept player actions,
            show the journal, or restart the adventure.
            - Maintain continuity using userdata (player name, inventory, choices, journal).
            - Be descriptive but concise (voice-optimized).
            - Drive the session forward with meaningful choices and reactions.
            - ALWAYS end every GM message with: 'What do you do?'
        """

        super().__init__(
            instructions=instructions,
            tools=[start_adventure, get_scene, player_action, show_journal, restart_adventure],
        )

# -------------------------
# Entrypoint & Prewarm (keeps speech functionality)
# -------------------------
def prewarm(proc: JobProcess):
    # load VAD model and stash on process userdata, try/catch like original file
    try:
        proc.userdata["vad"] = silero.VAD.load()
    except Exception:
        logger.warning("VAD prewarm failed; continuing without preloaded VAD.")

async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}
    

    userdata = Userdata()

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=google.LLM(model="gemini-2.5-flash"),
        tts=murf.TTS(
            voice="en-US-marcus",
            style="Conversational",
            text_pacing=True,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata.get("vad"),
        userdata=userdata,
    )

    # Start the agent session with the GameMasterAgent
    await session.start(
        agent=GameMasterAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()),
    )

    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))

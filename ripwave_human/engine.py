"""Deterministic, bounded ripple-field core inspired by Ripwave concepts."""
from __future__ import annotations
import math
import time
import uuid

ALLOWED_INTENTS = {
    "start_ripple", "expand", "contract", "raise_right_arm", "lower_right_arm",
    "reach_forward", "walking", "stop", "neutral", "reset", "audio_modulate"
}

_COMMANDS = (
    ("stop", ("stop walking", "stop the walk", "stop", "halt", "pause")),
    ("neutral", ("return to neutral", "back to neutral", "neutral", "reset", "reset simulation", "reset human", "reset model")),
    ("raise_right_arm", ("raise right arm", "raise your right arm", "lift right arm", "lift your right arm")),
    ("lower_right_arm", ("lower right arm", "lower your right arm")),
    ("reach_forward", ("reach forward", "reach forward with your right arm")),
    ("walking", ("start walking", "begin walking", "walk", "walking")),
    ("start_ripple", ("start ripple", "start the ripple", "begin ripple", "activate ripple")),
    ("expand", ("expand", "expand field", "expand the field", "grow field", "grow the field")),
    ("contract", ("contract", "contract field", "contract the field", "shrink field", "shrink the field")),
    ("audio_modulate", ("modulate audio", "follow audio", "use audio")),
)

def normalize_command(text):
    """Map a complete, normalized phrase to an explicit intent; never substring-match."""
    if not isinstance(text, str):
        return None
    phrase = " ".join(text.casefold().strip().replace("’", "'").split())
    phrase = phrase.strip(" \t\r\n.,!?;:")
    phrase = phrase.removeprefix("please ")
    phrase = phrase.removeprefix("could you ")
    phrase = phrase.removeprefix("can you ")
    phrase = " ".join(phrase.split())
    for intent, phrases in _COMMANDS:
        if phrase in phrases:
            return intent
    return None

def clamp(value, lo, hi):
    return max(lo, min(hi, value))

def validate_event(payload):
    if not isinstance(payload, dict):
        raise ValueError("Event must be a JSON object.")
    if payload.get("schema_version", "1.0") != "1.0":
        raise ValueError("Unsupported schema_version.")
    transcript = payload.get("transcript", payload.get("text", ""))
    if not isinstance(transcript, str) or len(transcript) > 300:
        raise ValueError("transcript must be a string of at most 300 characters.")
    intent = payload.get("intent")
    if transcript:
        parsed_intent = normalize_command(transcript)
        if parsed_intent is None:
            raise ValueError("Command not recognized. Try ‘raise your right arm’, ‘start walking’, ‘stop’, or ‘return to neutral’.")
        if intent is not None and intent != parsed_intent:
            raise ValueError("Command text and intent do not match. Please submit the phrase again.")
        intent = parsed_intent
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"Unsupported intent: {intent!r}")
    audio = payload.get("acoustics") or {}
    if not isinstance(audio, dict):
        raise ValueError("acoustics must be an object.")
    rms = float(audio.get("rms_energy", 0.0))
    centroid = float(audio.get("spectral_centroid_hz", 0.0))
    if not math.isfinite(rms) or not math.isfinite(centroid):
        raise ValueError("Audio features must be finite numbers.")
    confidence = float(payload.get("confidence", 1.0))
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1.")
    if confidence < 0.55 and intent not in {"stop"}:
        raise ValueError("Low-confidence event rejected; confirm the command.")
    return {
        "schema_version": "1.0",
        "event_id": str(payload.get("event_id") or uuid.uuid4()),
        "source": str(payload.get("source", "text"))[:32],
        "transcript": transcript[:300],
        "intent": intent,
        "confidence": confidence,
        "acoustics": {
            "rms_energy": clamp(rms, 0.0, 1.0),
            "spectral_centroid_hz": clamp(centroid, 0.0, 12000.0),
        },
        "timestamp": time.time(),
    }

class RippleEngine:
    """Small deterministic field-state model; radius is a world-space meter value."""
    def __init__(self):
        self.amplitude = 0.35
        self.frequency_hz = 1.0
        self.radius = 0.28
        self.active = False
        self.last_intent = "idle"
        self.revision = 0
        self.last_updated = time.time()

    def apply(self, event):
        intent = event["intent"]
        rms = event["acoustics"]["rms_energy"]
        centroid = event["acoustics"]["spectral_centroid_hz"]
        if intent == "start_ripple":
            self.active = True
            self.radius = 0.28
        elif intent == "expand":
            self.active = True
            self.radius = clamp(self.radius + 0.12, 0.08, 1.0)
        elif intent == "contract":
            self.radius = clamp(self.radius - 0.12, 0.08, 1.0)
        elif intent == "audio_modulate":
            self.active = True
        elif intent == "stop":
            self.active = False
        elif intent in {"neutral", "reset"}:
            self.active = False
            self.radius = 0.28
        elif intent == "raise_right_arm":
            # Intent is logged for the simulation adapter; no actuator control occurs.
            self.active = True
        elif intent == "walking":
            # Preserve the field and record the unsupported gait intent only.
            pass
        if event["source"] == "voice" or intent == "audio_modulate":
            self.amplitude = clamp(0.18 + 0.55 * rms, 0.12, 0.8)
            self.frequency_hz = clamp(0.5 + centroid / 5000.0, 0.5, 3.0)
        self.last_intent = intent
        self.revision += 1
        self.last_updated = time.time()
        return {
            "event_id": event["event_id"],
            "command_id": event["event_id"],
            "accepted": True,
            "intent": intent,
            "feedback": "Command accepted for processing.",
            "field_revision": self.revision,
        }

    def snapshot(self):
        return {
            "amplitude": round(self.amplitude, 4),
            "frequency_hz": round(self.frequency_hz, 4),
            "radius": round(self.radius, 4),
            "active": self.active,
            "last_intent": self.last_intent,
            "revision": self.revision,
            "updated_at": self.last_updated,
        }

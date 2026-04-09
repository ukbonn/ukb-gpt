import json
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


CONVERSATION_LANGUAGE_CHOICES = (
    "German",
    "Arabic",
    "English",
    "Turkish",
    "French",
    "Spanish",
    "Ukrainian",
    "Russian",
    "Polish",
    "Romanian",
    "Italian",
    "Kurdish",
    "Persian",
    "Dari",
    "Pashto",
    "Urdu",
    "Hindi",
    "Bengali",
    "Punjabi",
    "Tamil",
    "Telugu",
    "Mandarin Chinese",
    "Cantonese",
    "Vietnamese",
    "Thai",
    "Greek",
    "Serbian",
    "Croatian",
    "Bosnian",
    "Bulgarian",
    "Hungarian",
    "Czech",
    "Slovak",
    "Dutch",
    "Portuguese",
    "Albanian",
    "Tigrinya",
    "Amharic",
    "Somali",
    "Hebrew",
)

_LANGUAGE_ALIAS_MAP = {
    "de": "German",
    "deutsch": "German",
    "german": "German",
    "ar": "Arabic",
    "arabic": "Arabic",
    "en": "English",
    "english": "English",
    "tr": "Turkish",
    "turkish": "Turkish",
    "fr": "French",
    "french": "French",
    "es": "Spanish",
    "spanish": "Spanish",
    "uk": "Ukrainian",
    "ukrainian": "Ukrainian",
    "ru": "Russian",
    "russian": "Russian",
    "pl": "Polish",
    "polish": "Polish",
    "ro": "Romanian",
    "romanian": "Romanian",
    "it": "Italian",
    "italian": "Italian",
    "ku": "Kurdish",
    "kurdish": "Kurdish",
    "fa": "Persian",
    "farsi": "Persian",
    "persian": "Persian",
    "prs": "Dari",
    "dari": "Dari",
    "ps": "Pashto",
    "pashto": "Pashto",
    "ur": "Urdu",
    "urdu": "Urdu",
    "hi": "Hindi",
    "hindi": "Hindi",
    "bn": "Bengali",
    "bengali": "Bengali",
    "pa": "Punjabi",
    "punjabi": "Punjabi",
    "ta": "Tamil",
    "tamil": "Tamil",
    "te": "Telugu",
    "telugu": "Telugu",
    "zh": "Mandarin Chinese",
    "chinese": "Mandarin Chinese",
    "mandarin": "Mandarin Chinese",
    "mandarin chinese": "Mandarin Chinese",
    "yue": "Cantonese",
    "cantonese": "Cantonese",
    "vi": "Vietnamese",
    "vietnamese": "Vietnamese",
    "th": "Thai",
    "thai": "Thai",
    "el": "Greek",
    "greek": "Greek",
    "sr": "Serbian",
    "serbian": "Serbian",
    "hr": "Croatian",
    "croatian": "Croatian",
    "bs": "Bosnian",
    "bosnian": "Bosnian",
    "bg": "Bulgarian",
    "bulgarian": "Bulgarian",
    "hu": "Hungarian",
    "hungarian": "Hungarian",
    "cs": "Czech",
    "czech": "Czech",
    "sk": "Slovak",
    "slovak": "Slovak",
    "nl": "Dutch",
    "dutch": "Dutch",
    "pt": "Portuguese",
    "portuguese": "Portuguese",
    "sq": "Albanian",
    "albanian": "Albanian",
    "ti": "Tigrinya",
    "tigrinya": "Tigrinya",
    "am": "Amharic",
    "amharic": "Amharic",
    "so": "Somali",
    "somali": "Somali",
    "he": "Hebrew",
    "iw": "Hebrew",
    "hebrew": "Hebrew",
}


ConversationLanguage = Enum(
    "ConversationLanguage",
    {re.sub(r"[^A-Z0-9]+", "_", value.upper()): value for value in CONVERSATION_LANGUAGE_CHOICES},
    type=str,
)


class StructuredTranscription(BaseModel):
    spoken_language: ConversationLanguage
    transcription: str = Field(min_length=1)

    @field_validator("transcription", mode="before")
    @classmethod
    def _strip_transcription(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("transcription must not be empty")
        return text


def canonicalize_conversation_language(raw_language: str, fallback: str) -> str:
    normalized_fallback = (fallback or "").strip() or "German"
    raw = (raw_language or "").strip()
    if not raw:
        return normalized_fallback

    alias = _LANGUAGE_ALIAS_MAP.get(raw.casefold())
    if alias:
        return alias

    for candidate in CONVERSATION_LANGUAGE_CHOICES:
        if candidate.casefold() == raw.casefold():
            return candidate

    return normalized_fallback


def transcription_response_format(name: str = "dictation_transcription") -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "schema": StructuredTranscription.model_json_schema(),
        },
    }


def parse_structured_transcription(raw_text: str) -> StructuredTranscription:
    candidates = [raw_text.strip()]
    normalized = raw_text.strip()
    if normalized.startswith("```"):
        fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", normalized, flags=re.DOTALL)
        candidates.append(fenced.strip())
    start = normalized.find("{")
    end = normalized.rfind("}")
    if start != -1 and end > start:
        candidates.append(normalized[start : end + 1].strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        try:
            return StructuredTranscription.model_validate(payload)
        except ValidationError:
            continue

    raise ValueError("Could not parse structured transcription JSON.")

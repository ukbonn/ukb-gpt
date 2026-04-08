import asyncio
import audioop
import base64
import json
import os
import re
import tempfile
import uuid
import urllib.request
import wave
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote

try:
    import websockets
except ModuleNotFoundError:  # pragma: no cover - exercised in local test envs
    websockets = None


STT_BASE_URL = (
    os.getenv("DICTATION_STT_BASE_URL")
    or f"http://{os.getenv('STT_ENDPOINT', 'stt_worker_0:5000')}/v1"
).rstrip("/")
STT_MODEL = os.getenv(
    "DICTATION_STT_MODEL",
    os.getenv("STT_MODEL_ID", "mistralai/Voxtral-Mini-4B-Realtime-2602"),
)
STT_API_KEY = os.getenv("DICTATION_API_KEY", "")
DICTATION_REQUEST_MODE = os.getenv("DICTATION_REQUEST_MODE", "auto").strip().lower()
REQUEST_TIMEOUT_SECONDS = int(os.getenv("DICTATION_REQUEST_TIMEOUT_SECONDS", "300"))
MAX_AUDIO_BYTES = int(os.getenv("DICTATION_MAX_AUDIO_BYTES", str(50 * 1024 * 1024)))
DICTATION_PORT = int(os.getenv("DICTATION_PORT", "7860"))
DICTATION_ROOT_PATH = os.getenv("DICTATION_ROOT_PATH", "/dictation")
POST_PROC_CONFIG_PATH = Path(
    os.getenv("DICTATION_POSTPROC_CONFIG_PATH", "/app/dictation_postprocessing.json")
)
DICTATION_TRANSLATION_PROMPT_PATH = Path(
    os.getenv("DICTATION_TRANSLATION_PROMPT_PATH", "/app/translate_prompt.txt")
)
DICTATION_TRANSCRIPTION_PROMPT_PATH = Path(
    os.getenv("DICTATION_TRANSCRIPTION_PROMPT_PATH", "/app/transcription_prompt.txt")
)
DICTATION_BACK_TRANSLATION_PROMPT_PATH = Path(
    os.getenv("DICTATION_BACK_TRANSLATION_PROMPT_PATH", "/app/back_translation_prompt.txt")
)
_raw_llm_url = os.getenv("DICTATION_LLM_BASE_URL", "").strip()
if _raw_llm_url:
    DICTATION_LLM_BASE_URL = _raw_llm_url.rstrip("/")
else:
    primary_llm = os.getenv(
        "PRIMARY_OPENAI_ENDPOINT", os.getenv("LLM_ENDPOINT", "backend_router:5000")
    ).strip()
    if not primary_llm:
        primary_llm = "backend_router:5000"
    if "://" not in primary_llm:
        primary_llm = f"http://{primary_llm}"
    DICTATION_LLM_BASE_URL = primary_llm.rstrip("/")
    if not DICTATION_LLM_BASE_URL.endswith("/v1"):
        DICTATION_LLM_BASE_URL = f"{DICTATION_LLM_BASE_URL}/v1"
DICTATION_TRANSLATION_MODEL = os.getenv("DICTATION_TRANSLATION_MODEL", "").strip()
DICTATION_TRANSLATION_TIMEOUT_SECONDS = int(
    os.getenv("DICTATION_TRANSLATION_TIMEOUT_SECONDS", "120")
)
DICTATION_TRANSLATION_API_KEY = os.getenv("DICTATION_TRANSLATION_API_KEY", "")
_raw_tts_url = os.getenv("DICTATION_TTS_BASE_URL", "").strip()
if _raw_tts_url:
    DICTATION_TTS_BASE_URL = _raw_tts_url.rstrip("/")
else:
    tts_endpoint = os.getenv("TTS_ENDPOINT", "").strip()
    if tts_endpoint:
        if "://" not in tts_endpoint:
            tts_endpoint = f"http://{tts_endpoint}"
        DICTATION_TTS_BASE_URL = tts_endpoint.rstrip("/")
        if not DICTATION_TTS_BASE_URL.endswith("/v1"):
            DICTATION_TTS_BASE_URL = f"{DICTATION_TTS_BASE_URL}/v1"
    else:
        DICTATION_TTS_BASE_URL = ""
DICTATION_TTS_MODEL = os.getenv("DICTATION_TTS_MODEL", os.getenv("TTS_MODEL_ID", "")).strip()
DICTATION_TTS_DEFAULT_VOICE = (
    os.getenv("DICTATION_TTS_DEFAULT_VOICE", "casual_male").strip() or "casual_male"
)
DICTATION_TTS_RESPONSE_FORMAT = (
    os.getenv("DICTATION_TTS_RESPONSE_FORMAT", "wav").strip().lower() or "wav"
)
DICTATION_TTS_TIMEOUT_SECONDS = int(os.getenv("DICTATION_TTS_TIMEOUT_SECONDS", "300"))

_DEFAULT_TRANSCRIPTION_PROMPT = """Transcribe the following speech segment in {{INPUT_LANGUAGE}} into {{OUTPUT_LANGUAGE}} text.

Follow these specific instructions for formatting the answer:
* Only output the transcription, with no newlines.
* When transcribing numbers, write the digits, i.e. write 1.7 and not one point seven, and write 3 instead of three.
* The user that sent the segment works in a medical facility, so if you are not sure about words consider a clinical context.
"""

_DEFAULT_BACK_TRANSLATION_PROMPT = """Translate the following {{OUTPUT_LANGUAGE}} text into {{INPUT_LANGUAGE}}.

Return only the translated text on one line.

{{TEXT}}"""

_DEFAULT_TRANSLATION_PROMPT = """Translate the following transcript from {{INPUT_LANGUAGE}} into {{OUTPUT_LANGUAGE}}.

Return only the translated text on one line.

{{TEXT}}"""

_FINAL_ANSWER_MARKER = "FINAL_ANSWER:"


def _extract_transcript(raw_payload: str) -> str:
    payload = raw_payload.strip()
    if not payload:
        return ""

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload

    if not isinstance(data, dict):
        return payload

    for key in ("text", "transcript", "output_text"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    segments = data.get("segments")
    if isinstance(segments, list):
        joined = " ".join(
            segment.get("text", "").strip()
            for segment in segments
            if isinstance(segment, dict) and isinstance(segment.get("text"), str)
        ).strip()
        if joined:
            return joined

    return payload


def _raise_for_stt_error_payload(raw_payload: str) -> None:
    payload = raw_payload.strip()
    if not payload:
        return

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return

    if not isinstance(data, dict):
        return

    error_payload = data.get("error")
    if isinstance(error_payload, dict):
        message = error_payload.get("message")
        if isinstance(message, str) and message.strip():
            raise RuntimeError(f"STT backend returned error: {message.strip()}")
        raise RuntimeError("STT backend returned an error payload.")


def clean_text_structure(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"([.,;:!?])(?:\s*\1)+", r"\1", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"\.\s*,", ".", text)

    high_punct = r"[:!?;()]"
    text = re.sub(rf"[.,]\s*({high_punct})", r"\1", text)
    text = re.sub(rf"({high_punct})\s*[.,]", r"\1", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\(\s*,", "(", text)
    text = re.sub(r",\s*\)", ")", text)
    text = re.sub(r"([.,;:!?])(?=[a-zA-ZäöüÄÖÜ0-9])", r"\1 ", text)
    text = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", text)

    def _capitalize_match(match: re.Match[str]) -> str:
        return match.group(1) + " " + match.group(2).upper()

    text = re.sub(r"([.!?])\s+([a-zäöü])", _capitalize_match, text)
    text = re.sub(r"\n\s*[.,;:]", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def apply_post_processing(text_input):
    if not text_input:
        return text_input

    def _load_replacements() -> dict[str, object]:
        if not POST_PROC_CONFIG_PATH.exists():
            return {}
        try:
            with POST_PROC_CONFIG_PATH.open("r", encoding="utf-8") as config_file:
                loaded = json.load(config_file)
            return loaded if isinstance(loaded, dict) else {}
        except Exception as exc:
            print(
                f"Failed to load post-processing config '{POST_PROC_CONFIG_PATH}': {exc}"
            )
            return {}

    def _to_bool_value(raw_value, default_value: bool) -> bool:
        return bool(raw_value) if isinstance(raw_value, bool) else default_value

    def process_string(txt: str) -> str:
        if not txt:
            return ""

        cleaned_text = txt
        replacements = _load_replacements()
        if replacements:
            for search_term, config in replacements.items():
                if not isinstance(search_term, str) or not search_term:
                    continue

                replacement = ""
                is_case_sensitive = False
                is_whole_word = False
                consume_punct = False

                if isinstance(config, str):
                    replacement = config
                elif isinstance(config, dict):
                    replacement = str(config.get("replacement", ""))
                    is_case_sensitive = _to_bool_value(
                        config.get("case_sensitive"), False
                    )
                    is_whole_word = _to_bool_value(config.get("whole_word"), False)
                    consume_punct = _to_bool_value(
                        config.get("consume_punctuation"), False
                    )
                else:
                    replacement = str(config)

                flags = 0 if is_case_sensitive else re.IGNORECASE
                pattern = re.escape(search_term)
                if is_whole_word:
                    pattern = r"\b" + pattern + r"\b"
                if consume_punct:
                    pattern += r"\s*[.,;:!?]*"

                cleaned_text = re.sub(pattern, replacement, cleaned_text, flags=flags)

        return clean_text_structure(cleaned_text)

    if isinstance(text_input, str):
        return process_string(text_input)

    if isinstance(text_input, list):
        processed_segments = []
        for segment in text_input:
            if not isinstance(segment, dict):
                processed_segments.append(segment)
                continue

            updated = segment.copy()
            updated["text"] = process_string(segment.get("text", ""))
            processed_segments.append(updated)
        return processed_segments

    return text_input


def _load_prompt_template(path: Path, default_prompt: str) -> str:
    try:
        if not path.exists():
            return default_prompt
        prompt_content = path.read_text(encoding="utf-8").strip()
        return prompt_content if prompt_content else default_prompt
    except Exception as exc:
        print(f"Failed to load prompt '{path}': {exc}")
        return default_prompt


def _render_prompt_template(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered.strip()


def _with_final_answer_contract(prompt: str) -> str:
    prompt = prompt.strip()
    if _FINAL_ANSWER_MARKER in prompt:
        return prompt
    return (
        f"{prompt}\n\n"
        "You may think step by step before answering, but the user-facing output must stay strict.\n"
        f"End with exactly one line in this format: {_FINAL_ANSWER_MARKER} <text>\n"
        "Do not add any text after that final line."
    )


def _extract_message_content(payload: dict[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("LLM returned no choices.")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise RuntimeError("LLM returned an invalid choice payload.")

    message = choice.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("LLM returned no assistant message.")

    content = message.get("content")
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
        if text_parts:
            return "".join(text_parts)

    raise RuntimeError("LLM returned an empty assistant message.")


def _extract_final_answer(raw_text: str) -> str:
    normalized = (raw_text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return ""

    marker_index = normalized.rfind(_FINAL_ANSWER_MARKER)
    if marker_index != -1:
        final_segment = normalized[marker_index + len(_FINAL_ANSWER_MARKER) :].strip()
        if final_segment:
            return final_segment.splitlines()[0].strip()

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return ""

    for candidate in reversed(lines):
        lowered = candidate.casefold()
        if lowered == "thought":
            continue
        if lowered.startswith("here's a thinking process") or lowered.startswith(
            "here is a thinking process"
        ):
            continue
        if re.match(r"^(\d+\.|[-*])\s", candidate):
            continue
        return candidate

    return lines[-1]


def _language_descriptor(raw_language: str, *, fallback: str) -> str:
    language = (raw_language or "").strip()
    return language if language else fallback


def _output_language_matches_input(input_language: str, output_language: str) -> bool:
    normalized_output = (output_language or "").strip()
    if not normalized_output:
        return True
    normalized_input = (input_language or "").strip()
    return bool(normalized_input) and normalized_output.casefold() == normalized_input.casefold()


def _stt_model_supports_prompted_multilingual_output() -> bool:
    lowered = STT_MODEL.lower()
    return "gemma-4-e4b" in lowered


def _normalize_translation_base_url() -> str:
    if DICTATION_LLM_BASE_URL.endswith("/v1"):
        return DICTATION_LLM_BASE_URL
    return f"{DICTATION_LLM_BASE_URL.rstrip('/')}/v1"


def _normalize_tts_base_url() -> str:
    if not DICTATION_TTS_BASE_URL:
        return ""
    if DICTATION_TTS_BASE_URL.endswith("/v1"):
        return DICTATION_TTS_BASE_URL
    return f"{DICTATION_TTS_BASE_URL.rstrip('/')}/v1"


def _translation_api_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if DICTATION_TRANSLATION_API_KEY:
        headers["Authorization"] = f"Bearer {DICTATION_TRANSLATION_API_KEY}"
    return headers


def _tts_api_headers() -> dict[str, str]:
    return {"Content-Type": "application/json"}


def _resolve_translation_model() -> str:
    if DICTATION_TRANSLATION_MODEL:
        return DICTATION_TRANSLATION_MODEL
    if not DICTATION_LLM_BASE_URL:
        raise RuntimeError("Translation disabled: no configured LLM endpoint.")

    endpoint = f"{_normalize_translation_base_url()}/models"
    request = urllib.request.Request(endpoint, method="GET", headers=_translation_api_headers())
    with urllib.request.urlopen(
        request, timeout=DICTATION_TRANSLATION_TIMEOUT_SECONDS
    ) as response:
        models_payload = json.loads(response.read().decode("utf-8", errors="replace"))

    if not isinstance(models_payload, dict):
        raise RuntimeError("Failed to parse LLM model list payload.")

    data = models_payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                model_id = item.get("id")
                if isinstance(model_id, str) and model_id.strip():
                    return model_id.strip()

    if isinstance(data, str) and data.strip():
        return data.strip()

    raise RuntimeError("Could not discover a model id from the LLM endpoint.")


def _resolve_tts_model() -> str:
    if DICTATION_TTS_MODEL:
        return DICTATION_TTS_MODEL
    if not DICTATION_TTS_BASE_URL:
        raise RuntimeError("Read-aloud unavailable: no configured TTS endpoint.")

    endpoint = f"{_normalize_tts_base_url()}/models"
    request = urllib.request.Request(endpoint, method="GET", headers=_tts_api_headers())
    with urllib.request.urlopen(request, timeout=DICTATION_TTS_TIMEOUT_SECONDS) as response:
        models_payload = json.loads(response.read().decode("utf-8", errors="replace"))

    if not isinstance(models_payload, dict):
        raise RuntimeError("Failed to parse TTS model list payload.")

    data = models_payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                model_id = item.get("id")
                if isinstance(model_id, str) and model_id.strip():
                    return model_id.strip()

    if isinstance(data, str) and data.strip():
        return data.strip()

    raise RuntimeError("Could not discover a model id from the TTS endpoint.")


def _build_transcription_prompt(input_language: str, output_language: str) -> str:
    template = _load_prompt_template(
        DICTATION_TRANSCRIPTION_PROMPT_PATH,
        _DEFAULT_TRANSCRIPTION_PROMPT,
    )
    prompt = _render_prompt_template(
        template,
        {
            "{{INPUT_LANGUAGE}}": _language_descriptor(
                input_language,
                fallback="the spoken language in the audio",
            ),
            "{{OUTPUT_LANGUAGE}}": _language_descriptor(
                output_language,
                fallback="the same language as the speaker",
            ),
        },
    )
    return _with_final_answer_contract(prompt)


def _build_translation_prompt(text: str, input_language: str, output_language: str) -> str:
    template = _load_prompt_template(
        DICTATION_TRANSLATION_PROMPT_PATH,
        _DEFAULT_TRANSLATION_PROMPT,
    )
    prompt = _render_prompt_template(
        template,
        {
            "{{TEXT}}": text,
            "{{INPUT_LANGUAGE}}": _language_descriptor(
                input_language,
                fallback="the original source language",
            ),
            "{{OUTPUT_LANGUAGE}}": _language_descriptor(
                output_language,
                fallback="the requested target language",
            ),
            "{{TARGET_LANGAUGE}}": _language_descriptor(
                output_language,
                fallback="the requested target language",
            ),
            "{{TARGET_LANGUAGE}}": _language_descriptor(
                output_language,
                fallback="the requested target language",
            ),
        },
    )
    return _with_final_answer_contract(prompt)


def _build_back_translation_prompt(
    text: str, input_language: str, output_language: str
) -> str:
    template_path = DICTATION_BACK_TRANSLATION_PROMPT_PATH
    if template_path.exists():
        template = _load_prompt_template(
            template_path,
            _DEFAULT_BACK_TRANSLATION_PROMPT,
        )
    else:
        template = _load_prompt_template(
            DICTATION_TRANSLATION_PROMPT_PATH,
            _DEFAULT_BACK_TRANSLATION_PROMPT,
        )
    prompt = _render_prompt_template(
        template,
        {
            "{{TEXT}}": text,
            "{{INPUT_LANGUAGE}}": _language_descriptor(
                input_language,
                fallback="the speaker's source language",
            ),
            "{{OUTPUT_LANGUAGE}}": _language_descriptor(
                output_language,
                fallback="the transcript language",
            ),
            "{{TARGET_LANGAUGE}}": _language_descriptor(
                input_language,
                fallback="the speaker's source language",
            ),
            "{{TARGET_LANGUAGE}}": _language_descriptor(
                input_language,
                fallback="the speaker's source language",
            ),
        },
    )
    return _with_final_answer_contract(prompt)


def _run_llm_prompt(prompt: str, model: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    request = urllib.request.Request(
        f"{_normalize_translation_base_url()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(
                {"Authorization": f"Bearer {DICTATION_TRANSLATION_API_KEY}"}
                if DICTATION_TRANSLATION_API_KEY
                else {}
            ),
        },
    )
    with urllib.request.urlopen(
        request, timeout=DICTATION_TRANSLATION_TIMEOUT_SECONDS
    ) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise RuntimeError("LLM returned an invalid JSON payload.")
    raw_content = _extract_message_content(payload)
    final_answer = _extract_final_answer(raw_content)
    if not final_answer:
        raise RuntimeError("LLM returned no final answer.")
    return final_answer


def _stt_ws_base_url() -> str:
    if STT_BASE_URL.startswith("https://"):
        return "wss://" + STT_BASE_URL[len("https://") :]
    if STT_BASE_URL.startswith("http://"):
        return "ws://" + STT_BASE_URL[len("http://") :]
    raise ValueError(f"Unsupported STT base URL scheme: {STT_BASE_URL}")


def _read_pcm16_audio(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        raw_frames = wav_file.readframes(wav_file.getnframes())

    if channels < 1:
        raise ValueError("Audio stream has no channels.")
    if channels > 2:
        raise ValueError("Realtime mode supports mono/stereo wav files only.")
    if sample_width <= 0:
        raise ValueError("Invalid WAV sample width.")

    pcm16 = raw_frames
    if sample_width != 2:
        pcm16 = audioop.lin2lin(pcm16, sample_width, 2)

    if channels == 2:
        pcm16 = audioop.tomono(pcm16, 2, 0.5, 0.5)

    if sample_rate != 16000:
        pcm16, _ = audioop.ratecv(pcm16, 2, 1, sample_rate, 16000, None)

    return pcm16


def _transcribe_file_http(audio_path: str, prompt: str = "") -> str:
    path = Path(audio_path)
    if not path.is_file():
        return f"Audio file was not found: {audio_path}"
    if path.stat().st_size > MAX_AUDIO_BYTES:
        max_mb = MAX_AUDIO_BYTES // (1024 * 1024)
        return f"Audio file is too large. Limit is {max_mb} MB."

    boundary = "----ukbgpt-" + uuid.uuid4().hex

    def form_field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    with path.open("rb") as handle:
        audio_bytes = handle.read()

    body = bytearray()
    body += form_field("model", STT_MODEL)
    body += form_field("response_format", "text")
    if prompt.strip():
        body += form_field("prompt", prompt.strip())
    body += f"--{boundary}\r\n".encode("utf-8")
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8")
    body += audio_bytes
    body += b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")

    request = urllib.request.Request(
        f"{STT_BASE_URL}/audio/transcriptions",
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if STT_API_KEY:
        request.add_header("Authorization", f"Bearer {STT_API_KEY}")

    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8", errors="replace")

    _raise_for_stt_error_payload(raw)
    transcript = _extract_transcript(raw)
    return transcript or "No transcript returned by STT backend."


def _is_voxtral_realtime_model() -> bool:
    lowered = STT_MODEL.lower()
    return "voxtral" in lowered and "realtime" in lowered


def _resolve_request_mode() -> str:
    if DICTATION_REQUEST_MODE in {"http", "realtime"}:
        return DICTATION_REQUEST_MODE
    if DICTATION_REQUEST_MODE == "auto":
        if _is_voxtral_realtime_model():
            return "realtime"
        return "http"
    raise ValueError(
        f"Invalid DICTATION_REQUEST_MODE='{DICTATION_REQUEST_MODE}'. "
        "Use one of: auto, http, realtime."
    )


def _transcribe_file_realtime(audio_path: str) -> str:
    if websockets is None:
        raise RuntimeError("Realtime dictation mode requires the websockets package.")

    path = Path(audio_path)
    if not path.is_file():
        return f"Audio file was not found: {audio_path}"
    if path.stat().st_size > MAX_AUDIO_BYTES:
        max_mb = MAX_AUDIO_BYTES // (1024 * 1024)
        return f"Audio file is too large. Limit is {max_mb} MB."

    audio_bytes = _read_pcm16_audio(path)
    chunks = [audio_bytes[i : i + 4800] for i in range(0, len(audio_bytes), 4800)]
    if not chunks:
        return "Audio appears to be empty."

    ws_url = f"{_stt_ws_base_url()}/realtime?model={quote(STT_MODEL, safe='')}"
    auth_header = None
    if STT_API_KEY:
        auth_header = ("Authorization", f"Bearer {STT_API_KEY}")

    async def transcribe_realtime() -> str:
        connect_kwargs: dict = {
            "max_size": 2**24,
            "open_timeout": REQUEST_TIMEOUT_SECONDS,
        }
        if auth_header:
            connect_kwargs["additional_headers"] = [auth_header]

        async with websockets.connect(ws_url, **connect_kwargs) as ws:
            await ws.send(json.dumps({"type": "session.update", "model": STT_MODEL}))

            for chunk in chunks:
                await ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(chunk).decode("utf-8"),
                        }
                    )
                )

            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await ws.send(json.dumps({"type": "input_audio_buffer.commit", "final": True}))

            deltas: list[str] = []
            for _ in range(1200):
                raw_event = await asyncio.wait_for(
                    ws.recv(),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                event = json.loads(raw_event)
                event_type = event.get("type")

                if event_type == "transcription.delta":
                    delta = event.get("delta", "")
                    if isinstance(delta, str) and delta:
                        deltas.append(delta)
                    continue

                if event_type == "transcription.done":
                    text = event.get("text")
                    if not isinstance(text, str):
                        text = "".join(deltas)
                    text = text.strip()
                    if text:
                        return text
                    raise RuntimeError("Realtime STT returned an empty final transcript.")

                if event_type == "error":
                    raise RuntimeError(
                        "Realtime STT error: " + json.dumps(event, ensure_ascii=False)
                    )

            raise TimeoutError("Timed out waiting for realtime transcription completion.")

    return asyncio.run(transcribe_realtime())


def _transcribe_file(audio_path: str, prompt: str = "") -> str:
    mode = _resolve_request_mode()
    if mode == "realtime":
        return _transcribe_file_realtime(audio_path)
    return _transcribe_file_http(audio_path, prompt=prompt)


def _stream_translation_to_output(
    source_text: str, input_language: str, output_language: str
):
    if not source_text:
        yield ""
        return
    if not DICTATION_LLM_BASE_URL:
        yield "Translation unavailable: missing DICTATION_LLM_BASE_URL."
        return

    try:
        model = _resolve_translation_model()
    except (HTTPError, URLError, RuntimeError, ValueError, OSError) as exc:
        yield f"Translation unavailable: {exc}"
        return

    prompt = _build_translation_prompt(source_text, input_language, output_language)
    try:
        translated = _run_llm_prompt(prompt, model)
        if translated:
            yield translated
        else:
            yield "Translation unavailable: empty response from LLM."
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if details:
            yield f"Translation failed: {exc} - {details[:300]}"
        else:
            yield f"Translation failed: {exc}"
    except (RuntimeError, URLError, OSError, ValueError) as exc:
        yield f"Translation failed: {exc}"


def _stream_back_translation(
    text: str, input_language: str, output_language: str
):
    if not text:
        yield "No output text to verify."
        return
    if not input_language.strip():
        yield "Enter an input language to generate a verification back-translation."
        return
    if not output_language.strip() or _output_language_matches_input(
        input_language, output_language
    ):
        yield ""
        return
    if not DICTATION_LLM_BASE_URL:
        yield "Back-translation unavailable: missing DICTATION_LLM_BASE_URL."
        return

    try:
        model = _resolve_translation_model()
    except (HTTPError, URLError, RuntimeError, ValueError, OSError) as exc:
        yield f"Back-translation unavailable: {exc}"
        return

    prompt = _build_back_translation_prompt(text, input_language, output_language)
    try:
        translated = _run_llm_prompt(prompt, model)
        if translated:
            yield translated
        else:
            yield "Back-translation unavailable: empty response from LLM."
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if details:
            yield f"Back-translation failed: {exc} - {details[:300]}"
        else:
            yield f"Back-translation failed: {exc}"
    except (RuntimeError, URLError, OSError, ValueError) as exc:
        yield f"Back-translation failed: {exc}"


def _audio_extension_for_format(response_format: str) -> str:
    normalized = (response_format or "wav").strip().lower()
    return normalized if normalized else "wav"


def _write_generated_audio(audio_bytes: bytes, response_format: str) -> str:
    suffix = f".{_audio_extension_for_format(response_format)}"
    with tempfile.NamedTemporaryFile(
        prefix="dictation_tts_",
        suffix=suffix,
        dir="/tmp",
        delete=False,
    ) as handle:
        handle.write(audio_bytes)
        return handle.name


def speak_translation(text: str, target_language: str) -> tuple[str, str | None]:
    text = (text or "").strip()
    target_language = (target_language or "").strip()
    if not text:
        return "Read-aloud unavailable: no output text to speak.", None
    if not DICTATION_TTS_BASE_URL:
        return "Read-aloud unavailable: missing DICTATION_TTS_BASE_URL or TTS_ENDPOINT.", None

    try:
        model = _resolve_tts_model()
        payload = {
            "model": model,
            "input": text,
            "voice": DICTATION_TTS_DEFAULT_VOICE,
            "response_format": DICTATION_TTS_RESPONSE_FORMAT,
        }
        if target_language:
            payload["language"] = target_language

        request = urllib.request.Request(
            f"{_normalize_tts_base_url()}/audio/speech",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=_tts_api_headers(),
        )

        with urllib.request.urlopen(request, timeout=DICTATION_TTS_TIMEOUT_SECONDS) as response:
            audio_bytes = response.read()

        if not audio_bytes:
            return "Read-aloud failed: empty audio response from TTS backend.", None
        if DICTATION_TTS_RESPONSE_FORMAT == "wav" and not audio_bytes.startswith(b"RIFF"):
            return "Read-aloud failed: TTS backend did not return WAV audio.", None

        audio_path = _write_generated_audio(audio_bytes, DICTATION_TTS_RESPONSE_FORMAT)
        return f"Read-aloud complete using voice '{DICTATION_TTS_DEFAULT_VOICE}'.", audio_path
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if details:
            return f"Read-aloud failed: {exc} - {details[:300]}", None
        return f"Read-aloud failed: {exc}", None
    except (RuntimeError, URLError, OSError, ValueError) as exc:
        return f"Read-aloud failed: {exc}", None


def transcribe_and_translate(
    audio_path: str | None, input_language: str, output_language: str
):
    if not audio_path:
        yield "Record or upload audio first.", ""
        return

    input_language = (input_language or "").strip()
    output_language = (output_language or "").strip()
    same_language_output = _output_language_matches_input(input_language, output_language)
    direct_multilingual = (
        _stt_model_supports_prompted_multilingual_output() and not same_language_output
    )

    try:
        if direct_multilingual or same_language_output:
            desired_output_language = output_language if output_language else input_language
            transcription_prompt = _build_transcription_prompt(
                input_language,
                desired_output_language,
            )
            primary_text = _transcribe_file(audio_path, prompt=transcription_prompt)
            primary_text = apply_post_processing(primary_text)
        else:
            transcript = _transcribe_file(audio_path)
            primary_text = apply_post_processing(transcript)
    except Exception as exc:
        yield f"Transcription failed: {exc}", ""
        return

    if same_language_output:
        yield primary_text, ""
        return

    if not direct_multilingual:
        translated_text = ""
        for translated in _stream_translation_to_output(
            primary_text, input_language, output_language
        ):
            translated_text = translated
            if translated.startswith("Translation failed:") or translated.startswith(
                "Translation unavailable:"
            ):
                yield primary_text, translated
                return
            yield translated_text, ""
        if translated_text:
            primary_text = translated_text

    yield primary_text, "Back-translating for verification..."
    for verification in _stream_back_translation(
        primary_text,
        input_language,
        output_language,
    ):
        yield primary_text, verification


def retranslate(text: str, input_language: str, output_language: str):
    text = (text or "").strip()
    input_language = (input_language or "").strip()
    output_language = (output_language or "").strip()
    if not text:
        yield "No output text to verify."
        return
    for translated_text in _stream_back_translation(text, input_language, output_language):
        yield translated_text

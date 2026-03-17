import asyncio
import audioop
import base64
import json
import os
import re
import uuid
import urllib.request
import wave
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError, URLError

import gradio as gr
import websockets

STT_BASE_URL = (
    os.getenv("DICTATION_STT_BASE_URL")
    or f"http://{os.getenv('STT_ENDPOINT', 'stt_worker_0:5000')}/v1"
).rstrip("/")
STT_MODEL = os.getenv("DICTATION_STT_MODEL", os.getenv("STT_MODEL_ID", "mistralai/Voxtral-Mini-4B-Realtime-2602"))
STT_API_KEY = os.getenv("DICTATION_API_KEY", "")
DICTATION_REQUEST_MODE = os.getenv("DICTATION_REQUEST_MODE", "auto").strip().lower()
REQUEST_TIMEOUT_SECONDS = int(os.getenv("DICTATION_REQUEST_TIMEOUT_SECONDS", "300"))
MAX_AUDIO_BYTES = int(os.getenv("DICTATION_MAX_AUDIO_BYTES", str(50 * 1024 * 1024)))
DICTATION_PORT = int(os.getenv("DICTATION_PORT", "7860"))
DICTATION_ROOT_PATH = os.getenv("DICTATION_ROOT_PATH", "/dictation")
POST_PROC_CONFIG_PATH = Path(os.getenv("DICTATION_POSTPROC_CONFIG_PATH", "/app/dictation_postprocessing.json"))
DICTATION_TRANSLATION_PROMPT_PATH = Path(
    os.getenv("DICTATION_TRANSLATION_PROMPT_PATH", "/app/translate_prompt.txt")
)
_raw_llm_url = os.getenv("DICTATION_LLM_BASE_URL", "").strip()
if _raw_llm_url:
    DICTATION_LLM_BASE_URL = _raw_llm_url.rstrip("/")
else:
    primary_llm = os.getenv("PRIMARY_OPENAI_ENDPOINT", os.getenv("LLM_ENDPOINT", "backend_router:5000")).strip()
    if not primary_llm:
        primary_llm = "backend_router:5000"
    if "://" not in primary_llm:
        primary_llm = f"http://{primary_llm}"
    DICTATION_LLM_BASE_URL = primary_llm.rstrip("/")
    if not DICTATION_LLM_BASE_URL.endswith("/v1"):
        DICTATION_LLM_BASE_URL = f"{DICTATION_LLM_BASE_URL}/v1"
DICTATION_TRANSLATION_MODEL = os.getenv("DICTATION_TRANSLATION_MODEL", "").strip()
DICTATION_TRANSLATION_TIMEOUT_SECONDS = int(os.getenv("DICTATION_TRANSLATION_TIMEOUT_SECONDS", "120"))
DICTATION_TRANSLATION_API_KEY = os.getenv("DICTATION_TRANSLATION_API_KEY", "")
_DEFAULT_TRANSLATION_PROMPT = """Translate the following text into {{TARGET_LANGAUGE}}.

Keep the original meaning and formatting, unless formatting would block meaning.
Return only the translated text.

{{TEXT}}"""


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
    """
    Strict cleanup pass for dictation text.
    Enforces punctuation hierarchy and normalization:
    - High:  : ! ? ; ( )
    - Mid:   .
    - Low:   ,
    """
    if not text:
        return ""

    # 1) Normalize spaces
    text = re.sub(r"[ \t]+", " ", text)

    # 2) Deduplicate same symbol first (e.g. ".." -> ".", ",," -> ",")
    text = re.sub(r"([.,;:!?])(?:\s*\1)+", r"\1", text)

    # 3) Low-level resolution: comma vs dot -> dot wins
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"\.\s*,", ".", text)

    # 4) High symbol wins over dot/comma
    high_punct = r"[:!?;()]"
    text = re.sub(rf"[.,]\s*({high_punct})", r"\1", text)
    text = re.sub(rf"({high_punct})\s*[.,]", r"\1", text)

    # 5) Remove spaces before punctuation
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)

    # 6) Parentheses artifacts
    text = re.sub(r"\(\s*,", "(", text)
    text = re.sub(r",\s*\)", ")", text)

    # 7) Ensure spacing after punctuation
    text = re.sub(r"([.,;:!?])(?=[a-zA-ZäöüÄÖÜ0-9])", r"\1 ", text)
    text = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", text)

    # 8) Capitalize the start of sentences
    def _capitalize_match(match: re.Match[str]) -> str:
        return match.group(1) + " " + match.group(2).upper()

    text = re.sub(r"([.!?])\s+([a-zäöü])", _capitalize_match, text)

    # 9) Newline artifacts
    text = re.sub(r"\n\s*[.,;:]", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def apply_post_processing(text_input):
    """
    Apply deterministic replacements from JSON config and cleanup normalization.
    Input:
      - String: returns String
      - List[dict]: returns list with per-segment `text` rewritten
    """

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
            print(f"Failed to load post-processing config '{POST_PROC_CONFIG_PATH}': {exc}")
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
                    is_case_sensitive = _to_bool_value(config.get("case_sensitive"), False)
                    is_whole_word = _to_bool_value(config.get("whole_word"), False)
                    consume_punct = _to_bool_value(config.get("consume_punctuation"), False)
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


def _transcribe_file_http(audio_path: str) -> str:
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


def _translate_api_headers() -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if DICTATION_TRANSLATION_API_KEY:
        headers["Authorization"] = f"Bearer {DICTATION_TRANSLATION_API_KEY}"
    return headers


def _normalize_translation_base_url() -> str:
    if DICTATION_LLM_BASE_URL.endswith("/v1"):
        return DICTATION_LLM_BASE_URL
    return f"{DICTATION_LLM_BASE_URL.rstrip('/')}/v1"


def _load_translation_prompt() -> str:
    try:
        if not DICTATION_TRANSLATION_PROMPT_PATH.exists():
            return _DEFAULT_TRANSLATION_PROMPT

        prompt_content = DICTATION_TRANSLATION_PROMPT_PATH.read_text(encoding="utf-8").strip()
        return prompt_content if prompt_content else _DEFAULT_TRANSLATION_PROMPT
    except Exception as exc:
        print(f"Failed to load translation prompt '{DICTATION_TRANSLATION_PROMPT_PATH}': {exc}")
        return _DEFAULT_TRANSLATION_PROMPT


def _resolve_translation_model() -> str:
    if DICTATION_TRANSLATION_MODEL:
        return DICTATION_TRANSLATION_MODEL

    if not DICTATION_LLM_BASE_URL:
        raise RuntimeError("Translation disabled: no configured LLM endpoint.")

    endpoint = f"{_normalize_translation_base_url()}/models"
    request = urllib.request.Request(endpoint, method="GET", headers=_translate_api_headers())
    with urllib.request.urlopen(request, timeout=DICTATION_TRANSLATION_TIMEOUT_SECONDS) as response:
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


def _transcribe_file_realtime(audio_path: str) -> str:
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
        connect_kwargs: dict = {"max_size": 2**24, "open_timeout": REQUEST_TIMEOUT_SECONDS}
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


def _translate_stream(text: str, target_language: str, model: str):
    if not text or not target_language:
        return
    if not DICTATION_LLM_BASE_URL:
        raise RuntimeError("No LLM endpoint is configured for translation.")

    prompt_template = _load_translation_prompt()
    prompt = prompt_template.replace("{{TEXT}}", text)
    prompt = prompt.replace("{{TARGET_LANGAUGE}}", target_language).replace(
        "{{TARGET_LANGUAGE}}", target_language
    )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": True,
    }

    request = urllib.request.Request(
        f"{_normalize_translation_base_url()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=_translate_api_headers(),
    )

    with urllib.request.urlopen(
        request, timeout=DICTATION_TRANSLATION_TIMEOUT_SECONDS
    ) as response:
        had_chunk = False
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data: "):
                continue
            payload_text = line[6:].strip()
            if payload_text == "[DONE]":
                break
            try:
                event = json.loads(payload_text)
            except json.JSONDecodeError:
                continue
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta")
            if isinstance(delta, dict):
                chunk = delta.get("content", "")
                if isinstance(chunk, str) and chunk:
                    had_chunk = True
                    yield chunk
                    continue

            message = choice.get("message")
            if isinstance(message, dict):
                chunk = message.get("content", "")
                if isinstance(chunk, str) and chunk:
                    had_chunk = True
                    yield chunk
                    continue

        if not had_chunk:
            raise RuntimeError("LLM did not stream translation content.")


def _transcribe_file(audio_path: str) -> str:
    mode = _resolve_request_mode()
    if mode == "realtime":
        return _transcribe_file_realtime(audio_path)
    return _transcribe_file_http(audio_path)


def _translate_text(text: str, target_language: str):
    target_language = (target_language or "").strip()
    if not target_language:
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

    try:
        current = ""
        for chunk in _translate_stream(text, target_language, model):
            current += chunk
            yield current
        if not current:
            yield "Translation unavailable: empty response from LLM."
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if details:
            yield f"Translation failed: {exc} - {details[:300]}"
        else:
            yield f"Translation failed: {exc}"
    except (RuntimeError, URLError, OSError, ValueError) as exc:
        yield f"Translation failed: {exc}"


def transcribe_and_translate(audio_path: str | None, target_language: str):
    if not audio_path:
        yield "Record or upload audio first.", ""
        return

    target_language = (target_language or "").strip()
    try:
        transcript = _transcribe_file(audio_path)
        transcript = apply_post_processing(transcript)
    except Exception as exc:
        yield f"Transcription failed: {exc}", ""
        return

    if not target_language:
        yield transcript, ""
        return

    yield transcript, "Translating..."

    for translated in _translate_text(transcript, target_language):
        yield transcript, translated


def retranslate(text: str, target_language: str):
    text = (text or "").strip()
    target_language = (target_language or "").strip()
    if not text:
        yield "No source text to translate."
        return
    if not target_language:
        yield "Enter a target language to translate."
        return

    for translated in _translate_text(text, target_language):
        yield translated


with gr.Blocks(title="UKB Dictation", analytics_enabled=False) as demo:
    gr.Markdown("## Secure Dictation")
    gr.Markdown("Internal speech-to-text UI routed only inside the UKB-GPT stack.")

    audio = gr.Audio(
        sources=["microphone", "upload"],
        type="filepath",
        format="wav",
        label="Audio",
    )
    transcript = gr.Textbox(
        label="Transcript",
        lines=10,
        show_copy_button=True,
    )
    target_language = gr.Textbox(
        label="Target language",
        placeholder="e.g. German",
        lines=1,
        info="Leave empty to skip translation.",
    )
    translated = gr.Textbox(label="Translated text", lines=10, show_copy_button=True)

    with gr.Row():
        transcribe_button = gr.Button("Transcribe", variant="primary")
        retranslate_button = gr.Button("Retranslate", variant="secondary")
        gr.ClearButton([audio, transcript, target_language, translated])

    transcribe_button.click(
        transcribe_and_translate,
        inputs=[audio, target_language],
        outputs=[transcript, translated],
        api_name="transcribe",
        show_api=False,
    )

    retranslate_button.click(
        retranslate,
        inputs=[transcript, target_language],
        outputs=[translated],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=DICTATION_PORT,
        share=False,
        show_api=False,
        root_path=DICTATION_ROOT_PATH,
    )

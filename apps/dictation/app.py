import html
import os
import tempfile
import uuid
import wave

try:
    import gradio as gr
except ModuleNotFoundError:  # pragma: no cover - exercised in local test envs without gradio
    gr = None

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - exercised in local test envs without numpy
    np = None

from apps.dictation.core import (
    DICTATION_DEFAULT_LANGUAGE_1,
    DICTATION_DEFAULT_LANGUAGE_2,
    DICTATION_PORT,
    DICTATION_ROOT_PATH,
    DICTATION_TTS_BASE_URL,
    dictation_supports_live_streaming,
    language_is_rtl,
    retranslate,
    resolve_target_language,
    run_conversation_audio_translation,
    speak_translation,
    transcribe_and_translate,
    transcribe_conversation_file,
    transcribe_live_audio_chunk,
    translate_to_target,
)
from apps.common.stt_contract import CONVERSATION_LANGUAGE_CHOICES


COMMON_LANGUAGES = list(CONVERSATION_LANGUAGE_CHOICES)
LIVE_STREAM_INTERVAL_SECONDS = 3.0
LIVE_STREAM_TIME_LIMIT_SECONDS = 600
LIVE_CONCURRENCY_ID = "dictation-live"

DICTATION_CSS = """
#ukb-dictation-shell {
  max-width: 760px;
  margin: 0 auto;
}
#ukb-dictation-shell .gradio-container {
  padding-top: 0.5rem;
}
#ukb-dictation-status {
  font-size: 0.95rem;
  margin-bottom: 0.35rem;
}
.ukb-direction-badge {
  display: inline-block;
  padding: 0.35rem 0.7rem;
  border-radius: 999px;
  background: #fff4ed;
  color: #9a3412;
  font-size: 0.9rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
}
.ukb-status-note {
  padding: 0.6rem 0.8rem;
  border-radius: 0.9rem;
  background: #f4f4f5;
  color: #27272a;
  font-size: 0.92rem;
}
.ukb-status-note.error {
  background: #fef2f2;
  color: #991b1b;
}
.ukb-status-note.ready {
  background: #ecfdf5;
  color: #166534;
}
.ukb-status-note.working {
  background: #eff6ff;
  color: #1d4ed8;
}
.ukb-status-note.working::before {
  content: "";
  display: inline-block;
  width: 0.8rem;
  height: 0.8rem;
  margin-right: 0.45rem;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 999px;
  vertical-align: -0.12rem;
  animation: ukb-spin 0.8s linear infinite;
}
#ukb-transcript-box textarea {
  min-height: 5rem !important;
}
#ukb-translation-box textarea {
  min-height: 7rem !important;
}
@keyframes ukb-spin {
  to { transform: rotate(360deg); }
}
@media (max-width: 640px) {
  #ukb-dictation-shell {
    padding-left: 0.15rem;
    padding-right: 0.15rem;
  }
  .ukb-direction-badge {
    font-size: 0.84rem;
    width: 100%;
    text-align: center;
  }
  .ukb-status-note {
    font-size: 0.88rem;
  }
}
"""


def _normalize_language(raw_language: str, fallback: str) -> str:
    language = (raw_language or "").strip()
    return language if language else fallback


def _status_html(message: str, tone: str = "muted") -> str:
    classes = "ukb-status-note"
    if tone in {"error", "ready", "working"}:
        classes += f" {tone}"
    return f'<div class="{classes}">{html.escape(message)}</div>'


def _direction_badge_html(spoken_language: str, target_language: str) -> str:
    if not spoken_language or not target_language:
        return ""
    return (
        '<div class="ukb-direction-badge">'
        f"{html.escape(spoken_language)} -> {html.escape(target_language)}"
        "</div>"
    )


def _textbox_update(value: str, display_language: str):
    return gr.update(value=value, rtl=language_is_rtl(display_language))


def _default_status() -> str:
    return _status_html("Tap the microphone or upload audio. The transcript updates while you speak.")


def _empty_tts_outputs() -> tuple[str, str | None]:
    return "", None


def _prepare_tts_outputs(
    text: str,
    target_language: str,
    language_2: str,
) -> tuple[str, str | None]:
    if not DICTATION_TTS_BASE_URL:
        return _empty_tts_outputs()
    preferred_language = _normalize_language(target_language, language_2)
    return speak_output_for_ui(text, preferred_language, language_2)


def _empty_live_session() -> dict[str, object]:
    return {
        "session_id": "",
        "sample_rate": 0,
        "audio_segments": [],
        "chunk_uuids": [],
        "transcript_text": "",
        "spoken_language": "",
    }


def _clone_live_session(session: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(session, dict):
        return _empty_live_session()
    cloned = dict(session)
    cloned["audio_segments"] = list(session.get("audio_segments") or [])
    cloned["chunk_uuids"] = list(session.get("chunk_uuids") or [])
    return cloned


def _normalize_stream_audio(audio_value):
    if audio_value is None or np is None:
        return None
    if not isinstance(audio_value, (list, tuple)) or len(audio_value) != 2:
        return None

    sample_rate, samples = audio_value
    try:
        resolved_sample_rate = int(sample_rate)
    except (TypeError, ValueError):
        return None

    array = np.asarray(samples)
    if array.size == 0:
        return None
    if array.ndim > 1:
        array = array.mean(axis=1)
    array = array.astype(np.float32, copy=False)
    max_abs = float(np.max(np.abs(array))) if array.size else 0.0
    if max_abs > 1.0:
        array = array / max_abs
    return resolved_sample_rate, array


def _write_stream_audio_file(audio_value, *, prefix: str) -> str:
    normalized = _normalize_stream_audio(audio_value)
    if normalized is None:
        return ""
    sample_rate, samples = normalized
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with tempfile.NamedTemporaryFile(
        prefix=prefix,
        suffix=".wav",
        dir="/tmp",
        delete=False,
    ) as handle:
        with wave.open(handle, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return handle.name


def _final_audio_path_from_session(
    session: dict[str, object],
    final_audio_value,
) -> str:
    final_audio_path = _write_stream_audio_file(
        final_audio_value,
        prefix="dictation_final_recording_",
    )
    if final_audio_path:
        return final_audio_path

    if np is None:
        return ""

    segments = list(session.get("audio_segments") or [])
    if not segments:
        return ""

    sample_rate = int(session.get("sample_rate") or 16000)
    merged = np.concatenate([np.asarray(segment, dtype=np.float32) for segment in segments])
    return _write_stream_audio_file(
        (sample_rate, merged),
        prefix="dictation_final_recording_",
    )


def _extract_uploaded_audio_path(upload_value) -> str:
    if isinstance(upload_value, str):
        return upload_value
    if isinstance(upload_value, list) and upload_value:
        return _extract_uploaded_audio_path(upload_value[0])
    candidate = getattr(upload_value, "name", "")
    return candidate if isinstance(candidate, str) else ""


def _streaming_placeholder_status() -> str:
    if dictation_supports_live_streaming():
        return _status_html("Listening...", tone="working")
    return _status_html(
        "Listening... Live transcript updates are unavailable for this STT backend, final transcript will appear after stop.",
        tone="working",
    )


def process_stream_chunk_for_ui(
    live_session: dict[str, object] | None,
    new_chunk,
    language_1: str,
    language_2: str,
):
    normalized_language_1 = _normalize_language(language_1, DICTATION_DEFAULT_LANGUAGE_1)
    normalized_language_2 = _normalize_language(language_2, DICTATION_DEFAULT_LANGUAGE_2)
    session = _clone_live_session(live_session)
    normalized_audio = _normalize_stream_audio(new_chunk)

    if normalized_audio is None:
        yield (
            _textbox_update("", normalized_language_2),
            _textbox_update(session.get("transcript_text", ""), session.get("spoken_language", normalized_language_1)),
            _direction_badge_html(
                session.get("spoken_language", ""),
                resolve_target_language(
                    session.get("spoken_language", normalized_language_1),
                    normalized_language_1,
                    normalized_language_2,
                )
                if session.get("spoken_language")
                else "",
            ),
            _default_status(),
            session.get("spoken_language", ""),
            resolve_target_language(
                session.get("spoken_language", normalized_language_1),
                normalized_language_1,
                normalized_language_2,
            )
            if session.get("spoken_language")
            else "",
            session,
            *_empty_tts_outputs(),
        )
        return

    sample_rate, samples = normalized_audio
    session.setdefault("session_id", "")
    if not session["session_id"]:
        session = _empty_live_session()
        session["session_id"] = uuid.uuid4().hex
        session["sample_rate"] = sample_rate
    elif int(session.get("sample_rate") or sample_rate) != sample_rate:
        session = _empty_live_session()
        session["session_id"] = uuid.uuid4().hex
        session["sample_rate"] = sample_rate

    session["audio_segments"].append(samples.copy())
    chunk_uuid = f"{session['session_id']}-chunk-{len(session['audio_segments']):04d}"
    chunk_path = _write_stream_audio_file((sample_rate, samples), prefix="dictation_live_chunk_")
    previous_chunk_uuids = list(session.get("chunk_uuids") or [])

    current_spoken_language = str(session.get("spoken_language") or "")
    current_transcript = str(session.get("transcript_text") or "")
    current_target_language = (
        resolve_target_language(current_spoken_language, normalized_language_1, normalized_language_2)
        if current_spoken_language
        else ""
    )

    yield (
        _textbox_update("", current_target_language or normalized_language_2),
        _textbox_update(current_transcript, current_spoken_language or normalized_language_1),
        _direction_badge_html(current_spoken_language, current_target_language),
        _streaming_placeholder_status(),
        current_spoken_language,
        current_target_language,
        session,
        *_empty_tts_outputs(),
    )

    if not dictation_supports_live_streaming():
        if chunk_path:
            try:
                os.unlink(chunk_path)
            except OSError:
                pass
        return

    try:
        live_result = transcribe_live_audio_chunk(
            chunk_path,
            normalized_language_1,
            normalized_language_2,
            previous_chunk_uuids=previous_chunk_uuids,
            chunk_uuid=chunk_uuid,
        )
        spoken_language = live_result.spoken_language.value
        target_language = resolve_target_language(
            spoken_language,
            normalized_language_1,
            normalized_language_2,
        )
        session["chunk_uuids"].append(chunk_uuid)
        session["spoken_language"] = spoken_language
        session["transcript_text"] = live_result.transcription
        yield (
            _textbox_update("", target_language),
            _textbox_update(live_result.transcription, spoken_language),
            _direction_badge_html(spoken_language, target_language),
            _status_html("Transcript updated. Keep speaking or stop to finalize the translation.", tone="ready"),
            spoken_language,
            target_language,
            session,
            *_empty_tts_outputs(),
        )
    except Exception as exc:
        yield (
            _textbox_update("", current_target_language or normalized_language_2),
            _textbox_update(current_transcript, current_spoken_language or normalized_language_1),
            _direction_badge_html(current_spoken_language, current_target_language),
            _status_html(f"Live update failed: {exc}. Final transcript will retry after stop.", tone="error"),
            current_spoken_language,
            current_target_language,
            session,
            *_empty_tts_outputs(),
        )
    finally:
        if chunk_path:
            try:
                os.unlink(chunk_path)
            except OSError:
                pass


def _translate_transcript_for_ui(
    transcript_text: str,
    spoken_language: str,
    target_language: str,
    thinking_mode: bool,
):
    if not transcript_text:
        return "Nothing to translate yet."
    if target_language.casefold() == spoken_language.casefold():
        return transcript_text

    translated_text = ""
    for translated_text in translate_to_target(
        transcript_text,
        spoken_language,
        target_language,
        enable_thinking=thinking_mode,
    ):
        pass
    return translated_text


def _process_audio_file_for_ui(
    audio_path: str,
    normalized_language_1: str,
    normalized_language_2: str,
    thinking_mode: bool,
    initial_transcript: str = "",
    initial_spoken_language: str = "",
):
    yield (
        _textbox_update("", normalized_language_2),
        _textbox_update(initial_transcript, initial_spoken_language or normalized_language_1),
        _direction_badge_html(
            initial_spoken_language,
            resolve_target_language(
                initial_spoken_language,
                normalized_language_1,
                normalized_language_2,
            )
            if initial_spoken_language
            else "",
        ),
        _status_html("Finalizing transcript...", tone="working"),
        initial_spoken_language,
        resolve_target_language(
            initial_spoken_language,
            normalized_language_1,
            normalized_language_2,
        )
        if initial_spoken_language
        else "",
        _empty_live_session(),
        *_empty_tts_outputs(),
    )

    try:
        final_transcription = transcribe_conversation_file(
            audio_path,
            normalized_language_1,
            normalized_language_2,
        )
    except Exception as exc:
        yield (
            _textbox_update("", normalized_language_2),
            _textbox_update(initial_transcript, initial_spoken_language or normalized_language_1),
            "",
            _status_html(f"Transcription failed: {exc}", tone="error"),
            "",
            "",
            _empty_live_session(),
            *_empty_tts_outputs(),
        )
        return

    spoken_language = final_transcription.spoken_language.value
    target_language = resolve_target_language(
        spoken_language,
        normalized_language_1,
        normalized_language_2,
    )
    transcript_text = final_transcription.transcription

    yield (
        _textbox_update("", target_language),
        _textbox_update(transcript_text, spoken_language),
        _direction_badge_html(spoken_language, target_language),
        _status_html("Translating...", tone="working"),
        spoken_language,
        target_language,
        _empty_live_session(),
        *_empty_tts_outputs(),
    )

    translated_text = _translate_transcript_for_ui(
        transcript_text,
        spoken_language,
        target_language,
        thinking_mode,
    )
    if translated_text.startswith("Translation failed:") or translated_text.startswith(
        "Translation unavailable:"
    ):
        yield (
            _textbox_update(translated_text, target_language),
            _textbox_update(transcript_text, spoken_language),
            _direction_badge_html(spoken_language, target_language),
            _status_html(translated_text, tone="error"),
            spoken_language,
            target_language,
            _empty_live_session(),
            *_empty_tts_outputs(),
        )
        return

    final_outputs = (
        _textbox_update(translated_text, target_language),
        _textbox_update(transcript_text, spoken_language),
        _direction_badge_html(spoken_language, target_language),
        _status_html(f"{spoken_language} detected, translated to {target_language}.", tone="ready"),
        spoken_language,
        target_language,
        _empty_live_session(),
    )

    if not DICTATION_TTS_BASE_URL:
        yield (*final_outputs, *_empty_tts_outputs())
        return

    yield (*final_outputs, "Preparing translated audio...", None)
    tts_status, tts_audio_path = _prepare_tts_outputs(
        translated_text,
        target_language,
        normalized_language_2,
    )
    yield (*final_outputs, tts_status, tts_audio_path)


def finalize_recording_for_ui(
    live_session: dict[str, object] | None,
    final_audio_value,
    language_1: str,
    language_2: str,
    thinking_mode: bool,
):
    normalized_language_1 = _normalize_language(language_1, DICTATION_DEFAULT_LANGUAGE_1)
    normalized_language_2 = _normalize_language(language_2, DICTATION_DEFAULT_LANGUAGE_2)
    session = _clone_live_session(live_session)
    final_audio_path = _final_audio_path_from_session(session, final_audio_value)

    if not final_audio_path:
        yield (
            _textbox_update("", normalized_language_2),
            _textbox_update("", normalized_language_1),
            "",
            _status_html("Record or upload audio first.", tone="error"),
            "",
            "",
            _empty_live_session(),
            *_empty_tts_outputs(),
        )
        return

    yield from _process_audio_file_for_ui(
        final_audio_path,
        normalized_language_1,
        normalized_language_2,
        thinking_mode,
        initial_transcript=str(session.get("transcript_text") or ""),
        initial_spoken_language=str(session.get("spoken_language") or ""),
    )


def process_uploaded_audio_for_ui(
    upload_value,
    language_1: str,
    language_2: str,
    thinking_mode: bool,
):
    uploaded_audio_path = _extract_uploaded_audio_path(upload_value)
    if not uploaded_audio_path:
        yield (
            _textbox_update("", _normalize_language(language_2, DICTATION_DEFAULT_LANGUAGE_2)),
            _textbox_update("", _normalize_language(language_1, DICTATION_DEFAULT_LANGUAGE_1)),
            "",
            _status_html("Upload an audio file first.", tone="error"),
            "",
            "",
            _empty_live_session(),
            *_empty_tts_outputs(),
        )
        return

    yield from _process_audio_file_for_ui(
        uploaded_audio_path,
        _normalize_language(language_1, DICTATION_DEFAULT_LANGUAGE_1),
        _normalize_language(language_2, DICTATION_DEFAULT_LANGUAGE_2),
        thinking_mode,
    )


def refresh_translation_for_ui(
    transcript_text: str,
    detected_spoken_language: str,
    detected_target_language: str,
    language_1: str,
    language_2: str,
    thinking_mode: bool,
):
    normalized_language_1 = _normalize_language(language_1, DICTATION_DEFAULT_LANGUAGE_1)
    normalized_language_2 = _normalize_language(language_2, DICTATION_DEFAULT_LANGUAGE_2)
    transcript_value = (transcript_text or "").strip()
    spoken_language = _normalize_language(detected_spoken_language, normalized_language_1)
    target_language = _normalize_language(
        detected_target_language,
        resolve_target_language(spoken_language, normalized_language_1, normalized_language_2),
    )

    if not transcript_value:
        yield (
            _textbox_update("", target_language),
            _status_html("Nothing to translate yet.", tone="error"),
            spoken_language,
            target_language,
            *_empty_tts_outputs(),
        )
        return

    yield (
        _textbox_update("", target_language),
        _status_html("Translating...", tone="working"),
        spoken_language,
        target_language,
        *_empty_tts_outputs(),
    )

    translated_text = _translate_transcript_for_ui(
        transcript_value,
        spoken_language,
        target_language,
        thinking_mode,
    )
    if translated_text.startswith("Translation failed:") or translated_text.startswith(
        "Translation unavailable:"
    ):
        yield (
            _textbox_update(translated_text, target_language),
            _status_html(translated_text, tone="error"),
            spoken_language,
            target_language,
            *_empty_tts_outputs(),
        )
        return

    if not DICTATION_TTS_BASE_URL:
        yield (
            _textbox_update(translated_text, target_language),
            _status_html("Translation refreshed.", tone="ready"),
            spoken_language,
            target_language,
            *_empty_tts_outputs(),
        )
        return

    yield (
        _textbox_update(translated_text, target_language),
        _status_html("Translation refreshed. Preparing audio...", tone="working"),
        spoken_language,
        target_language,
        "Preparing translated audio...",
        None,
    )
    tts_status, tts_audio_path = _prepare_tts_outputs(
        translated_text,
        target_language,
        normalized_language_2,
    )
    yield (
        _textbox_update(translated_text, target_language),
        _status_html("Translation refreshed.", tone="ready"),
        spoken_language,
        target_language,
        tts_status,
        tts_audio_path,
    )


def speak_output_for_ui(
    text: str,
    detected_target_language: str,
    language_2: str,
):
    preferred_language = _normalize_language(
        detected_target_language,
        _normalize_language(language_2, DICTATION_DEFAULT_LANGUAGE_2),
    )
    return speak_translation(text, preferred_language)


def reset_ui_for_defaults():
    return (
        None,
        DICTATION_DEFAULT_LANGUAGE_1,
        DICTATION_DEFAULT_LANGUAGE_2,
        False,
        _textbox_update("", DICTATION_DEFAULT_LANGUAGE_2),
        _textbox_update("", DICTATION_DEFAULT_LANGUAGE_1),
        "",
        _default_status(),
        "",
        "",
        _empty_live_session(),
        *_empty_tts_outputs(),
    )


if gr is not None:
    with gr.Blocks(
        title="UKB Dictation",
        analytics_enabled=False,
        css=DICTATION_CSS,
    ) as demo:
        last_spoken_language = gr.State("")
        last_target_language = gr.State("")
        live_session = gr.State(_empty_live_session())

        with gr.Column(elem_id="ukb-dictation-shell"):
            gr.Markdown("## Conversation Translator")
            direction_badge = gr.HTML(value="")
            status_panel = gr.HTML(value=_default_status(), elem_id="ukb-dictation-status")

            audio_input = gr.Audio(
                sources=["microphone"],
                type="numpy",
                streaming=True,
                label="Speak",
            )
            upload_input = gr.UploadButton(
                "Upload audio",
                file_types=["audio"],
                type="filepath",
                file_count="single",
                variant="secondary",
            )

            with gr.Row():
                language_1 = gr.Dropdown(
                    choices=COMMON_LANGUAGES,
                    value=DICTATION_DEFAULT_LANGUAGE_1,
                    allow_custom_value=False,
                    label="Language 1",
                    scale=1,
                )
                language_2 = gr.Dropdown(
                    choices=COMMON_LANGUAGES,
                    value=DICTATION_DEFAULT_LANGUAGE_2,
                    allow_custom_value=False,
                    label="Language 2",
                    scale=1,
                )

            translation = gr.Textbox(
                label="Translation",
                lines=5,
                show_copy_button=True,
                interactive=False,
                placeholder="The other-language translation appears here.",
                elem_id="ukb-translation-box",
            )
            transcript = gr.Textbox(
                label="Transcript / correction",
                lines=4,
                show_copy_button=True,
                interactive=True,
                placeholder="The spoken-language transcript updates while you speak.",
                elem_id="ukb-transcript-box",
            )

            with gr.Row():
                thinking_mode = gr.Checkbox(
                    label="Thinking mode (better but slower)",
                    value=False,
                    scale=2,
                )
                refresh_translation_button = gr.Button(
                    "Refresh Translation",
                    variant="secondary",
                    scale=1,
                )
                reset_button = gr.Button("Reset", variant="secondary", scale=1)

            tts_enabled = bool(DICTATION_TTS_BASE_URL)
            with gr.Column(visible=tts_enabled):
                tts_status = gr.Textbox(
                    label="Read-aloud status",
                    lines=2,
                    interactive=False,
                    visible=tts_enabled,
                )
                tts_audio = gr.Audio(
                    label="Generated audio",
                    type="filepath",
                    interactive=False,
                    visible=tts_enabled,
                )
                speak_button = gr.Button(
                    "Refresh Audio",
                    variant="secondary",
                    visible=tts_enabled,
                )

        processing_outputs = [
            translation,
            transcript,
            direction_badge,
            status_panel,
            last_spoken_language,
            last_target_language,
            live_session,
            tts_status,
            tts_audio,
        ]

        stream_event = audio_input.stream(
            process_stream_chunk_for_ui,
            inputs=[live_session, audio_input, language_1, language_2],
            outputs=processing_outputs,
            stream_every=LIVE_STREAM_INTERVAL_SECONDS,
            time_limit=LIVE_STREAM_TIME_LIMIT_SECONDS,
            concurrency_limit=1,
            concurrency_id=LIVE_CONCURRENCY_ID,
            trigger_mode="always_last",
        )

        audio_input.stop_recording(
            finalize_recording_for_ui,
            inputs=[live_session, audio_input, language_1, language_2, thinking_mode],
            outputs=processing_outputs,
            cancels=[stream_event],
            concurrency_limit=1,
            concurrency_id=LIVE_CONCURRENCY_ID,
            trigger_mode="always_last",
        )

        upload_input.upload(
            process_uploaded_audio_for_ui,
            inputs=[upload_input, language_1, language_2, thinking_mode],
            outputs=processing_outputs,
            concurrency_limit=1,
            concurrency_id=LIVE_CONCURRENCY_ID,
            trigger_mode="always_last",
        )

        audio_input.clear(
            lambda: (
                _textbox_update("", DICTATION_DEFAULT_LANGUAGE_2),
                _textbox_update("", DICTATION_DEFAULT_LANGUAGE_1),
                "",
                _default_status(),
                "",
                "",
                _empty_live_session(),
                *_empty_tts_outputs(),
            ),
            outputs=processing_outputs,
        )

        refresh_translation_button.click(
            refresh_translation_for_ui,
            inputs=[
                transcript,
                last_spoken_language,
                last_target_language,
                language_1,
                language_2,
                thinking_mode,
            ],
            outputs=[
                translation,
                status_panel,
                last_spoken_language,
                last_target_language,
                tts_status,
                tts_audio,
            ],
        )

        reset_button.click(
            reset_ui_for_defaults,
            outputs=[
                audio_input,
                language_1,
                language_2,
                thinking_mode,
                translation,
                transcript,
                direction_badge,
                status_panel,
                last_spoken_language,
                last_target_language,
                live_session,
                tts_status,
                tts_audio,
            ],
        )

        if tts_enabled:
            speak_button.click(
                speak_output_for_ui,
                inputs=[translation, last_target_language, language_2],
                outputs=[tts_status, tts_audio],
                api_name="speak_translation",
            )
else:  # pragma: no cover - exercised only in non-container test/dev envs
    demo = None


if __name__ == "__main__":
    if demo is None:
        raise RuntimeError("gradio is required to run the dictation UI.")
    demo.launch(
        server_name="0.0.0.0",
        server_port=DICTATION_PORT,
        share=False,
        root_path=DICTATION_ROOT_PATH,
    )

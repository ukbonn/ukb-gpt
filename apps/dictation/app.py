try:
    import gradio as gr
except ModuleNotFoundError:  # pragma: no cover - exercised in local test envs without gradio
    gr = None

from apps.dictation.core import (
    DICTATION_PORT,
    DICTATION_ROOT_PATH,
    retranslate,
    speak_translation,
    transcribe_and_translate,
)


def transcribe_and_translate_for_ui(
    audio_path: str | None,
    input_language: str,
    output_language: str,
):
    for primary_text, verification_text in transcribe_and_translate(
        audio_path,
        input_language,
        output_language,
    ):
        yield primary_text, verification_text, "", None


def retranslate_for_ui(
    text: str,
    input_language: str,
    output_language: str,
):
    for verification_text in retranslate(text, input_language, output_language):
        yield verification_text, "", None


def speak_output_for_ui(
    text: str,
    input_language: str,
    output_language: str,
):
    preferred_language = (output_language or input_language or "").strip()
    return speak_translation(text, preferred_language)


if gr is not None:
    with gr.Blocks(title="UKB Dictation", analytics_enabled=False) as demo:
        gr.Markdown("## Secure Dictation")
        gr.Markdown(
            "Record or upload a speech segment, choose the spoken language, and optionally"
            " produce text in a second language. When the output language differs, the"
            " verification panel is automatically filled with a back-translation so the"
            " speaker can confirm the meaning."
        )

        with gr.Accordion("How This Workflow Behaves", open=False):
            gr.Markdown(
                "1. Leave **Output language** empty for a same-language transcription.\n"
                "2. Set **Output language** to a different language to get a translated"
                " primary result plus an automatic verification back-translation.\n"
                "3. Edit the primary output if needed, then press **Refresh verification**"
                " to confirm the corrected meaning without retranscribing the audio.\n"
                "4. **Read Primary Output** sends the primary result to the TTS backend."
            )

        with gr.Row():
            with gr.Column(scale=4):
                gr.Markdown("### Audio And Languages")
                audio = gr.Audio(
                    sources=["microphone", "upload"],
                    type="filepath",
                    format="wav",
                    label="Speech segment",
                )
                input_language = gr.Textbox(
                    label="Input language",
                    placeholder="e.g. English",
                    lines=1,
                    info="Language spoken by the speaker. Leave empty to let the model infer it.",
                )
                output_language = gr.Textbox(
                    label="Output language",
                    placeholder="Leave empty to keep the primary output in the input language.",
                    lines=1,
                    info="If different from the input language, the app also creates a verification back-translation.",
                )
                gr.Markdown(
                    "The primary output box stays editable. Use **Refresh verification** after"
                    " manual corrections to regenerate the confirmation text."
                )
                with gr.Row():
                    transcribe_button = gr.Button(
                        "Transcribe Audio", variant="primary"
                    )
                    retranslate_button = gr.Button(
                        "Refresh Verification", variant="secondary"
                    )
                with gr.Row():
                    speak_button = gr.Button(
                        "Read Primary Output", variant="secondary"
                    )

            with gr.Column(scale=6):
                gr.Markdown("### Results")
                transcript = gr.Textbox(
                    label="Primary transcript / translated output",
                    placeholder="The main text result appears here. This is the box to edit, copy, or read aloud.",
                    lines=10,
                    show_copy_button=True,
                )
                verification = gr.Textbox(
                    label="Verification back-translation",
                    placeholder="If the output language differs, the app fills this box with a back-translation into the input language.",
                    lines=10,
                    interactive=False,
                    show_copy_button=True,
                )
                tts_status = gr.Textbox(
                    label="Read-aloud status",
                    lines=2,
                    interactive=False,
                )
                tts_audio = gr.Audio(
                    label="Generated read-aloud audio",
                    type="filepath",
                    interactive=False,
                )

        with gr.Row():
            gr.ClearButton(
                [
                    audio,
                    input_language,
                    output_language,
                    transcript,
                    verification,
                    tts_status,
                    tts_audio,
                ]
            )

        transcribe_button.click(
            transcribe_and_translate_for_ui,
            inputs=[audio, input_language, output_language],
            outputs=[transcript, verification, tts_status, tts_audio],
            api_name="transcribe",
            show_api=False,
        )

        retranslate_button.click(
            retranslate_for_ui,
            inputs=[transcript, input_language, output_language],
            outputs=[verification, tts_status, tts_audio],
        )

        speak_button.click(
            speak_output_for_ui,
            inputs=[transcript, input_language, output_language],
            outputs=[tts_status, tts_audio],
            api_name="speak_translation",
            show_api=False,
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
        show_api=False,
        root_path=DICTATION_ROOT_PATH,
    )

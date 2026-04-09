# Dictation App

Optional internal Gradio UI for low-latency two-language conversation transcription, translation, and optional translated-text read-aloud.

## Enable

```bash
export ENABLE_DICTATION_APP=true
export STT_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-mini-4b.single-gpu.toml"
export STT_MODEL_ID="mistralai/Voxtral-Mini-4B-Realtime-2602"
```

The app is exposed only through ingress at `https://<SERVER_NAME>/dictation/`.

## Workflow

- `Language 1` and `Language 2` define the conversation pair
- microphone recording updates the spoken-language transcript while the user speaks
- stopping the recording or uploading audio finalizes the transcript and then translates automatically
- the app detects whether the segment was spoken in `Language 1` or `Language 2`
- the primary output box shows the translation in the other language
- the transcript box shows editable source-language text for correction
- `Refresh Translation` regenerates the translated output from the corrected transcript
- `Thinking mode (better but slower)` affects only the final translation pass, not the live transcript updates

Optional translated-text read-aloud:

```bash
export TTS_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-4b-tts.single-gpu.toml"
export TTS_MODEL_ID="mistralai/Voxtral-4B-TTS-2603"
export DICTATION_TTS_DEFAULT_VOICE="casual_male"
export DICTATION_TTS_RESPONSE_FORMAT="wav"
```

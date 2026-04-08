# Dictation App

Optional internal Gradio UI for speech-to-text transcription, translation, verification via back-translation, and translated-text read-aloud.

## Enable

```bash
export ENABLE_DICTATION_APP=true
export STT_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-mini-4b.single-gpu.toml"
export STT_MODEL_ID="mistralai/Voxtral-Mini-4B-Realtime-2602"
```

The app is exposed only through ingress at `https://<SERVER_NAME>/dictation/`.

## Workflow

- `Input language`: language spoken by the speaker
- `Output language`: language you want in the primary output box
- leave `Output language` empty for a same-language transcription
- if `Output language` differs from `Input language`, the app fills the primary output box with the translated text and the verification box with a back-translation into the input language
- after editing the primary output manually, use `Refresh Verification` to regenerate the confirmation text without retranscribing the audio

Optional translated-text read-aloud:

```bash
export TTS_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-4b-tts.single-gpu.toml"
export TTS_MODEL_ID="mistralai/Voxtral-4B-TTS-2603"
export DICTATION_TTS_DEFAULT_VOICE="casual_male"
export DICTATION_TTS_RESPONSE_FORMAT="wav"
```

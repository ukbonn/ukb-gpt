# Dictation App

Optional internal Gradio UI for speech-to-text transcription, translation, and translated-text read-aloud.

## Enable

```bash
export ENABLE_DICTATION_APP=true
export STT_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-mini-4b.single-gpu.toml"
export STT_MODEL_ID="mistralai/Voxtral-Mini-4B-Realtime-2602"
```

The app is exposed only through ingress at `https://<SERVER_NAME>/dictation/`.

Optional translated-text read-aloud:

```bash
export TTS_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-4b-tts.single-gpu.toml"
export TTS_MODEL_ID="mistralai/Voxtral-4B-TTS-2603"
export DICTATION_TTS_DEFAULT_VOICE="casual_male"
export DICTATION_TTS_RESPONSE_FORMAT="wav"
```

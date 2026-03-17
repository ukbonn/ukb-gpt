# Dictation App

Optional internal Gradio UI for speech-to-text transcription.

## Enable

```bash
export ENABLE_DICTATION_APP=true
export STT_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-mini-4b.single-gpu.toml"
export STT_MODEL_ID="mistralai/Voxtral-Mini-4B-Realtime-2602"
```

The app is exposed only through ingress at `https://<SERVER_NAME>/dictation/`.

import argparse
import io
import logging
import sys
import time
import uuid
import wave

from flask import Flask, Response, jsonify, request
from werkzeug.serving import WSGIRequestHandler

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
logger = logging.getLogger(__name__)

# vLLM arguments are slightly different, but we accept generic ones to be safe
parser = argparse.ArgumentParser(description="Dummy vLLM Worker")
parser.add_argument("--host", type=str, default="0.0.0.0")
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--model", type=str, default="openai/gpt-oss-120b", help="Name or path of the model")
# Catch-all for other vLLM flags so the container doesn't crash on startup
parser.add_argument("--tensor-parallel-size", "-tp", type=int, default=1)
parser.add_argument("--max-model-len", type=int, default=None)
parser.add_argument("--trust-remote-code", action="store_true")
parser.add_argument(
    "--openwebui-api-compat",
    action="store_true",
    help="Also expose OpenWebUI-style /api/* endpoints.",
)
parser.add_argument(
    "--disable-v1",
    action="store_true",
    help="Disable OpenAI-style /v1/* endpoints.",
)

args, unknown = parser.parse_known_args()
app = Flask(__name__)
# vLLM usually uses the --model argument for the ID
MODEL_ID = args.model


class _InternalDockerHostRequestHandler(WSGIRequestHandler):
    """Normalize Docker service hosts that Werkzeug 3.2 rejects for tests.

    Compose service names like ``worker_0`` are valid inside Docker DNS but the
    dev server's Host validation rejects underscores before Flask can route the
    request. The dummy worker is test-only, so normalizing the Host header keeps
    the mock compatible with OpenWebUI's internal calls without changing the
    production stack.
    """

    def make_environ(self):
        environ = super().make_environ()
        host = environ.get("HTTP_HOST", "")
        if "_" in host:
            environ["HTTP_HOST"] = host.replace("_", "-")
        return environ


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"})


def get_models():
    return jsonify(
        {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "dummy-vllm",
                    "root": MODEL_ID,
                }
            ],
        }
    )


def chat_completions():
    content = "Hello from Dummy vLLM Worker!"
    return jsonify(
        {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": MODEL_ID,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
    )


def _normalize_embedding_inputs(payload):
    raw = payload.get("input", "")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return [str(raw)]


def _fake_embedding(text: str, index: int) -> list[float]:
    seed = (sum(ord(ch) for ch in text) + index) % 1000
    base = seed / 1000.0
    return [round(base + (i * 0.001), 6) for i in range(8)]


def embeddings():
    payload = request.get_json(silent=True) or {}
    inputs = _normalize_embedding_inputs(payload)
    data = []
    for index, text in enumerate(inputs):
        data.append(
            {
                "object": "embedding",
                "embedding": _fake_embedding(text, index),
                "index": index,
            }
        )

    prompt_tokens = sum(max(1, len(text.split())) for text in inputs)
    return jsonify(
        {
            "object": "list",
            "data": data,
            "model": MODEL_ID,
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        }
    )


def audio_transcriptions():
    response_format = (request.form.get("response_format") or "json").strip().lower()
    transcription_text = "Dummy transcription from vLLM worker."

    if response_format == "text":
        return Response(transcription_text, mimetype="text/plain")

    return jsonify(
        {
            "text": transcription_text,
            "model": MODEL_ID,
        }
    )


def _silent_wav_bytes(duration_seconds: float = 0.25, sample_rate: int = 16000) -> bytes:
    frame_count = max(1, int(duration_seconds * sample_rate))
    pcm_frames = b"\x00\x00" * frame_count
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_frames)
    return buffer.getvalue()


def audio_speech():
    response_format = ((request.get_json(silent=True) or {}).get("response_format") or "wav").strip().lower()
    if response_format != "wav":
        response_format = "wav"
    return Response(_silent_wav_bytes(), mimetype="audio/wav")


if not args.disable_v1:
    app.add_url_rule("/v1/models", "get_models_v1", get_models, methods=["GET"])
    app.add_url_rule("/v1/chat/completions", "chat_completions_v1", chat_completions, methods=["POST"])
    app.add_url_rule("/v1/embeddings", "embeddings_v1", embeddings, methods=["POST"])
    app.add_url_rule(
        "/v1/audio/transcriptions",
        "audio_transcriptions_v1",
        audio_transcriptions,
        methods=["POST"],
    )
    app.add_url_rule(
        "/v1/audio/speech",
        "audio_speech_v1",
        audio_speech,
        methods=["POST"],
    )

if args.openwebui_api_compat:
    app.add_url_rule("/api/models", "get_models_api", get_models, methods=["GET"])
    app.add_url_rule("/api/chat/completions", "chat_completions_api", chat_completions, methods=["POST"])
    app.add_url_rule("/api/embeddings", "embeddings_api", embeddings, methods=["POST"])
    app.add_url_rule("/api/v1/embeddings", "embeddings_api_v1", embeddings, methods=["POST"])
    app.add_url_rule(
        "/api/audio/transcriptions",
        "audio_transcriptions_api",
        audio_transcriptions,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/v1/audio/transcriptions",
        "audio_transcriptions_api_v1",
        audio_transcriptions,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/audio/speech",
        "audio_speech_api",
        audio_speech,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/v1/audio/speech",
        "audio_speech_api_v1",
        audio_speech,
        methods=["POST"],
    )


@app.route("/metrics", methods=["GET"])
def get_metrics():
    # vLLM Standard Metrics
    metrics_data = f"""# HELP vllm:num_requests_running Number of requests currently running on GPU.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{{model_name=\"{MODEL_ID}\"}} 0.0
# HELP vllm:num_requests_waiting Number of requests waiting to be processed.
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting{{model_name=\"{MODEL_ID}\"}} 0.0
# HELP vllm:gpu_cache_usage_perc GPU KV-cache usage. 1 means 100 percent usage.
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc{{model_name=\"{MODEL_ID}\"}} 0.0
"""
    return Response(metrics_data, mimetype="text/plain")


if __name__ == "__main__":
    app.run(
        host=args.host,
        port=args.port,
        threaded=True,
        request_handler=_InternalDockerHostRequestHandler,
    )

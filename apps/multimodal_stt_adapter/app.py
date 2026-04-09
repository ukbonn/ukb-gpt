import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from apps.common.stt_contract import (
    canonicalize_conversation_language,
    parse_structured_transcription,
    transcription_response_format,
)

try:
    import uvicorn
except ModuleNotFoundError:  # pragma: no cover - exercised in local test envs
    uvicorn = None


ADAPTER_HOST = os.getenv("MULTIMODAL_STT_ADAPTER_HOST", "0.0.0.0")
ADAPTER_PORT = int(os.getenv("MULTIMODAL_STT_ADAPTER_PORT", "5000"))
INNER_VLLM_HOST = os.getenv("MULTIMODAL_STT_INNER_HOST", "127.0.0.1")
INNER_VLLM_PORT = int(os.getenv("MULTIMODAL_STT_INNER_PORT", "5001"))
INNER_VLLM_BASE_URL = f"http://{INNER_VLLM_HOST}:{INNER_VLLM_PORT}"
INNER_STARTUP_TIMEOUT_SECONDS = int(
    os.getenv("MULTIMODAL_STT_STARTUP_TIMEOUT_SECONDS", "1800")
)
UPSTREAM_REQUEST_TIMEOUT_SECONDS = int(
    os.getenv("MULTIMODAL_STT_UPSTREAM_TIMEOUT_SECONDS", "300")
)
SERVED_MODEL_ID = os.getenv(
    "MULTIMODAL_STT_SERVED_MODEL",
    os.getenv("STT_MODEL_ID", "google/gemma-4-E4B-it"),
).strip() or "google/gemma-4-E4B-it"
TRANSCRIPTION_MAX_TOKENS = int(
    os.getenv("MULTIMODAL_STT_TRANSCRIPTION_MAX_TOKENS", "2048")
)

_DEFAULT_TRANSCRIPTION_PROMPT = (
    "Provide a verbatim, word-for-word transcription of the audio. "
    "Return structured JSON only. "
    "The transcription must stay on one line, use digits for numbers and dates, "
    "and prefer medical wording if the audio is ambiguous."
)
_HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "content-encoding",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _normalize_audio_format(filename: str, content_type: str) -> str:
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if suffix:
        return suffix

    if "/" in content_type:
        subtype = content_type.split("/", 1)[1].strip().lower()
        if subtype:
            return subtype

    return "wav"


def _build_transcription_prompt(*, prompt: str, language: str) -> str:
    prompt = (prompt or "").strip()
    if prompt:
        base_prompt = prompt
    else:
        language = (language or "").strip()
        if not language:
            base_prompt = _DEFAULT_TRANSCRIPTION_PROMPT
        else:
            preferred_language = canonicalize_conversation_language(language, "English")
            base_prompt = (
                f"Provide a verbatim, word-for-word transcription of the audio. "
                f"If the spoken language is clear, prefer {preferred_language}. "
                "Return structured JSON only. "
                "The transcription must stay on one line, use digits for numbers and dates, "
                "and prefer medical wording if the audio is ambiguous."
            )
    return base_prompt


def build_transcription_chat_payload(
    *,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    model: str,
    prompt: str,
    language: str,
) -> dict[str, object]:
    if not audio_bytes:
        raise ValueError("Uploaded audio file is empty.")

    audio_format = _normalize_audio_format(filename, content_type)
    instruction = _build_transcription_prompt(prompt=prompt, language=language)
    return {
        "model": (model or SERVED_MODEL_ID).strip() or SERVED_MODEL_ID,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": base64.b64encode(audio_bytes).decode("ascii"),
                            "format": audio_format,
                        },
                    },
                    {
                        "type": "text",
                        "text": instruction,
                    },
                ],
            }
        ],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": TRANSCRIPTION_MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": transcription_response_format("adapter_transcription"),
    }


def extract_transcription_text(payload: dict[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Gemma STT backend returned no choices.")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("Gemma STT backend returned an invalid choice payload.")

    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("Gemma STT backend returned no assistant message.")

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        text = content.strip()
        try:
            return parse_structured_transcription(text).transcription
        except ValueError:
            return text

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            combined = "\n".join(parts).strip()
            try:
                return parse_structured_transcription(combined).transcription
            except ValueError:
                return combined

    raise ValueError("Gemma STT backend returned an empty transcription.")


def _filtered_request_headers(headers: Request.headers.__class__) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }


def _filtered_response_headers(headers: requests.structures.CaseInsensitiveDict) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }


def _response_from_upstream(upstream: requests.Response) -> Response:
    content_type = upstream.headers.get("content-type", "")
    response_headers = _filtered_response_headers(upstream.headers)

    if "text/event-stream" in content_type.lower():
        def _iter_chunks() -> Iterator[bytes]:
            try:
                for chunk in upstream.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            _iter_chunks(),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=content_type or None,
        )

    try:
        body = upstream.content
    finally:
        upstream.close()

    return Response(
        content=body,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=content_type or None,
    )


class InnerVllmController:
    def __init__(self, *, inner_args: tuple[str, ...], http_session: requests.Session):
        self._inner_args = inner_args
        self._http_session = http_session
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._process is not None:
            return
        if not self._inner_args:
            raise RuntimeError("Gemma STT adapter requires inner vLLM launch arguments.")

        self._process = subprocess.Popen(
            ["vllm", "serve", *self._inner_args],
            start_new_session=True,
        )
        self._wait_until_ready()

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is not None:
            self._process = None
            return

        try:
            os.killpg(self._process.pid, signal.SIGTERM)
        except ProcessLookupError:
            self._process = None
            return

        try:
            self._process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._process.wait(timeout=10)
        finally:
            self._process = None

    def _wait_until_ready(self) -> None:
        deadline = time.time() + INNER_STARTUP_TIMEOUT_SECONDS
        last_error = "timed out waiting for inner vLLM health endpoint"

        while time.time() < deadline:
            if self._process is None:
                raise RuntimeError("Inner vLLM process was not started.")

            exit_code = self._process.poll()
            if exit_code is not None:
                raise RuntimeError(
                    f"Inner vLLM process exited before readiness with code {exit_code}."
                )

            try:
                response = self._http_session.get(
                    f"{INNER_VLLM_BASE_URL}/health",
                    timeout=5,
                )
                if response.status_code == 200:
                    return
                last_error = f"inner health returned {response.status_code}"
            except requests.RequestException as exc:
                last_error = str(exc)

            time.sleep(2)

        raise RuntimeError(f"Gemma STT adapter startup failed: {last_error}")


def create_app(
    *,
    inner_args: tuple[str, ...] = (),
    manage_inner_process: bool = True,
    http_session: requests.Session | None = None,
) -> FastAPI:
    session = http_session or requests.Session()
    controller = (
        InnerVllmController(inner_args=inner_args, http_session=session)
        if manage_inner_process
        else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if controller is not None:
            controller.start()
        try:
            yield
        finally:
            if controller is not None:
                controller.stop()

    app = FastAPI(title="UKB-GPT Multimodal STT Adapter", lifespan=lifespan)
    app.state.http_session = session

    def _upstream_request(
        *,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        json_body: dict[str, object] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        try:
            return app.state.http_session.request(
                method=method,
                url=f"{INNER_VLLM_BASE_URL}{path}",
                headers=headers,
                data=data,
                json=json_body,
                timeout=UPSTREAM_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/health")
    def health() -> Response:
        upstream = _upstream_request(method="GET", path="/health")
        if upstream.status_code != 200:
            return Response(
                content=upstream.content,
                status_code=503,
                media_type=upstream.headers.get("content-type") or None,
            )
        return Response(status_code=200)

    @app.get("/metrics")
    def metrics() -> Response:
        upstream = _upstream_request(method="GET", path="/metrics")
        return _response_from_upstream(upstream)

    @app.get("/v1/models")
    def models() -> Response:
        upstream = _upstream_request(method="GET", path="/v1/models")
        return _response_from_upstream(upstream)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        raw_body = await request.body()
        upstream = _upstream_request(
            method="POST",
            path="/v1/chat/completions",
            headers=_filtered_request_headers(request.headers),
            data=raw_body,
            stream=True,
        )
        return _response_from_upstream(upstream)

    @app.post("/v1/audio/transcriptions")
    async def audio_transcriptions(
        request: Request,
        file: UploadFile = File(...),
        model: str = Form(""),
        response_format: str = Form("json"),
        language: str = Form(""),
        prompt: str = Form(""),
    ) -> Response:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")

        payload = build_transcription_chat_payload(
            audio_bytes=audio_bytes,
            filename=file.filename or "audio.wav",
            content_type=file.content_type or "",
            model=model,
            prompt=prompt,
            language=language,
        )
        headers = _filtered_request_headers(request.headers)
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        upstream = _upstream_request(
            method="POST",
            path="/v1/chat/completions",
            headers=headers,
            json_body=payload,
        )

        if upstream.status_code >= 400:
            return _response_from_upstream(upstream)

        try:
            response_payload = upstream.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=502,
                detail="Gemma STT backend returned invalid JSON.",
            ) from exc
        finally:
            upstream.close()

        if not isinstance(response_payload, dict):
            raise HTTPException(
                status_code=502,
                detail="Gemma STT backend returned an invalid response payload.",
            )

        try:
            transcript = extract_transcription_text(response_payload)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        normalized_format = (response_format or "json").strip().lower()
        if normalized_format == "text":
            return PlainTextResponse(transcript)

        return JSONResponse(
            {
                "text": transcript,
                "model": (model or SERVED_MODEL_ID).strip() or SERVED_MODEL_ID,
            }
        )

    return app


def main() -> None:
    if uvicorn is None:
        raise RuntimeError("uvicorn is required to run the multimodal STT adapter.")
    app = create_app(
        inner_args=tuple(sys.argv[1:]),
        manage_inner_process=True,
    )
    uvicorn.run(app, host=ADAPTER_HOST, port=ADAPTER_PORT, log_level="info")


if __name__ == "__main__":
    main()

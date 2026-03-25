## implements the patch not merged yet in https://github.com/open-webui/pipelines/pull/597

from pathlib import Path


MAIN_PATH = Path("/app/main.py")
IMPORT_OLD = "import shutil\n"
IMPORT_NEW = "import shutil\nimport inspect\n"
START_MARKER = '@app.post("/v1/{pipeline_id}/filter/inlet")\n@app.post("/{pipeline_id}/filter/inlet")\n'
END_MARKER = '\n\n@app.post("/v1/{pipeline_id}/filter/outlet")\n@app.post("/{pipeline_id}/filter/outlet")\n'
OUTLET_END_MARKER = '\n\n@app.post("/v1/chat/completions")\n@app.post("/chat/completions")\n'
OLD = """    except Exception as e:
        print(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f\"{str(e)}\",
        )
"""
INLET_CALL_OLD = """    try:
        if hasattr(pipeline, "inlet"):
            body = await pipeline.inlet(form_data.body, form_data.user)
            return body
        else:
            return form_data.body
"""
INLET_CALL_NEW = """    try:
        if hasattr(pipeline, "inlet"):
            inlet = pipeline.inlet
            inlet_signature = inspect.signature(inlet)
            inlet_kwargs = {}
            request_context = getattr(form_data, "request", None)
            if "request" in inlet_signature.parameters:
                inlet_kwargs["request"] = request_context
            elif "__request__" in inlet_signature.parameters:
                inlet_kwargs["__request__"] = request_context
            body = await inlet(form_data.body, form_data.user, **inlet_kwargs)
            return body
        else:
            return form_data.body
"""
NEW = """    except HTTPException:
        raise
    except Exception as e:
        print(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f\"{str(e)}\",
        )
"""


def _patch_once(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    block = text[start:end]
    if OLD not in block:
        raise RuntimeError(f"Could not find HTTPException wrapper in block starting with {start_marker!r}")
    return text[:start] + block.replace(OLD, NEW, 1) + text[end:]


def main() -> None:
    text = MAIN_PATH.read_text(encoding="utf-8")
    if IMPORT_NEW not in text:
        if IMPORT_OLD not in text:
            raise RuntimeError("Could not find shutil import to extend with inspect")
        text = text.replace(IMPORT_OLD, IMPORT_NEW, 1)
    if INLET_CALL_NEW not in text:
        if INLET_CALL_OLD not in text:
            raise RuntimeError("Could not find filter inlet call block to patch")
        text = text.replace(INLET_CALL_OLD, INLET_CALL_NEW, 1)
    text = _patch_once(text, START_MARKER, END_MARKER)
    text = _patch_once(text, END_MARKER.lstrip("\n"), OUTLET_END_MARKER)
    MAIN_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()

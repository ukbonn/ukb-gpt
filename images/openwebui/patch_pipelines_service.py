## implements the patch not merged yet in https://github.com/open-webui/pipelines/pull/597

from pathlib import Path


MAIN_PATH = Path("/app/main.py")
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
    text = _patch_once(text, START_MARKER, END_MARKER)
    text = _patch_once(text, END_MARKER.lstrip("\n"), OUTLET_END_MARKER)
    MAIN_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()

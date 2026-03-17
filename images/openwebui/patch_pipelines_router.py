## Implements the patch not yet merged in https://github.com/open-webui/open-webui/pull/22726
## and applies pipeline inlet filters to the OpenWebUI embeddings endpoint.
##
## This repo intentionally keeps the embeddings patch local. If upstream OpenWebUI
## changes and the patch no longer matches, warn loudly in build/start logs but do
## not fail the whole stack.

from pathlib import Path


ROUTER_PATH = Path("/app/backend/open_webui/routers/pipelines.py")
MAIN_PATH = Path("/app/backend/open_webui/main.py")
ROUTER_OLD = '                    raise Exception(response.status, res["detail"])\n'
ROUTER_NEW = '                    raise HTTPException(status_code=e.status, detail=res["detail"])\n'
EMBEDDINGS_IMPORT_OLD = "from open_webui.utils.embeddings import generate_embeddings\n"
EMBEDDINGS_IMPORT_NEW = (
    "from open_webui.utils.embeddings import generate_embeddings\n"
    "from open_webui.routers.pipelines import process_pipeline_inlet_filter\n"
)
EMBEDDINGS_OLD = """    # Make sure models are loaded in app state
    if not request.app.state.MODELS:
        await get_all_models(request, user=user)
    # Use generic dispatcher in utils.embeddings
    return await generate_embeddings(request, form_data, user)
"""
EMBEDDINGS_NEW = """    # Make sure models are loaded in app state
    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    form_data = await process_pipeline_inlet_filter(
        request, form_data, user, request.app.state.MODELS
    )
    # Use generic dispatcher in utils.embeddings
    return await generate_embeddings(request, form_data, user)
"""
MAIN_OLD_VARIANTS = [
    """        except Exception as e:
            log.debug(f\"Error processing chat payload: {e}\")
            if metadata.get(\"chat_id\") and metadata.get(\"message_id\"):
                # Update the chat message with the error
                try:
                    if not metadata[\"chat_id\"].startswith(\"local:\"):
                        Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata[\"chat_id\"],
                            metadata[\"message_id\"],
                            {
                                \"parentId\": metadata.get(\"parent_message_id\", None),
                                \"error\": {\"content\": str(e)},
                            },
                        )

                    event_emitter = get_event_emitter(metadata)
                    await event_emitter(
                        {
                            \"type\": \"chat:message:error\",
                            \"data\": {\"error\": {\"content\": str(e)}},
                        }
                    )
                    await event_emitter(
                        {\"type\": \"chat:tasks:cancel\"},
                    )

                except:
                    pass
""",
    """        except Exception as e:
            log.debug(f\"Error processing chat payload: {e}\")
            if metadata.get(\"chat_id\") and metadata.get(\"message_id\"):
                # Update the chat message with the error
                try:
                    if not metadata[\"chat_id\"].startswith(\"local:\"):
                        Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata[\"chat_id\"],
                            metadata[\"message_id\"],
                            {
                                \"parentId\": metadata.get(\"parent_message_id\", None),
                                \"error\": {\"content\": str(e)},
                            },
                        )

                    event_emitter = get_event_emitter(metadata)
                    await event_emitter(
                        {
                            \"type\": \"chat:message:error\",
                            \"data\": {\"error\": {\"content\": str(e)}},
                        }
                    )
                    await event_emitter(
                        {\"type\": \"chat:tasks:cancel\"},
                    )

                except Exception:
                    pass
""",
]
MAIN_NEW = """        except Exception as e:
            log.debug(f\"Error processing chat payload: {e}\")

            error_content = (
                str(e.detail)
                if isinstance(e, HTTPException) and e.detail is not None
                else str(e)
            )
            if metadata.get(\"chat_id\") and metadata.get(\"message_id\"):
                # Update the chat message with the error
                try:
                    if not metadata[\"chat_id\"].startswith(\"local:\"):
                        Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata[\"chat_id\"],
                            metadata[\"message_id\"],
                            {
                                \"parentId\": metadata.get(\"parent_message_id\", None),
                                \"error\": {\"content\": error_content},
                            },
                        )

                    event_emitter = get_event_emitter(metadata)
                    await event_emitter(
                        {
                            \"type\": \"chat:message:error\",
                            \"data\": {\"error\": {\"content\": error_content}},
                        }
                    )
                    await event_emitter(
                        {\"type\": \"chat:tasks:cancel\"},
                    )

                except Exception:
                    pass

            if isinstance(e, HTTPException):
                raise
"""


def _replace_first(text: str, old_values: list[str], new_value: str) -> tuple[str, bool]:
    for old_value in old_values:
        if old_value in text:
            return text.replace(old_value, new_value, 1), True
    return text, False


def _warn(message: str) -> None:
    print(f"WARNING [patch_pipelines_router]: {message}", flush=True)


def main() -> None:
    try:
        router_text = ROUTER_PATH.read_text(encoding="utf-8")
        if ROUTER_NEW in router_text:
            pass
        elif ROUTER_OLD in router_text:
            ROUTER_PATH.write_text(
                router_text.replace(ROUTER_OLD, ROUTER_NEW, 1),
                encoding="utf-8",
            )
        else:
            _warn("Could not match pipeline HTTP error forwarding block in routers/pipelines.py")

        main_text = MAIN_PATH.read_text(encoding="utf-8")
        main_changed = False

        if EMBEDDINGS_IMPORT_NEW not in main_text:
            if EMBEDDINGS_IMPORT_OLD in main_text:
                main_text = main_text.replace(EMBEDDINGS_IMPORT_OLD, EMBEDDINGS_IMPORT_NEW, 1)
                main_changed = True
            else:
                _warn("Could not match embeddings import block in main.py")

        if EMBEDDINGS_NEW not in main_text:
            if EMBEDDINGS_OLD in main_text:
                main_text = main_text.replace(EMBEDDINGS_OLD, EMBEDDINGS_NEW, 1)
                main_changed = True
            else:
                _warn("Could not match embeddings handler block in main.py")

        if MAIN_NEW not in main_text:
            main_text, replaced = _replace_first(main_text, MAIN_OLD_VARIANTS, MAIN_NEW)
            if replaced:
                main_changed = True
            else:
                _warn("Could not match process_chat exception block in main.py")

        if main_changed:
            MAIN_PATH.write_text(main_text, encoding="utf-8")
    except Exception as exc:
        _warn(f"Unexpected patch failure; continuing without patch: {exc}")


if __name__ == "__main__":
    main()

import pytest

CHATBOT_PROVIDER_MARKS = [
    pytest.mark.integration,
    pytest.mark.chatbot_provider,
    pytest.mark.usefixtures("chatbot_provider_stack"),
]

BATCH_CLIENT_MARKS = [
    pytest.mark.integration,
    pytest.mark.batch_client,
    pytest.mark.usefixtures("batch_client_stack"),
]

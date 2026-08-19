"""Unit tests for parsing Meta's WhatsApp webhook payload shapes into typed
events. Sample payloads match Meta's documented `messages` field format."""

from app.webhooks.whatsapp.schemas import (
    InboundMessageEvent,
    StatusUpdateEvent,
    TemplateStatusUpdateEvent,
    parse_webhook_events,
)

_INBOUND_MESSAGE_PAYLOAD = {
    "entry": [
        {
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "770971286099252"},
                        "contacts": [{"profile": {"name": "Jane"}, "wa_id": "919999999999"}],
                        "messages": [
                            {
                                "from": "919999999999",
                                "id": "wamid.ABC123",
                                "timestamp": "1700000000",
                                "type": "text",
                                "text": {"body": "hello"},
                            }
                        ],
                    },
                }
            ]
        }
    ]
}

_STATUS_UPDATE_PAYLOAD = {
    "entry": [
        {
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "770971286099252"},
                        "statuses": [
                            {
                                "id": "wamid.ABC123",
                                "status": "delivered",
                                "timestamp": "1700000100",
                                "recipient_id": "919999999999",
                            }
                        ],
                    },
                }
            ]
        }
    ]
}

_TEMPLATE_STATUS_PAYLOAD = {
    "entry": [
        {
            "changes": [
                {
                    "field": "message_template_status_update",
                    "value": {
                        "message_template_id": 123456,
                        "message_template_name": "greeting",
                        "message_template_language": "hi",
                        "event": "APPROVED",
                    },
                }
            ]
        }
    ]
}


def test_parse_inbound_message_event() -> None:
    events = parse_webhook_events(_INBOUND_MESSAGE_PAYLOAD)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, InboundMessageEvent)
    assert event.from_phone == "919999999999"
    assert event.wa_message_id == "wamid.ABC123"
    assert event.text == "hello"
    assert event.contact_name == "Jane"


def test_parse_status_update_event() -> None:
    events = parse_webhook_events(_STATUS_UPDATE_PAYLOAD)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, StatusUpdateEvent)
    assert event.wa_message_id == "wamid.ABC123"
    assert event.status == "delivered"


def test_parse_template_status_update_event() -> None:
    events = parse_webhook_events(_TEMPLATE_STATUS_PAYLOAD)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, TemplateStatusUpdateEvent)
    assert event.provider_template_id == "123456"
    assert event.status == "approved"


def test_parse_ignores_unrecognized_fields() -> None:
    payload = {"entry": [{"changes": [{"field": "business_status_update", "value": {}}]}]}
    assert parse_webhook_events(payload) == []


def test_parse_handles_empty_entry_list() -> None:
    assert parse_webhook_events({"entry": []}) == []

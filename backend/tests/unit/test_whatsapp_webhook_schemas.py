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


def test_parse_skips_malformed_message_with_nondict_text() -> None:
    """Verify that a message with non-dict text field is skipped but other messages are processed."""
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "770971286099252"},
                            "contacts": [
                                {"profile": {"name": "Jane"}, "wa_id": "919999999999"},
                                {"profile": {"name": "Bob"}, "wa_id": "919999999998"},
                            ],
                            "messages": [
                                {
                                    "from": "919999999999",
                                    "id": "wamid.GOOD1",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                },
                                {
                                    "from": "919999999998",
                                    "id": "wamid.BAD1",
                                    "timestamp": "1700000001",
                                    "type": "text",
                                    "text": "not-a-dict",  # BAD: text should be a dict
                                },
                                {
                                    "from": "919999999999",
                                    "id": "wamid.GOOD2",
                                    "timestamp": "1700000002",
                                    "type": "text",
                                    "text": {"body": "world"},
                                },
                            ],
                        },
                    }
                ]
            }
        ]
    }
    events = parse_webhook_events(payload)
    # Should process good messages and skip bad one
    assert len(events) == 2
    assert events[0].wa_message_id == "wamid.GOOD1"
    assert events[0].text == "hello"
    assert events[1].wa_message_id == "wamid.GOOD2"
    assert events[1].text == "world"


def test_parse_skips_malformed_message_with_validation_error() -> None:
    """Verify that a message with type-mismatched from field is skipped."""
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "770971286099252"},
                            "contacts": [],
                            "messages": [
                                {
                                    "from": "919999999999",
                                    "id": "wamid.GOOD1",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                },
                                {
                                    "from": 12345,  # BAD: from should be string, triggers pydantic ValidationError
                                    "id": "wamid.BAD1",
                                    "timestamp": "1700000001",
                                    "type": "text",
                                    "text": {"body": "bad"},
                                },
                                {
                                    "from": "919999999999",
                                    "id": "wamid.GOOD2",
                                    "timestamp": "1700000002",
                                    "type": "text",
                                    "text": {"body": "world"},
                                },
                            ],
                        },
                    }
                ]
            }
        ]
    }
    events = parse_webhook_events(payload)
    # Should process good messages and skip bad one
    assert len(events) == 2
    assert events[0].wa_message_id == "wamid.GOOD1"
    assert events[1].wa_message_id == "wamid.GOOD2"


def test_parse_skips_malformed_status_with_nondict_errors() -> None:
    """Verify that a status with non-dict errors entry is skipped."""
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "770971286099252"},
                            "statuses": [
                                {
                                    "id": "wamid.GOOD1",
                                    "status": "delivered",
                                    "timestamp": "1700000100",
                                },
                                {
                                    "id": "wamid.BAD1",
                                    "status": "failed",
                                    "timestamp": "1700000101",
                                    "errors": ["not-a-dict"],  # BAD: errors[0] should be dict
                                },
                                {
                                    "id": "wamid.GOOD2",
                                    "status": "read",
                                    "timestamp": "1700000102",
                                },
                            ],
                        },
                    }
                ]
            }
        ]
    }
    events = parse_webhook_events(payload)
    # Should process good statuses and skip bad one
    assert len(events) == 2
    assert events[0].wa_message_id == "wamid.GOOD1"
    assert events[0].status == "delivered"
    assert events[1].wa_message_id == "wamid.GOOD2"
    assert events[1].status == "read"

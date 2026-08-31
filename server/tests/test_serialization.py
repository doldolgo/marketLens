from app.core.serialization import camelize_json


def test_camelize_json_converts_nested_dict_and_list_keys() -> None:
    assert camelize_json(
        {
            "data_received_at": 1,
            "rows": [{"rate_ask": 1400.0}],
            "error": {"detail": {"available_exchanges": ["upbit"]}},
        }
    ) == {
        "dataReceivedAt": 1,
        "rows": [{"rateAsk": 1400.0}],
        "error": {"detail": {"availableExchanges": ["upbit"]}},
    }

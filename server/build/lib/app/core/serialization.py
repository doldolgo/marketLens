from pydantic.alias_generators import to_camel


def camelize_json(value: object) -> object:
    if isinstance(value, dict):
        return {
            to_camel(key) if isinstance(key, str) else key: camelize_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [camelize_json(item) for item in value]
    return value

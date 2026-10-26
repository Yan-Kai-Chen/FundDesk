"""Validate the deliberately small JSON Schema subset used by bundled contracts."""
import json
import re
from pathlib import Path
from .domain import InputError, require

ROOT = Path(__file__).resolve().parents[1]


def validate(value, schema, document=None, path="$"):
    document = document or schema
    if "$ref" in schema:
        ref = schema["$ref"]
        require(ref.startswith("#/$defs/"), "Only local contract references supported")
        return validate(value, document["$defs"][ref.split("/")[-1]], document, path)
    if "anyOf" in schema:
        for candidate in schema["anyOf"]:
            try:
                validate(value, candidate, document, path)
                return
            except InputError:
                pass
        raise InputError(path + ": no allowed shape matches")
    types = schema.get("type")
    if types:
        names = types if isinstance(types, list) else [types]
        matching = {
            "object": isinstance(value, dict), "array": isinstance(value, list),
            "string": isinstance(value, str), "integer": type(value) is int,
            "number": type(value) in (int, float), "boolean": type(value) is bool,
            "null": value is None
        }
        require(any(matching[name] for name in names), path + ": wrong type")
    if "const" in schema:
        require(type(value) is type(schema["const"]) and value == schema["const"], path + ": wrong constant")
    if "enum" in schema:
        require(value in schema["enum"], path + ": value outside enum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            require(key in value, path + ": missing " + key)
        properties = schema.get("properties", {})
        for key, item in value.items():
            child = properties.get(key)
            if child is None:
                additional = schema.get("additionalProperties", True)
                require(additional is not False, path + ": unknown field " + key)
                child = additional if isinstance(additional, dict) else {}
            validate(item, child, document, path + "." + key)
    elif isinstance(value, list):
        require(len(value) >= schema.get("minItems", 0), path + ": array too short")
        if "maxItems" in schema:
            require(len(value) <= schema["maxItems"], path + ": array too long")
        if schema.get("uniqueItems"):
            require(len({json.dumps(x, sort_keys=True) for x in value}) == len(value), path + ": duplicate items")
        for i, item in enumerate(value):
            validate(item, schema.get("items", {}), document, path + "[" + str(i) + "]")
    elif isinstance(value, str):
        require(len(value) >= schema.get("minLength", 0), path + ": string too short")
        if "pattern" in schema:
            require(re.fullmatch(schema["pattern"], value) is not None, path + ": invalid string format")
    elif type(value) in (int, float):
        if "minimum" in schema:
            require(value >= schema["minimum"], path + ": below minimum")
        if "maximum" in schema:
            require(value <= schema["maximum"], path + ": above maximum")


def _load(name):
    return json.loads((ROOT / "contracts" / name).read_text(encoding="utf-8"))


def validate_input(value):
    validate(value, _load("input.schema.json"))


def validate_output(value):
    validate(value, _load("output.schema.json"))

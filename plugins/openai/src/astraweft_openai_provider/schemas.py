"""Static settings, credential, model parameter, and output Schemas."""

from __future__ import annotations

from collections.abc import Mapping

SETTINGS_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "organization": {
            "type": "string",
            "title": "Organization ID (可选)",
            "minLength": 1,
            "maxLength": 128,
        },
        "project": {
            "type": "string",
            "title": "Project ID (可选)",
            "minLength": 1,
            "maxLength": 128,
        },
        "request_timeout_seconds": {
            "type": "number",
            "title": "请求超时 (秒)",
            "minimum": 1,
            "maximum": 300,
            "default": 60,
        },
    },
    "additionalProperties": False,
}

SETTINGS_UI_SCHEMA: Mapping[str, object] = {
    "ui:order": ["organization", "project", "request_timeout_seconds"],
    "organization": {"ui:widget": "text", "ui:group": "OpenAI"},
    "project": {"ui:widget": "text", "ui:group": "OpenAI"},
    "request_timeout_seconds": {"ui:widget": "number", "ui:group": "Network"},
}

CREDENTIAL_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "api_key": {
            "type": "string",
            "title": "OpenAI API Key",
            "minLength": 1,
            "x-astraweft-secret": True,
        }
    },
    "required": ["api_key"],
    "additionalProperties": False,
}

TEXT_PARAMETER_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "title": "Prompt",
            "minLength": 1,
        },
        "instructions": {
            "type": "string",
            "title": "Instructions",
            "minLength": 1,
        },
        "max_output_tokens": {
            "type": "integer",
            "title": "Max output tokens",
            "minimum": 1,
            "maximum": 100000,
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

TEXT_PARAMETER_UI_SCHEMA: Mapping[str, object] = {
    "ui:order": ["prompt", "instructions", "max_output_tokens"],
    "prompt": {"ui:widget": "textarea"},
    "instructions": {"ui:widget": "textarea"},
    "max_output_tokens": {"ui:widget": "number"},
}

TEXT_OUTPUT_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}

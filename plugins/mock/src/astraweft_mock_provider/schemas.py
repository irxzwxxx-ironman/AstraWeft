"""Static JSON and UI Schemas exposed by the Mock Provider."""

from __future__ import annotations

from collections.abc import Mapping

SETTINGS_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "title": "故障模式",
            "enum": [
                "healthy",
                "authentication_error",
                "rate_limit",
                "unavailable",
                "timeout",
                "protocol_error",
                "task_failed",
            ],
            "default": "healthy",
        },
        "response_mode": {
            "type": "string",
            "title": "任务模式",
            "enum": ["completed", "accepted"],
            "default": "completed",
        },
        "catalog_revision": {
            "type": "integer",
            "title": "模型目录版本",
            "enum": [1, 2],
            "default": 1,
        },
        "delay_ms": {
            "type": "integer",
            "title": "模拟延迟 (毫秒)",
            "minimum": 0,
            "maximum": 250,
            "default": 0,
        },
    },
    "additionalProperties": False,
}

SETTINGS_UI_SCHEMA: Mapping[str, object] = {
    "ui:order": ["mode", "response_mode", "catalog_revision", "delay_ms"],
    "mode": {"ui:widget": "select", "ui:group": "Behavior"},
    "response_mode": {"ui:widget": "segmented", "ui:group": "Behavior"},
    "catalog_revision": {"ui:widget": "select", "ui:group": "Catalog"},
    "delay_ms": {"ui:widget": "number", "ui:group": "Behavior"},
}

CREDENTIAL_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "api_key": {
            "type": "string",
            "title": "Mock API Key",
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
        "prompt": {"type": "string", "title": "Prompt", "minLength": 1},
        "temperature": {
            "type": "number",
            "title": "Temperature",
            "minimum": 0,
            "maximum": 2,
            "default": 0.7,
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

IMAGE_PARAMETER_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "title": "Prompt", "minLength": 1},
        "width": {"type": "integer", "enum": [512, 1024], "default": 1024},
        "height": {"type": "integer", "enum": [512, 1024], "default": 1024},
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

VIDEO_PARAMETER_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "title": "Prompt", "minLength": 1},
        "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

PARAMETER_UI_SCHEMA: Mapping[str, object] = {
    "ui:order": ["prompt", "temperature", "width", "height", "duration_seconds"],
    "prompt": {"ui:widget": "textarea"},
}

TEXT_OUTPUT_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}

ARTIFACT_OUTPUT_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"artifact_count": {"type": "integer", "minimum": 1}},
    "required": ["artifact_count"],
    "additionalProperties": False,
}

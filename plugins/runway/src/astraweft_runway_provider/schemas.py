"""Runway settings, credentials, model parameters, and outputs."""

from __future__ import annotations

from collections.abc import Mapping

SETTINGS_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "request_timeout_seconds": {
            "type": "number",
            "title": "请求超时 (秒)",
            "minimum": 1,
            "maximum": 300,
            "default": 60,
        },
        "poll_interval_seconds": {
            "type": "number",
            "title": "轮询间隔 (秒)",
            "minimum": 5,
            "maximum": 60,
            "default": 5,
        },
    },
    "additionalProperties": False,
}

SETTINGS_UI_SCHEMA: Mapping[str, object] = {
    "ui:order": ["request_timeout_seconds", "poll_interval_seconds"],
    "request_timeout_seconds": {"ui:widget": "number", "ui:group": "Network"},
    "poll_interval_seconds": {"ui:widget": "number", "ui:group": "Tasks"},
}

CREDENTIAL_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "api_key": {
            "type": "string",
            "title": "Runway API Key",
            "minLength": 1,
            "x-astraweft-secret": True,
        }
    },
    "required": ["api_key"],
    "additionalProperties": False,
}

VIDEO_PARAMETER_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "title": "Prompt",
            "minLength": 1,
            "maxLength": 1000,
        },
        "duration": {
            "type": "integer",
            "title": "时长 (秒)",
            "minimum": 2,
            "maximum": 10,
            "default": 5,
        },
        "ratio": {
            "type": "string",
            "title": "画幅",
            "enum": ["1280:720", "720:1280"],
            "default": "1280:720",
        },
        "seed": {
            "type": "integer",
            "title": "Seed (可选)",
            "minimum": 0,
            "maximum": 4294967295,
        },
    },
    "required": ["prompt", "duration", "ratio"],
    "additionalProperties": False,
}

VIDEO_PARAMETER_UI_SCHEMA: Mapping[str, object] = {
    "ui:order": ["prompt", "duration", "ratio", "seed"],
    "prompt": {"ui:widget": "textarea"},
    "duration": {"ui:widget": "number"},
    "ratio": {"ui:widget": "select"},
    "seed": {"ui:widget": "number"},
}

VIDEO_OUTPUT_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"video_count": {"type": "integer", "minimum": 1}},
    "required": ["video_count"],
    "additionalProperties": False,
}

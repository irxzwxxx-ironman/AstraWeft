"""Configuration schemas and an editable multi-endpoint starter definition."""

from __future__ import annotations

from collections.abc import Mapping

STARTER_DEFINITION: Mapping[str, object] = {
    "health": {"method": "GET", "path": "/health"},
    "models": [
        {
            "id": "replace-with-remote-model-id",
            "name": "My image model",
            "modality": "IMAGE",
            "operations": ["image.generate"],
            "input_schema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "title": "Prompt", "minLength": 1},
                    "width": {"type": "integer", "default": 1024, "minimum": 64},
                    "height": {"type": "integer", "default": 1024, "minimum": 64},
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
            "input_ui_schema": {"prompt": {"ui:widget": "textarea"}},
            "requests": {
                "image.generate": {
                    "submit": {
                        "method": "POST",
                        "path": "/v1/generate",
                        "headers": {"Accept": "application/json"},
                        "body": {
                            "model": "${model_id}",
                            "prompt": "${input.prompt}",
                            "width": "${input.width}",
                            "height": "${input.height}",
                        },
                    },
                    "response": {
                        "mode": "sync",
                        "output": {
                            "data": {},
                            "artifacts": [
                                {
                                    "kind": "image",
                                    "source": "url",
                                    "pointer": "/data/0/url",
                                    "mime_type": "image/png",
                                }
                            ],
                        },
                    },
                }
            },
        }
    ],
}

SETTINGS_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "auth_mode": {
            "type": "string",
            "title": "鉴权方式",
            "enum": ["bearer", "api_key_header", "basic", "custom_templates", "none"],
            "default": "bearer",
            "x-astraweft-i18n": {"en_US": {"title": "Authentication"}},
        },
        "auth_header_name": {
            "type": "string",
            "title": "API Key 请求头名",
            "default": "X-API-Key",
            "minLength": 1,
            "maxLength": 128,
            "x-astraweft-i18n": {"en_US": {"title": "API key header name"}},
        },
        "auth_prefix": {
            "type": "string",
            "title": "API Key 前缀 (可留空)",
            "default": "",
            "maxLength": 64,
            "x-astraweft-i18n": {"en_US": {"title": "API key prefix (optional)"}},
        },
        "request_timeout_seconds": {
            "type": "number",
            "title": "单次请求超时 (秒)",
            "minimum": 1,
            "maximum": 900,
            "default": 120,
            "x-astraweft-i18n": {"en_US": {"title": "Request timeout (seconds)"}},
        },
        "additional_allowed_hosts": {
            "type": "array",
            "title": "产物下载域名 JSON",
            "description": "响应中的图片/视频如果来自其他域名，在这里逐项填写完整域名。",
            "items": {"type": "string", "minLength": 1, "maxLength": 253},
            "default": [],
            "maxItems": 32,
            "x-astraweft-i18n": {
                "en_US": {
                    "title": "Artifact download hosts (JSON)",
                    "description": "List exact hosts used by image/video URLs returned by the API.",
                }
            },
        },
        "definition": {
            "type": "object",
            "title": "API 转发定义 JSON",
            "description": "可配置多个模型、operation、提交/轮询/取消接口和响应字段映射。",
            "default": STARTER_DEFINITION,
            "required": ["models"],
            "properties": {
                "health": {"type": "object"},
                "models": {"type": "array", "minItems": 1, "maxItems": 100},
            },
            "additionalProperties": False,
            "x-astraweft-i18n": {
                "en_US": {
                    "title": "API forwarding definition (JSON)",
                    "description": "Configure models, operations, submit/poll/cancel routes and response mappings.",
                }
            },
        },
    },
    "required": ["auth_mode", "request_timeout_seconds", "additional_allowed_hosts", "definition"],
    "additionalProperties": False,
}

SETTINGS_UI_SCHEMA: Mapping[str, object] = {
    "ui:order": [
        "auth_mode",
        "auth_header_name",
        "auth_prefix",
        "request_timeout_seconds",
        "additional_allowed_hosts",
        "definition",
    ],
    "additional_allowed_hosts": {"ui:widget": "json"},
    "definition": {"ui:widget": "json"},
}

CREDENTIAL_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "api_key": {
            "type": "string",
            "title": "API Key / Bearer Token",
            "minLength": 1,
            "x-astraweft-secret": True,
        },
        "api_secret": {
            "type": "string",
            "title": "API Secret (可选)",
            "minLength": 1,
            "x-astraweft-secret": True,
            "x-astraweft-i18n": {"en_US": {"title": "API Secret (optional)"}},
        },
        "username": {
            "type": "string",
            "title": "Basic 用户名 (可选)",
            "minLength": 1,
            "x-astraweft-secret": True,
            "x-astraweft-i18n": {"en_US": {"title": "Basic username (optional)"}},
        },
        "password": {
            "type": "string",
            "title": "Basic 密码 (可选)",
            "minLength": 1,
            "x-astraweft-secret": True,
            "x-astraweft-i18n": {"en_US": {"title": "Basic password (optional)"}},
        },
    },
    "additionalProperties": False,
}

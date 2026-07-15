"""Deterministic, non-executable Core transforms for workflow nodes."""

from __future__ import annotations

import string
from collections.abc import Mapping, Sequence


class TransformConfigurationError(ValueError):
    """A transform uses unsupported or executable semantics."""


def validate_transform_config(config: Mapping[str, object]) -> None:
    kind = config.get("kind")
    if kind == "project":
        outputs = config.get("outputs")
        if not isinstance(outputs, Mapping) or not outputs:
            raise TransformConfigurationError("project 转换必须配置 outputs")
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in outputs.items()
        ):
            raise TransformConfigurationError("project outputs 必须是输出端口到输入端口的映射")
        if set(config) != {"kind", "outputs"}:
            raise TransformConfigurationError("project 转换包含未知配置字段")
        return
    if kind == "text_template":
        template = config.get("template")
        output = config.get("output")
        if not isinstance(template, str) or not template or not isinstance(output, str):
            raise TransformConfigurationError("text_template 必须配置 template 和 output")
        if set(config) != {"kind", "template", "output"}:
            raise TransformConfigurationError("text_template 转换包含未知配置字段")
        _template_fields(template)
        return
    raise TransformConfigurationError("转换类型不受支持")


def execute_transform(
    config: Mapping[str, object],
    inputs: Mapping[str, object],
) -> dict[str, object]:
    validate_transform_config(config)
    if config["kind"] == "project":
        outputs = config["outputs"]
        if not isinstance(outputs, Mapping):  # pragma: no cover - validated above
            raise TransformConfigurationError("project outputs 无效")
        result: dict[str, object] = {}
        for output_name, input_name in outputs.items():
            if not isinstance(output_name, str) or not isinstance(input_name, str):
                raise TransformConfigurationError("project outputs 无效")
            if input_name not in inputs:
                raise TransformConfigurationError(f"project 输入不存在：{input_name}")
            result[output_name] = inputs[input_name]
        return result

    template = config["template"]
    output = config["output"]
    if not isinstance(template, str) or not isinstance(output, str):  # pragma: no cover
        raise TransformConfigurationError("text_template 配置无效")
    values: dict[str, object] = {}
    for field in _template_fields(template):
        if field not in inputs:
            raise TransformConfigurationError(f"模板输入不存在：{field}")
        value = inputs[field]
        if isinstance(value, Mapping) or (
            isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
        ):
            raise TransformConfigurationError(f"模板输入必须是标量：{field}")
        values[field] = value
    return {output: template.format_map(values)}


def _template_fields(template: str) -> tuple[str, ...]:
    fields: list[str] = []
    try:
        parsed = string.Formatter().parse(template)
        for _literal, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if (
                not field_name.isidentifier()
                or "." in field_name
                or "[" in field_name
                or format_spec
                or conversion is not None
            ):
                raise TransformConfigurationError("模板只允许简单的 {input_port} 占位符")
            fields.append(field_name)
    except ValueError as exc:
        raise TransformConfigurationError("模板括号格式无效") from exc
    return tuple(dict.fromkeys(fields))

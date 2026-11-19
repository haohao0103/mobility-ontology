"""Palantir 追齐层：属性注册表 / 接口 / 安全 / 函数注册表 / 使用度量。

设计原则（承重验收）：所有能力定义在 enhancements/ontology_extension.json，
只改该 JSON 即可启用/关闭/改变行为，enhancements 代码零改动。
"""
from enhancements.registry import ExtensionRegistry  # noqa: F401

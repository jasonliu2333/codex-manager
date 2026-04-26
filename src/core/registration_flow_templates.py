"""
注册流程模板
用于在 UI 中选择不同注册流程（不可编辑）
"""

import json
import os
from typing import List, Dict, Any


_DEFAULT_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "default",
        "name": "默认流程（auth.openai.com）",
        "version": "2026-03-30",
        "description": "当前系统内置注册流程：直接走 auth.openai.com 注册接口，随后再走 Codex OAuth。",
    },
    {
        "id": "topic_1848126",
        "name": "topic_1848126 流程",
        "version": "2026-03-30",
        "description": "基于 chatgpt_register20260330.py 的流程：先走 chatgpt.com NextAuth 入口，再进入 auth.openai.com 注册。",
    },
    {
        "id": "topic_1840923",
        "name": "topic_1840923 流程",
        "version": "2026-03-30",
        "description": "基于 topic_1840923 注册流程：注册完成后优先复用 session 直接取 ChatGPT Session/AccessToken，失败再回退 OAuth 补全。",
    },
    {
        "id": "topic_1849054",
        "name": "topic_1849054 流程",
        "version": "2026-03-30",
        "description": "基于 topic_1849054 目录代码：支持 direct_auth 或 chatgpt_web 入口，注册后通过 consent + /oauth/token 换取 Codex Token。",
    },
]


def _templates_dir() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, "templates", "registration_flows")


def _load_templates_from_files() -> List[Dict[str, Any]]:
    templates_dir = _templates_dir()
    if not os.path.isdir(templates_dir):
        return []

    templates: List[Dict[str, Any]] = []
    for filename in sorted(os.listdir(templates_dir)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(templates_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                templates.append(data)
        except Exception:
            continue
    return templates


def get_registration_flow_templates() -> List[Dict[str, Any]]:
    """获取注册流程模板列表（优先文件定义，回退默认）"""
    templates = _load_templates_from_files()
    if templates:
        return templates
    return list(_DEFAULT_TEMPLATES)


def normalize_flow_template(flow_id: str) -> str:
    """校验模板 ID，非法时回退 default"""
    if flow_id == "chatgpt_20260330":
        return "topic_1848126"
    candidates = {tpl.get("id") for tpl in get_registration_flow_templates() if tpl.get("id")}
    if flow_id and flow_id in candidates:
        return flow_id
    return "default"

"""请求作用域的依赖取值助手。"""
from __future__ import annotations

from fastapi import Request

from .config import Settings
from .store import Store


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_settings(request: Request) -> Settings:
    return request.app.state.settings

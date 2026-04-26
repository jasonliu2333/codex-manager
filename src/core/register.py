"""
注册流程入口（模板分发器）
"""

import logging
from typing import Optional, Callable

from .registration_flow_templates import normalize_flow_template
from .registration_types import RegistrationResult
from ..services import BaseEmailService

logger = logging.getLogger(__name__)


class RegistrationEngine:
    """
    注册引擎（模板分发器）
    根据 flow_template 选择不同的独立引擎实现。
    """

    def __init__(
        self,
        email_service: BaseEmailService,
        proxy_url: Optional[str] = None,
        callback_logger: Optional[Callable[[str], None]] = None,
        task_uuid: Optional[str] = None,
        flow_template: Optional[str] = None,
    ):
        self._flow_template = normalize_flow_template(flow_template or "default")
        self._engine = self._create_engine(
            flow_template=self._flow_template,
            email_service=email_service,
            proxy_url=proxy_url,
            callback_logger=callback_logger,
            task_uuid=task_uuid,
        )

    @property
    def flow_template(self) -> str:
        return self._flow_template

    def _create_engine(
        self,
        flow_template: str,
        email_service: BaseEmailService,
        proxy_url: Optional[str],
        callback_logger: Optional[Callable[[str], None]],
        task_uuid: Optional[str],
    ):
        if flow_template == "topic_1848126":
            from .registration_flows.topic_1848126_engine import Topic1848126RegistrationEngine
            return Topic1848126RegistrationEngine(
                email_service=email_service,
                proxy_url=proxy_url,
                callback_logger=callback_logger,
                task_uuid=task_uuid,
            )
        if flow_template == "topic_1840923":
            from .registration_flows.topic_1840923_engine import Topic1840923RegistrationEngine
            return Topic1840923RegistrationEngine(
                email_service=email_service,
                proxy_url=proxy_url,
                callback_logger=callback_logger,
                task_uuid=task_uuid,
            )
        if flow_template == "topic_1849054":
            from .registration_flows.topic_1849054_engine import Topic1849054RegistrationEngine
            return Topic1849054RegistrationEngine(
                email_service=email_service,
                proxy_url=proxy_url,
                callback_logger=callback_logger,
                task_uuid=task_uuid,
            )

        from .registration_flows.default_engine import DefaultRegistrationEngine
        return DefaultRegistrationEngine(
            email_service=email_service,
            proxy_url=proxy_url,
            callback_logger=callback_logger,
            task_uuid=task_uuid,
        )

    def run(self) -> RegistrationResult:
        return self._engine.run()

    def save_to_database(self, result: RegistrationResult) -> bool:
        return self._engine.save_to_database(result)

    def recover_oauth_tokens(self, email: str, password: str):
        return self._engine.recover_oauth_tokens(email, password)

    def __getattr__(self, name: str):
        return getattr(self._engine, name)

    def __setattr__(self, name: str, value):
        if name in {"_engine", "_flow_template"} or name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if "_engine" in self.__dict__:
            setattr(self._engine, name, value)
            return
        object.__setattr__(self, name, value)


__all__ = ["RegistrationEngine", "RegistrationResult"]

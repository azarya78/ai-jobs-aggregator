"""Connector Registry mapping declarative source methods to independent connector classes."""
from __future__ import annotations
from typing import Dict, Type, Any
from .base import BaseConnector
from .lever import LeverConnector
from .greenhouse import GreenhouseConnector
from .ashby import AshbyConnector
from .smartrecruiters import SmartRecruitersConnector
from .teamtailor import TeamtailorConnector
from .public_web import (
    MercorConnector,
    Micro1Connector,
    AlignerrConnector,
    OneFormaConnector,
    TelusAIConnector,
    MindriftConnector,
    IMeritConnector,
)
from ..http_client import PoliteHTTPClient

CONNECTOR_REGISTRY: Dict[str, Type[BaseConnector]] = {
    "lever_api": LeverConnector,
    "greenhouse_api": GreenhouseConnector,
    "ashby_api": AshbyConnector,
    "smartrecruiters_api": SmartRecruitersConnector,
    "teamtailor_feed": TeamtailorConnector,
    "mercor_public_web": MercorConnector,
    "micro1_public_web": Micro1Connector,
    "alignerr_public_web": AlignerrConnector,
    "oneforma_public_web": OneFormaConnector,
    "telus_ai_public_web": TelusAIConnector,
    "mindrift_public_web": MindriftConnector,
    "imerit_public_web": IMeritConnector,
}


def create_connector(source_cfg: Dict[str, Any], http_client: PoliteHTTPClient) -> BaseConnector:
    method = source_cfg.get("method", "")
    cls = CONNECTOR_REGISTRY.get(method)
    if cls is None:
        raise ValueError(f"Unsupported connector method: {method}")
    return cls(source_cfg, http_client)

"""Base Connector interface for independent, fault-isolated source collection."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any, List
from ..http_client import PoliteHTTPClient
from ..models import RawJobListing


class BaseConnector(ABC):
    def __init__(self, config: Dict[str, Any], http_client: PoliteHTTPClient):
        self.config = config
        self.http = http_client
        self.source_id: str = config["id"]
        self.platform: str = config["platform"]
        self.official_url: str = config["official_url"]
        self.api_endpoint: str = config["api_endpoint"]
        self.method: str = config["method"]
        self.logo: str = config.get("logo", "")
        self.pure_ai_eval_board: bool = bool(config.get("pure_ai_eval_board", False))
        self.rate_limit_rps: float = float(config.get("rate_limit_rps", 1.5))
        self.timeout_sec: int = int(config.get("timeout_sec", 20))

    @abstractmethod
    def fetch_listings(self) -> List[RawJobListing]:
        raise NotImplementedError

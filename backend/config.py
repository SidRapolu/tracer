from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ingestion.source import LogSource

_DEFAULT_FIXTURE = str(Path(__file__).resolve().parents[1] / "fixtures" / "sample_logs.json")


@dataclass(frozen=True)
class Config:
    log_source: str
    fixture_path: str
    aws_region: str


def load_config() -> Config:
    return Config(
        log_source=os.environ.get("TRACER_LOG_SOURCE", "cloudwatch"),
        fixture_path=os.environ.get("TRACER_FIXTURE_PATH", _DEFAULT_FIXTURE),
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
    )


def build_log_source(config: Config) -> LogSource:
    if config.log_source == "fixture":
        from ingestion.fixtures import FixtureLogSource

        return FixtureLogSource(config.fixture_path)

    if config.log_source == "cloudwatch":
        import boto3

        from ingestion.cloudwatch import CloudWatchLogSource

        return CloudWatchLogSource(boto3.client("logs", region_name=config.aws_region))

    raise ValueError(f"unknown log source: {config.log_source!r}")


def build_embedder(config: Config):
    import boto3

    from signatures.embed import TitanEmbedder

    return TitanEmbedder(boto3.client("bedrock-runtime", region_name=config.aws_region))

"""Model roles name Agent Models selections declared per cluster, never provider settings."""
import json

import pytest
import yaml

from test_agent_platform import ROOT, resource

ROLE_ENV = {
    "agent-memory": {"embedding": "EMBEDDING_MODEL", "reranker": "RERANKER_MODEL",
                     "decision": "DECISION_MODEL", "dream": "DREAM_MODEL"},
    "agent-knowledge": {"embedding": "EMBEDDING_MODEL", "reranker": "RERANKER_MODEL",
                     "extraction": "KNOWLEDGE_EXTRACTION_MODEL"},
}


@pytest.mark.parametrize("platform", ["eks", "k3s"])
@pytest.mark.parametrize("chart", sorted(ROLE_ENV))
def test_declared_roles_reach_the_service_and_undeclared_roles_stay_unset(chart, platform):
    env = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
    models = env[chart.replace("-", "_")].get("models", {})
    config = resource(chart, platform, "ConfigMap", chart)["data"]
    for key, name in ROLE_ENV[chart].items():
        assert config.get(name) == models.get(key)


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_knowledge_audio_options_match_its_strict_schema(platform):
    env = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
    models = env["agent_knowledge"].get("models", {})
    config = resource("agent-knowledge", platform, "ConfigMap", "agent-knowledge")["data"]
    expected = [{"id": models[kind], "kind": kind} for kind in ("transcription", "summary") if kind in models]
    if not expected:
        assert "AUDIO_PROCESSING_OPTIONS" not in config
        return
    options = json.loads(config["AUDIO_PROCESSING_OPTIONS"])
    assert options == expected
    assert len({option["id"] for option in options}) == len(options)

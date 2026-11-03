"""Model roles name Agent Models selections declared per cluster, never provider settings."""
import json

import pytest
import yaml

from test_agent_platform import ROOT, resource

ROLE_ENV = {
    "agent-memory": {"embedding": "EMBEDDING_MODEL", "reranker": "RERANKER_MODEL",
                     "decision": "DECISION_MODEL", "dream": "DREAM_MODEL"},
    "agent-knowledge": {"embedding": "EMBEDDING_MODEL", "reranker": "RERANKER_MODEL",
                     "extraction": "KNOWLEDGE_EXTRACTION_MODEL", "verification": "KNOWLEDGE_VERIFICATION_MODEL"},
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


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_studio_model_defaults_reach_the_process_without_provider_configuration(platform):
    env = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
    expected = env["agent_studio"].get("model_usage")
    config = resource("agent-studio", platform, "ConfigMap", "agent-studio")["data"]
    if expected is None:
        assert "MODEL_USAGE_DEFAULTS" not in config
    else:
        assert json.loads(config["MODEL_USAGE_DEFAULTS"]) == expected
        assert config["MODELS_URL"] == "http://agent-models.agent-models.svc.cluster.local:3400"


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_forge_model_roles_have_one_config_source(platform):
    env = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
    expected = env["agent_forge"].get("model_roles")
    config = resource("agent-forge", platform, "ConfigMap", "agent-forge")["data"]
    secret = resource("agent-forge", platform, "ExternalSecret", "agent-forge-external")
    secret_names = {item["secretKey"] for item in secret["spec"]["data"]}
    if expected is None:
        assert "FORGE_MODEL_CHANNELS" not in config
        assert "FORGE_MODEL_CHANNELS" in secret_names
    else:
        assert json.loads(config["FORGE_MODEL_CHANNELS"]) == expected
        assert "FORGE_MODEL_CHANNELS" not in secret_names
        assert len({role["id"] for role in expected}) == len(expected)
        assert all(set(role) == {"id", "runtime", "model", "protocol", "maxOutputTokens", "maxRequestsPerRun"} for role in expected)

"""AgentOps Identity alone connects to the IdP; every Agent service verifies Identity's tokens."""
from pathlib import Path

import pytest
import yaml

from gen_values import validate_agent_oidc, validate_agentops_identity
from test_agent_platform import resource

ROOT = Path(__file__).resolve().parents[1]
# Every service that verifies AgentOps Identity tokens, and whether it holds a client secret at Identity.
SERVICES = {"studio": True, "memory": True, "knowledge": True, "forge": True, "runway": True, "models": False}
LEGACY = {"OIDC_ISSUER", "OIDC_PROVIDER_ID", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_SCOPES", "OIDC_PROFILE_CLAIMS",
          "OIDC_LOGOUT_MODE", "OIDC_TRUST_EMAIL", "ADMIN_EMAILS", "ALLOWED_EMAIL_DOMAINS", "GOOGLE_CLIENT_ID",
          "GOOGLE_CLIENT_SECRET", "AUTH_PASSWORD", "GIT_HOSTS", "GIT_HOST_SECRETS", "FORGE_ORGANIZATION_ID"}


def env():
    return yaml.safe_load((ROOT / "env" / "eks-demo.yaml").read_text())


@pytest.mark.parametrize("service,client", SERVICES.items())
@pytest.mark.parametrize("platform", ["eks"])
def test_services_verify_identity_tokens(service, client, platform):
    chart = f"agent-{service}"
    config = resource(chart, platform, "ConfigMap", chart)["data"]
    refs = {entry["secretKey"]: entry["remoteRef"]["key"]
            for entry in resource(chart, platform, "ExternalSecret", f"{chart}-external")["spec"]["data"]}
    assert config["IDENTITY_ISSUER"] == "https://agentops.agrt.dev/identity"
    assert config["IDENTITY_URL"] == "http://agentops-identity.agentops.svc.cluster.local:3000/identity"
    if client:
        assert refs["IDENTITY_CLIENT_SECRET"] == f"/k8s/{platform}-demo/agentops-identity/client-secret-{service}"
    else:
        assert "IDENTITY_CLIENT_SECRET" not in refs
    assert not (set(config) | set(refs)) & LEGACY
    assert not any(name.endswith("_OIDC_CLIENT_ID") for name in refs)
    # A service whose screens moved to AgentOps redirects its old pages to the console (agent-platform docs/AGENTOPS.md).
    assert config["CONSOLE_BASE_URL"] == "https://agentops.agrt.dev"


@pytest.mark.parametrize("platform", ["eks"])
def test_forge_seals_git_delegation_with_its_own_secret(platform):
    refs = {entry["secretKey"]: entry["remoteRef"]["key"]
            for entry in resource("agent-forge", platform, "ExternalSecret", "agent-forge-external")["spec"]["data"]}
    assert refs["FORGE_GIT_DELEGATION_SECRET"] == f"/k8s/{platform}-demo/agent-forge/git-delegation-secret"
    assert refs["FORGE_GIT_DELEGATION_SECRET"] != refs.get("FORGE_CHECKPOINT_SECRET")
    assert "AUTH_SECRET" not in refs


@pytest.mark.parametrize("service", list(SERVICES))
@pytest.mark.parametrize("key,value", [("oidc", {"issuer": "https://sso.example.test"}), ("google_sign_in", False),
                                        ("password_sign_in", False), ("sign_in_domains", ["example.test"]),
                                        ("git_hosts", [])])
def test_service_sign_in_settings_are_rejected_before_generation(service, key, value):
    context = env()
    context[f"agent_{service}"][key] = value
    with pytest.raises(ValueError, match="agentops.identity"):
        validate_agent_oidc(context)


@pytest.mark.parametrize("change,message", [
    ({"oidc_issuer": "http://sso.example.test"}, "https URL"),
    ({"import": False}, "off, count or apply"),
    ({"import": "copy"}, "off, count or apply"),
    ({"enrollment_domains": "example.test"}, "list of domains"),
])
def test_invalid_identity_settings_are_rejected_before_generation(change, message):
    context = env()
    context["agentops"]["identity"].update(change)
    with pytest.raises(ValueError, match=message):
        validate_agentops_identity(context)


def test_demo_identity_settings_are_valid():
    context = env()
    validate_agent_oidc(context)
    validate_agentops_identity(context)

"""Studio alone connects to the enterprise IdP; the other services sign in through Studio."""
import copy
import json
from pathlib import Path
import subprocess

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import pytest
import yaml

from gen_values import validate_agent_oidc
from test_agent_platform import resource

ROOT = Path(__file__).resolve().parents[1]
CONSUMERS = ["memory", "librio", "forge", "models"]


def configured(platform):
    context = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
    context["agent_studio_maintenance"] = False
    settings = context["agent_studio"]
    settings["google_sign_in"] = False
    settings["oidc"] = {
        "issuer": "https://identity.example.test/realms/company",
        "provider_id": "enterprise",
        "scopes": "openid profile email groups",
        "client_id_parameter": f"/k8s/{platform}-demo/agent-studio/oidc-client-id",
        "client_secret_parameter": f"/k8s/{platform}-demo/agent-studio/oidc-client-secret",
        "profile_claims": {"teams": "/directory~1groups", "department": "/company/department", "roles": "/realm_access/roles"},
    }
    return context


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_studio_enterprise_oidc_renders_to_real_manifests(platform):
    context = configured(platform)
    validate_agent_oidc(context)
    chart = ROOT / "charts" / "agent-studio"
    values = Environment(loader=FileSystemLoader(chart), undefined=StrictUndefined).get_template("values-template.yaml.j2").render(context)
    result = subprocess.run(["helm", "template", "agent-studio", str(chart),
        "-f", str(chart / "values.yaml"), "-f", str(chart / f"values-{context['phase']}.yaml"), "-f", "-"],
        input=values, text=True, capture_output=True, check=True)
    documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    config = next(doc["data"] for doc in documents if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "agent-studio")
    secret = next(doc for doc in documents if doc["kind"] == "ExternalSecret" and doc["metadata"]["name"] == "agent-studio-external")
    refs = {entry["secretKey"]: entry["remoteRef"]["key"] for entry in secret["spec"]["data"]}
    oidc = context["agent_studio"]["oidc"]
    assert config["OIDC_ISSUER"] == oidc["issuer"]
    assert config["OIDC_PROVIDER_ID"] == "enterprise"
    assert config["OIDC_SCOPES"] == oidc["scopes"]
    assert json.loads(config["OIDC_PROFILE_CLAIMS"]) == oidc["profile_claims"]
    assert config["OIDC_LOGOUT_MODE"] == "app"
    assert refs["OIDC_CLIENT_ID"] == oidc["client_id_parameter"]
    assert refs["OIDC_CLIENT_SECRET"] == oidc["client_secret_parameter"]
    assert "OIDC_CLIENT_SECRET" not in config
    assert "GOOGLE_CLIENT_ID" not in refs and "GOOGLE_CLIENT_SECRET" not in refs


@pytest.mark.parametrize("service", CONSUMERS)
@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_consumers_sign_in_only_through_studio_with_their_own_client(service, platform):
    chart = f"agent-{service}"
    config = resource(chart, platform, "ConfigMap", chart)["data"]
    refs = {entry["secretKey"]: entry["remoteRef"]["key"]
            for entry in resource(chart, platform, "ExternalSecret", f"{chart}-external")["spec"]["data"]}
    assert config["OIDC_ISSUER"] == "https://studio.agrt.dev/api/auth"
    assert config["OIDC_PROVIDER_ID"] == "studio"
    assert refs["OIDC_CLIENT_ID"] == f"/k8s/{platform}-demo/{chart}/oidc-client-id"
    assert refs["OIDC_CLIENT_SECRET"] == f"/k8s/{platform}-demo/{chart}/oidc-client-secret"
    legacy = {"OIDC_SCOPES", "OIDC_PROFILE_CLAIMS", "OIDC_LOGOUT_MODE", "ADMIN_EMAILS", "ALLOWED_EMAIL_DOMAINS",
              "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "AUTH_PASSWORD"}
    names = set(config) | set(refs)
    assert not names & legacy
    assert not any(name.endswith(("_API_KEY", "_DELEGATION_SECRET", "_DELEGATION_ISSUER", "_ISSUER_POLICIES"))
                   or (name.endswith("_BASE_URL") and name != "AUTH_BASE_URL") for name in names)
    if service != "models":
        assert config["MODELS_URL"] == "http://agent-models.agent-models.svc.cluster.local:3400"
    if service in ("librio", "forge"):
        assert config["STUDIO_URL"] == "http://agent-studio.agent-studio.svc.cluster.local:3000"


@pytest.mark.parametrize("service", CONSUMERS)
@pytest.mark.parametrize("key,value", [("oidc", {"issuer": "https://sso.example.test"}), ("google_sign_in", False),
                                        ("sign_in_domains", ["example.test"])])
def test_consumer_external_sign_in_settings_are_rejected_before_generation(service, key, value):
    context = yaml.safe_load((ROOT / "env" / "k3s-demo.yaml").read_text())
    context[f"agent_{service}"][key] = value
    with pytest.raises(ValueError, match="Studio OIDC"):
        validate_agent_oidc(context)


def test_incomplete_studio_oidc_configuration_is_rejected_before_generation():
    context = configured("k3s")
    for changed in [{}, {"issuer": "https://sso.example.test"}, "not a mapping"]:
        invalid = copy.deepcopy(context)
        invalid["agent_studio"]["oidc"] = changed
        with pytest.raises(ValueError):
            validate_agent_oidc(invalid)
    invalid = copy.deepcopy(context)
    invalid["agent_studio"]["oidc"]["logout_mode"] = "unknown"
    with pytest.raises(ValueError, match="logout_mode"):
        validate_agent_oidc(invalid)
    context["agent_studio"]["google_sign_in"] = "false"
    with pytest.raises(ValueError, match="boolean"):
        validate_agent_oidc(context)

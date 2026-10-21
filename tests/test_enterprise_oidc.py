"""OIDC deployment settings reach the application as strings and scoped Secret references."""
import copy
import json
from pathlib import Path
import subprocess

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import pytest
import yaml

from gen_values import validate_agent_oidc

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ["studio", "memory", "librio", "forge"]


def configured(service, platform):
    context = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
    context[f"agent_{service}_maintenance"] = False
    settings = context[f"agent_{service}"]
    settings["google_sign_in"] = False
    if service == "forge":
        settings["sign_in_domains"] = ["example.test"]
    settings["oidc"] = {
        "issuer": "https://identity.example.test/realms/company",
        "provider_id": "enterprise",
        "scopes": "openid profile email groups",
        "client_id_parameter": f"/k8s/{platform}-demo/agent-{service}/oidc-client-id",
        "client_secret_parameter": f"/k8s/{platform}-demo/agent-{service}/oidc-client-secret",
        "profile_claims": {"teams": "/directory~1groups", "department": "/company/department", "roles": "/realm_access/roles"},
    }
    return context


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_oidc_values_and_credentials_render_to_real_manifests(service, platform):
    context = configured(service, platform)
    validate_agent_oidc(context)
    chart = ROOT / "charts" / f"agent-{service}"
    values = Environment(loader=FileSystemLoader(chart), undefined=StrictUndefined).get_template("values-template.yaml.j2").render(context)
    result = subprocess.run(["helm", "template", f"agent-{service}", str(chart),
        "-f", str(chart / "values.yaml"), "-f", str(chart / f"values-{context['phase']}.yaml"), "-f", "-"],
        input=values, text=True, capture_output=True, check=True)
    documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    config = next(doc["data"] for doc in documents if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == f"agent-{service}")
    secret = next(doc for doc in documents if doc["kind"] == "ExternalSecret" and doc["metadata"]["name"] == f"agent-{service}-external")
    refs = {entry["secretKey"]: entry["remoteRef"]["key"] for entry in secret["spec"]["data"]}
    oidc = context[f"agent_{service}"]["oidc"]
    prefix = ""
    assert config[prefix + "OIDC_ISSUER"] == oidc["issuer"]
    assert config[prefix + "OIDC_PROVIDER_ID"] == "enterprise"
    assert config[prefix + "OIDC_SCOPES"] == oidc["scopes"]
    assert json.loads(config[prefix + "OIDC_PROFILE_CLAIMS"]) == oidc["profile_claims"]
    assert refs[prefix + "OIDC_CLIENT_ID"] == oidc["client_id_parameter"]
    assert refs[prefix + "OIDC_CLIENT_SECRET"] == oidc["client_secret_parameter"]
    assert prefix + "OIDC_CLIENT_SECRET" not in config
    assert "GOOGLE_CLIENT_ID" not in refs and "GOOGLE_CLIENT_SECRET" not in refs
    if service != "forge":
        assert config["OIDC_LOGOUT_MODE"] == "app"


@pytest.mark.parametrize("service", SERVICES)
def test_incomplete_oidc_configuration_is_rejected_before_generation(service):
    context = configured(service, "k3s")
    for changed in [{}, {"issuer": "https://sso.example.test"}, "not a mapping"]:
        invalid = copy.deepcopy(context)
        invalid[f"agent_{service}"]["oidc"] = changed
        with pytest.raises(ValueError):
            validate_agent_oidc(invalid)
    context[f"agent_{service}"]["google_sign_in"] = "false"
    with pytest.raises(ValueError, match="boolean"):
        validate_agent_oidc(context)


def test_forge_does_not_silently_accept_unsupported_provider_logout():
    context = configured("forge", "k3s")
    context["agent_forge"]["oidc"]["logout_mode"] = "provider"
    with pytest.raises(ValueError, match="logout_mode"):
        validate_agent_oidc(context)

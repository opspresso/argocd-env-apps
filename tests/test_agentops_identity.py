"""AgentOps Identity is the platform sign-in; the console asks it for tokens and the services verify them."""

import copy
import subprocess

import pytest
import yaml

from running_environment import running_values
from test_agent_platform import ROOT, resource

ENV = yaml.safe_load((ROOT / "env" / "eks-demo.yaml").read_text())


def manifests_with(import_mode):
    agentops = copy.deepcopy(ENV["agentops"])
    agentops["identity"]["import"] = import_mode
    path = ROOT / "charts" / "agentops"
    if not list((path / "charts").glob("*.tgz")):
        pytest.skip(f"Run helm dependency update {path}")
    result = subprocess.run(
        ["helm", "template", "agentops-eks-demo", str(path), "-f", str(path / "values.yaml"), "-f", "-"],
        input=running_values("agentops", "eks", agentops=agentops), capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def find(docs, kind, name):
    return next((doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name), None)


def test_console_takes_tokens_from_identity_only():
    config = resource("agentops", "eks", "ConfigMap", "agentops")["data"]
    assert config["IDENTITY_URL"] == "http://agentops-identity:3000/identity"
    assert "OIDC_ISSUER" not in config
    secret = resource("agentops", "eks", "ExternalSecret", "agentops-external")
    refs = {entry["secretKey"]: entry["remoteRef"]["key"] for entry in secret["spec"]["data"]}
    assert refs == {"DATABASE_URL": "/k8s/eks-demo/agent-platform/database-url",
                    "IDENTITY_CLIENT_SECRET_CONSOLE": "/k8s/eks-demo/agentops-identity/client-secret-console"}


def test_identity_signs_in_through_dex_and_issues_every_client():
    config = resource("agentops", "eks", "ConfigMap", "agentops-identity")["data"]
    assert config["AGENTOPS_ROLE"] == "identity"
    assert config["AUTH_BASE_URL"] == "https://agentops.agrt.dev"
    assert config["OIDC_ISSUER"] == "https://sso.demo.opsp.dev"
    hosts = yaml.safe_load(config["GIT_HOSTS"])
    assert {host["name"] for host in hosts} == {"github", "gitlab", "gitlab-demo", "gitea-demo"}
    assert all(set(host) == {"name", "provider", "origin", "clientId"} for host in hosts)

    secret = resource("agentops", "eks", "ExternalSecret", "agentops-identity-external")
    refs = {entry["secretKey"]: entry["remoteRef"]["key"] for entry in secret["spec"]["data"]}
    for client in ("console", "studio", "memory", "knowledge", "forge", "runway", "models"):
        assert refs[f"IDENTITY_CLIENT_SECRET_{client.upper()}"] == f"/k8s/eks-demo/agentops-identity/client-secret-{client}"
    assert refs["OIDC_CLIENT_SECRET"] == "/k8s/eks-demo/agentops-identity/oidc-client-secret"
    assert refs["IDENTITY_ORGANIZATION_ID"] == "/k8s/eks-demo/agentops-identity/organization-id"

    deployment = resource("agentops", "eks", "Deployment", "agentops-identity")
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    console = resource("agentops", "eks", "Deployment", "agentops")["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == console


def test_services_reach_identity():
    policy = resource("agentops", "eks", "NetworkPolicy", "agentops")
    sources = {peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
               for rule in policy["spec"]["ingress"] for peer in rule["from"] if "namespaceSelector" in peer}
    assert {"agent-studio", "agent-models", "agent-memory", "agent-knowledge", "agent-forge", "agent-runway"} <= sources


@pytest.mark.parametrize("mode,command", [
    ("count", ["node", "scripts/import-identity.ts"]),
    ("apply", ["node", "scripts/import-identity.ts", "--apply"]),
])
def test_import_runs_after_sync_in_the_chosen_mode(mode, command):
    docs = manifests_with(mode)
    job = find(docs, "Job", "agentops-identity-import")
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PostSync"
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == command
    assert job["spec"]["backoffLimit"] == 0
    assert find(docs, "ExternalSecret", "agentops-identity-import")


def test_import_off_renders_no_job():
    docs = manifests_with("off")
    assert find(docs, "Job", "agentops-identity-import") is None
    assert find(docs, "ExternalSecret", "agentops-identity-import") is None

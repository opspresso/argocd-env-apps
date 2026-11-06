"""Shared chart composition must preserve workload identity and version coupling."""

from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def render(chart, platform="eks", *settings):
    path = ROOT / "charts" / chart
    phase = "prod" if platform == "eks" else "alpha"
    command = ["helm", "template", "custom-release", str(path), "-n", "custom-namespace",
               "-f", str(path / f"values-{phase}.yaml"),
               "-f", str(path / platform / f"values-{platform}-demo.yaml")]
    for setting in settings:
        command += ["--set", setting]
    output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    docs = [doc for doc in yaml.safe_load_all(output) if doc]
    identities = [(doc["kind"], doc["metadata"].get("namespace", "custom-namespace"),
                   doc["metadata"]["name"]) for doc in docs]
    assert len(identities) == len(set(identities)), "duplicate resource identity"
    return docs


@pytest.mark.parametrize("platform", ["eks"])
def test_studio_runs_only_web_and_ticker_with_stable_identity(platform):
    docs = render("agent-studio", platform, "app.image.tag=v9.8.7")
    deployments = {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "Deployment"}
    # Audio and Workspace execution belong to Knowledge and Forge; Studio keeps no worker process.
    assert set(deployments) == {"agent-studio", "agent-studio-ticker"}
    for name, deployment in deployments.items():
        selector = {"app.kubernetes.io/name": name, "app.kubernetes.io/instance": "custom-release"}
        assert deployment["spec"]["selector"]["matchLabels"] == selector
        assert selector.items() <= deployment["spec"]["template"]["metadata"]["labels"].items()
    web = deployments["agent-studio"]["spec"]["template"]["spec"]["containers"][0]
    assert web["image"].endswith(":v9.8.7")
    assert web["envFrom"] == [{"configMapRef": {"name": "agent-studio"}},
                              {"secretRef": {"name": "agent-studio-external"}}]
    assert not any(doc["kind"] in {"Job", "PersistentVolumeClaim", "Namespace"} for doc in docs)


@pytest.mark.parametrize("backend", ["docker", "kubernetes"])
def test_retired_settings_cannot_restore_a_studio_executor(backend):
    docs = render("agent-studio", "eks", f"app.workloads.workspaceWorker.backend={backend}",
                  "app.workloads.workspaceWorker.legacyDocker.enabled=true", "app.legacyExecution.preserve=true")
    containers = [container for doc in docs if doc["kind"] == "Deployment"
                  for container in doc["spec"]["template"]["spec"]["containers"]]
    assert not any(container["name"] == "docker" or container.get("securityContext", {}).get("privileged") for container in containers)
    assert not any(doc["kind"] in {"RoleBinding", "PersistentVolumeClaim", "Namespace"} for doc in docs)


def test_config_changes_restart_studio_web():
    def checksum(value):
        docs = render("agent-studio", "eks", f"app.configmap.data.FORGE_URL={value}")
        web = next(doc for doc in docs if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "agent-studio")
        return web["spec"]["template"]["metadata"]["annotations"]["checksum/config"]

    assert checksum("http://forge-before") != checksum("http://forge-after")


def test_argocd_network_policy_can_be_disabled():
    assert any(doc["kind"] == "NetworkPolicy" for doc in render("mcp-argocd"))
    assert not any(doc["kind"] == "NetworkPolicy" for doc in render("mcp-argocd", "eks", "networkPolicy.enabled=false"))


def test_external_secret_entries_restart_knowledge_web_and_worker():
    def checksums(*settings):
        docs = render("agent-knowledge", "eks", *settings)
        return {doc["metadata"]["name"]: doc["spec"]["template"]["metadata"]["annotations"]["checksum/external-secrets"]
                for doc in docs if doc["kind"] == "Deployment"}

    before = checksums()
    assert set(before) == {"agent-knowledge", "agent-knowledge-worker"}
    # The worker reads the web app's ExternalSecret, so both follow the same entry list.
    assert len(set(before.values())) == 1
    after = checksums("app.externalSecrets.data[0].key=/k8s/other/database-url")
    assert all(after[name] != before[name] for name in before)
    # dataFrom counts as well.
    source = "app.externalSecrets.dataFrom[0].find.name.regexp=.*"
    before_from = checksums(source, "app.externalSecrets.dataFrom[0].find.path=/k8s/before")
    after_from = checksums(source, "app.externalSecrets.dataFrom[0].find.path=/k8s/after")
    assert len(set(before_from.values())) == len(set(after_from.values())) == 1
    assert all(after_from[name] != before_from[name] for name in before)

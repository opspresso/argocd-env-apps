"""Render the router's public, database and service-to-service deployment boundaries."""
import pytest
import yaml

from test_agent_platform import ROOT, manifests, resource
from test_agent_memory_maintenance import render


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_models_readiness_native_identity_and_consumer_network(platform):
    config = resource("agent-models", platform, "ConfigMap", "agent-models")["data"]
    assert config["AUTH_BASE_URL"] == "https://models.agrt.dev"
    assert config["OIDC_ISSUER"] == "https://studio.agrt.dev/api/auth"
    assert config["OIDC_PROVIDER_ID"] == "studio"
    deployment = resource("agent-models", platform, "Deployment", "agent-models")["spec"]
    pod = deployment["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    container = pod["containers"][0]
    assert container["readinessProbe"]["httpGet"] == {"path": "/api/ready", "port": 3400}
    assert container["livenessProbe"]["httpGet"] == {"path": "/api/health", "port": 3400}
    assert container["startupProbe"]["httpGet"] == container["livenessProbe"]["httpGet"]
    assert pod["terminationGracePeriodSeconds"] > int(config["MODEL_REQUEST_TIMEOUT_MS"]) // 1000 + 15
    refs = resource("agent-models", platform, "ExternalSecret", "agent-models-external")["spec"]["data"]
    refs = {entry["secretKey"]: entry["remoteRef"]["key"] for entry in refs}
    assert refs == {
        "DATABASE_URL": f"/k8s/{platform}-demo/agent-platform/database-url",
        "AUTH_SECRET": f"/k8s/{platform}-demo/agent-models/auth-secret",
        "AES_ENCRYPTION_KEY": f"/k8s/{platform}-demo/agent-models/encryption-key",
        "OIDC_CLIENT_ID": f"/k8s/{platform}-demo/agent-models/oidc-client-id",
        "OIDC_CLIENT_SECRET": f"/k8s/{platform}-demo/agent-models/oidc-client-secret",
    }
    ingress = resource("agent-models", platform, "NetworkPolicy", "agent-models")["spec"]["ingress"][0]
    assert ingress["ports"] == [{"protocol": "TCP", "port": 3400}]
    peers = {peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] for peer in ingress["from"] if "namespaceSelector" in peer}
    assert peers == {"agent-studio", "agent-memory", "agent-librio", "agent-forge", "kube-system" if platform == "k3s" else "istio-system"}
    assert not any(doc["kind"] == "PersistentVolumeClaim" for doc in manifests("agent-models", platform))
    assert resource("agent-models", platform, "HTTPRoute", "agent-models")["spec"]["hostnames"] == ["models.agrt.dev"]


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_models_maintenance_stops_dispatch_without_removing_configuration(platform, tmp_path):
    env = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
    env["agent_models_maintenance"] = True
    docs = render("agent-models", env, tmp_path)
    assert next(doc for doc in docs if doc["kind"] == "Deployment")["spec"]["replicas"] == 0
    assert not any(doc["kind"] == "HorizontalPodAutoscaler" for doc in docs)
    assert any(doc["kind"] == "ExternalSecret" and doc["metadata"]["name"] == "agent-models-external" for doc in docs)

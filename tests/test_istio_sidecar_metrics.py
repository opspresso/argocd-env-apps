"""Prometheus must reach the Envoy stats of every injected sidecar that a NetworkPolicy isolates."""

from pathlib import Path

import pytest
import yaml

from test_agent_platform import manifests


ROOT = Path(__file__).resolve().parents[1]
DEPLOYED = sorted(yaml.safe_load(path.read_text())["spec"]["template"]["spec"]["source"]["path"].split("/")[-1]
                  for path in (ROOT / "apps/eks").glob("*.yaml"))
POD_OWNERS = {"Deployment", "StatefulSet", "DaemonSet"}
ENVOY_STATS_PORT = 15090  # PodMonitor istio-system/istio-sidecars, port http-envoy-prom


def selects(policy, labels):
    selector = policy["spec"]["podSelector"]
    assert set(selector) <= {"matchLabels"}, "only matchLabels selectors are evaluated here"
    return selector.get("matchLabels", {}).items() <= labels.items()


def admits_prometheus(rule):
    peers = rule.get("from", [])
    ports = rule.get("ports", [])
    from_prometheus = not peers or any(
        peer.get("namespaceSelector", {}).get("matchLabels") == {"kubernetes.io/metadata.name": "addon-prometheus"}
        and "podSelector" not in peer for peer in peers)
    return from_prometheus and (not ports or {"protocol": "TCP", "port": ENVOY_STATS_PORT} in ports)


@pytest.mark.parametrize("chart", DEPLOYED)
def test_isolated_sidecars_admit_prometheus_on_envoy_stats(chart):
    docs = manifests(chart, "eks")
    policies = [doc for doc in docs if doc["kind"] == "NetworkPolicy" and "Ingress" in doc["spec"]["policyTypes"]]
    injected = [doc for doc in docs if doc["kind"] in POD_OWNERS
                and doc["spec"]["template"]["metadata"].get("labels", {}).get("sidecar.istio.io/inject") == "true"]
    for workload in injected:
        namespace = workload["metadata"].get("namespace", "default")
        labels = workload["spec"]["template"]["metadata"]["labels"]
        applied = [policy for policy in policies
                   if policy["metadata"].get("namespace", "default") == namespace and selects(policy, labels)]
        if applied:
            rules = [rule for policy in applied for rule in policy["spec"].get("ingress", [])]
            assert any(admits_prometheus(rule) for rule in rules), workload["metadata"]["name"]

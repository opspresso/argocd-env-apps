"""Rendered isolation and migration contracts shared by alpha and prod."""
from pathlib import Path
from ipaddress import ip_network
import subprocess

import pytest
import yaml
from running_environment import running_values

ROOT = Path(__file__).resolve().parents[1]


def render(platform, *overrides, **changes):
    chart = ROOT / "charts/agent-forge"
    phase = "prod" if platform == "eks" else "alpha"
    result = subprocess.run(["helm", "template", "agent-forge-eks-demo", str(chart), "--namespace", "agent-forge",
                             "-f", str(chart / "values.yaml"), "-f", str(chart / f"values-{phase}.yaml"),
                             "-f", "-", *overrides],
                            input=running_values("agent-forge", platform, **changes), capture_output=True, text=True, check=True)
    return [item for item in yaml.safe_load_all(result.stdout) if item]


@pytest.mark.parametrize("platform,max_pods", [("eks", "32")])
def test_forge_isolation_permissions_and_total_capacity(platform, max_pods):
    docs = render(platform)
    namespace = "agent-forge-workspaces"
    scoped = {item["kind"] + "/" + item["metadata"]["name"]: item for item in docs if item["metadata"].get("namespace") == namespace}
    assert scoped["Role/agent-forge-sandbox"]["rules"] == [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
        {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["get", "list", "create", "delete"]},
        {"apiGroups": [""], "resources": ["pods/exec"], "verbs": ["get", "create"]},
    ]
    assert scoped["RoleBinding/agent-forge-sandbox"]["subjects"] == [{"kind": "ServiceAccount", "name": "agent-forge", "namespace": "agent-forge"}]
    assert scoped["ServiceAccount/sandbox"]["automountServiceAccountToken"] is False
    quota = scoped["ResourceQuota/agent-forge-sandbox"]["spec"]["hard"]
    assert quota["pods"] == quota["count/jobs.batch"] == max_pods
    if platform == "eks":
        assert int(quota["requests.ephemeral-storage"][:-2]) == (2048 * 2 + 512) * int(max_pods)
    else:
        assert quota == {"pods": max_pods, "count/jobs.batch": max_pods}
    assert scoped["NetworkPolicy/sandbox-isolation"]["spec"] == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []}
    egress = scoped["NetworkPolicy/sandbox-egress"]["spec"]["egress"]
    assert egress[0]["ports"] == [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]
    assert {"169.254.0.0/16", "10.0.0.0/8", "100.64.0.0/10"} <= set(egress[1]["to"][0]["ipBlock"]["except"])
    for rule in egress:
        for destination in rule["to"]:
            if "ipBlock" in destination:
                block = destination["ipBlock"]
                assert all(ip_network(cidr).version == 4 for cidr in [block["cidr"], *block.get("except", [])])
    assert scoped["ExternalSecret/ecr-registry"]["spec"]["target"]["name"] == "ecr-registry"
    assert not any(item["kind"] == "ClusterRoleBinding" for item in docs)


@pytest.mark.parametrize("platform", ["eks"])
def test_sandboxes_reach_forge_only_on_the_sandbox_port(platform):
    # values-template.yaml.j2 writes the sandbox settings as literals; they must match the Service the chart renders.
    docs = render(platform)
    service = next(item for item in docs if item["kind"] == "Service" and item["metadata"]["name"] == "agent-forge")
    ports = {port["name"]: port for port in service["spec"]["ports"]}
    container = next(item for item in docs if item["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    container_port = next(port["containerPort"] for port in container["ports"] if port["name"] == ports["sandbox"]["targetPort"])
    assert ports["sandbox"]["port"] == container_port != ports["http"]["port"]
    config = next(item for item in docs if item["kind"] == "ConfigMap" and item["metadata"]["name"] == "agent-forge")
    assert config["data"]["FORGE_SANDBOX_PORT"] == str(container_port)
    assert config["data"]["FORGE_SANDBOX_API_URL"] == f"http://agent-forge.agent-forge.svc.cluster.local:{ports['sandbox']['port']}"
    assert config["data"]["FORGE_INSTANCE"] == service["spec"]["selector"]["app.kubernetes.io/instance"]
    egress = next(item for item in docs if item["kind"] == "NetworkPolicy" and item["metadata"]["name"] == "sandbox-egress")
    expected = {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "agent-forge"}}, "podSelector": {"matchLabels": service["spec"]["selector"]}}], "ports": [{"protocol": "TCP", "port": container_port}]}
    assert expected in egress["spec"]["egress"]
    # Forge admits the sandboxes on the sandbox port only.
    ingress = next(item for item in docs if item["kind"] == "NetworkPolicy" and item["metadata"]["name"] == "agent-forge")["spec"]["ingress"]
    sandbox = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "agent-forge-workspaces"}},
               "podSelector": {"matchLabels": {"app.kubernetes.io/name": "agent-forge-sandbox"}}}
    for rule in ingress:
        reaches = any(peer == sandbox or peer == {"namespaceSelector": sandbox["namespaceSelector"]} for peer in rule["from"])
        assert not reaches or rule["ports"] == [{"protocol": "TCP", "port": container_port}]
    assert any(sandbox in rule["from"] for rule in ingress)


@pytest.mark.parametrize("overrides,changes", [
    ((), {"workspace_memory_mb": 4096}), ((), {"workspace_cpus": 2}), ((), {"resources": False}),
    (("--set", "app.image.tag=v0.9.0"), {}),
])
def test_sandbox_configuration_restarts_controller(overrides, changes):
    # The sandbox settings and image are ConfigMap values, so checksum/config restarts the controller on a change.
    def checksum(docs):
        pod = next(item for item in docs if item["kind"] == "Deployment")["spec"]["template"]
        return pod["metadata"]["annotations"]["checksum/config"]
    assert checksum(render("eks")) != checksum(render("eks", *overrides, **changes))


@pytest.mark.parametrize("platform", ["eks"])
def test_studio_has_no_execution_resources(platform):
    from test_agent_platform import manifests
    docs = manifests("agent-studio", platform)
    assert not any(item["kind"] in {"PersistentVolumeClaim", "Namespace", "Role", "RoleBinding", "Job"} for item in docs)
    assert not any(item["metadata"].get("namespace") == "agent-studio-workspaces" for item in docs)
    deployments = {item["metadata"]["name"] for item in docs if item["kind"] == "Deployment"}
    assert deployments == {"agent-studio", "agent-studio-ticker"}

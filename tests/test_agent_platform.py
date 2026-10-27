"""Deployment contracts between the applications and their cluster dependencies."""

from functools import lru_cache
from pathlib import Path
import subprocess

import pytest
import yaml

from workload_policy import policy_errors
from running_environment import running_values


ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def manifests(chart, platform, image_tag=None):
    path = ROOT / "charts" / chart
    definition = yaml.safe_load((path / "Chart.yaml").read_text())
    if definition.get("dependencies") and not list((path / "charts").glob("*.tgz")):
        pytest.skip(f"Run helm dependency update {path}")
    args = ["helm", "template", f"{chart}-{platform}-demo", str(path),
            "--api-versions", "monitoring.coreos.com/v1", "-f", str(path / "values.yaml")]
    phase = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())["phase"]
    if (path / "values-alpha.yaml").exists():
        args += ["-f", str(path / f"values-{phase}.yaml")]
    args += ["-f", "-"]
    if image_tag:
        args += ["--set-string", f"app.image.tag={image_tag}"]
    result = subprocess.run(args, input=running_values(chart, platform), capture_output=True, text=True, check=True)
    assert not policy_errors(result.stdout, platform)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def resource(chart, platform, kind, name, **overrides):
    return next(doc for doc in manifests(chart, platform, **overrides)
                if doc["kind"] == kind and doc["metadata"]["name"] == name)


@pytest.mark.parametrize("platform", ["eks", "k3s"])
@pytest.mark.parametrize("chart", ["agent-studio", "agent-memory", "agent-librio", "agent-forge"])
def test_application_credentials_match_cluster_dependencies(chart, platform):
    secret = resource(chart, platform, "ExternalSecret", f"{chart}-external")
    refs = {entry["secretKey"]: entry["remoteRef"]["key"] for entry in secret["spec"]["data"]}
    assert refs["DATABASE_URL"] == f"/k8s/{platform}-demo/agent-platform/database-url"
    config = resource(chart, platform, "ConfigMap", chart)["data"]
    assert not any("BEDROCK" in key or "DYNAMO" in key or "NEO4J" in key for key in config)
    if chart in {"agent-studio", "agent-librio"}:
        if platform == "eks":
            assert "S3_ENDPOINT" not in config and "S3_ACCESS_KEY_ID" not in refs
            assert config["S3_BUCKET_NAME"] == f"{chart}-static"
        else:
            assert config["S3_ENDPOINT"] == "http://minio.agent-studio.svc.cluster.local:9000"
            assert refs["S3_ACCESS_KEY_ID"] == "/k8s/k3s-demo/agent-studio/minio-root-user"
    else:
        assert not any(key.startswith("S3_") for key in config | refs)
    assert not any("WORKSPACE_PROVIDER" in key or "DOCKER_HOST" in key for key in config)
    if chart == "agent-memory":
        assert "DOCUMENT_WORKER_ENABLED" not in config
        assert not any(doc["kind"] == "Deployment" and doc["metadata"]["name"] == "agent-memory-worker"
                       for doc in manifests(chart, platform))


@pytest.mark.parametrize("platform", ["eks", "k3s"])
@pytest.mark.parametrize("chart", ["agent-studio", "agent-memory", "agent-librio", "agent-forge"])
def test_authentication_hosts_match_public_routes(chart, platform):
    config = resource(chart, platform, "ConfigMap", chart)["data"]
    route = resource(chart, platform, "HTTPRoute", chart)["spec"]
    hostname = chart.removeprefix("agent-") + ".agrt.dev"
    assert route["hostnames"] == [hostname]
    if chart == "agent-forge":
        assert config["AUTH_BASE_URL"] == f"https://{hostname}"
        assert config["ALLOWED_ORIGINS"] == f"https://{hostname}"
    else:
        assert config["AUTH_ALLOWED_HOSTS"] == hostname
        assert config["AUTH_BASE_URL"] == f"https://{hostname}"


@pytest.mark.parametrize("platform", ["eks", "k3s"])
@pytest.mark.parametrize("chart", ["agent-memory", "agent-librio", "agent-forge"])
def test_metrics_auth_and_dependency_independent_liveness(chart, platform):
    service = resource(chart, platform, "Service", chart)
    deployment = resource(chart, platform, "Deployment", chart)
    assert service["spec"]["selector"].items() <= deployment["spec"]["template"]["metadata"]["labels"].items()
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    forge = chart == "agent-forge"
    assert container["readinessProbe"]["httpGet"]["path"] == ("/health" if forge else "/api/health")
    assert container["livenessProbe"]["httpGet"]["path"] == ("/" if forge else "/api/live")
    assert container["startupProbe"]["httpGet"] == container["livenessProbe"]["httpGet"]
    secret_name, secret_key = ("agent-forge-metrics", "token") if forge else (f"{chart}-external", "METRICS_BEARER_TOKEN")
    secret = resource(chart, platform, "ExternalSecret", secret_name)
    refs = {entry["secretKey"]: entry["remoteRef"]["key"] for entry in secret["spec"]["data"]}
    assert refs[secret_key] == f"/k8s/{platform}-demo/{chart}/metrics-bearer-token"
    if forge:
        # The scraper's bearer and Forge's accepted token come from the same parameter.
        app = resource(chart, platform, "ExternalSecret", f"{chart}-external")["spec"]["data"]
        assert {entry["secretKey"]: entry["remoteRef"]["key"] for entry in app}["METRICS_BEARER_TOKEN"] == refs[secret_key]
    if platform == "eks":
        monitor = resource(chart, platform, "ServiceMonitor", chart)["spec"]
        assert monitor["endpoints"][0]["authorization"]["credentials"] == {"name": secret_name, "key": secret_key}
        assert monitor["selector"]["matchLabels"].items() <= service["metadata"]["labels"].items()
    else:
        assert not any(item["kind"] == "ServiceMonitor" for item in manifests(chart, platform))


MCP_PORTS = {"mcp-argocd": 3000, "mcp-cloudwatch": 8000, "mcp-grafana": 8000, "mcp-kubernetes": 8080}


@pytest.mark.parametrize("chart", sorted(MCP_PORTS))
@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_mcp_accepts_only_trusted_agent_execution_processes(chart, platform):
    policy = resource(chart, platform, "NetworkPolicy", chart)["spec"]
    assert policy["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": chart}
    assert policy["ingress"][0]["ports"] == [{"protocol": "TCP", "port": MCP_PORTS[chart]}]
    source = policy["ingress"][0]["from"][0]
    assert source["namespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": "agent-studio"}
    expression = source["podSelector"]["matchExpressions"][0]
    expected = {doc["metadata"]["name"] for doc in manifests("agent-studio", platform)
                if doc["kind"] == "Deployment" and doc["spec"]["template"]["spec"].get("serviceAccountName") == "agent-studio"}
    assert set(expression["values"]) == expected


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_librio_consumer_is_independent_of_web_and_shares_metrics_auth(platform):
    worker = resource("agent-librio", platform, "Deployment", "agent-librio-worker")
    web = resource("agent-librio", platform, "Deployment", "agent-librio")
    assert worker["spec"]["replicas"] == 1
    pod = worker["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["command"] == ["node", "build/worker.cjs"]
    assert container["image"] == web["spec"]["template"]["spec"]["containers"][0]["image"]
    assert pod["terminationGracePeriodSeconds"] > 30
    assert pod["serviceAccountName"] == "agent-librio"
    assert container["envFrom"] == [{"configMapRef": {"name": "agent-librio"}}, {"secretRef": {"name": "agent-librio-external"}}]
    env = {entry["name"]: entry["value"] for entry in container["env"]}
    assert env["AUDIO_WORKER_ENABLED"] == env["DOCUMENT_WORKER_ENABLED"] == "true"
    config = resource("agent-librio", platform, "ConfigMap", "agent-librio")["data"]
    assert config["AUDIO_WORKER_ENABLED"] == config["DOCUMENT_WORKER_ENABLED"] == "false"
    service = resource("agent-librio", platform, "Service", "agent-librio-worker")
    assert service["spec"]["selector"] == worker["spec"]["selector"]["matchLabels"]
    assert resource("agent-librio", platform, "Service", "agent-librio")["spec"]["selector"] != service["spec"]["selector"]
    if platform == "eks":
        monitor = resource("agent-librio", platform, "ServiceMonitor", "agent-librio")
        assert monitor["spec"]["selector"]["matchLabels"].items() <= service["metadata"]["labels"].items()
    else:
        assert not container.get("resources")


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_studio_has_no_execution_or_cluster_authority(platform):
    config = resource("agent-studio", platform, "ConfigMap", "agent-studio")["data"]
    pod = resource("agent-studio", platform, "Deployment", "agent-studio")["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert len(pod["containers"]) == 1 and not pod.get("volumes")
    assert not any(name.startswith(("WORKSPACE_", "AUDIO_")) and name.endswith("_CONCURRENCY") for name in config)
    assert config["FORGE_URL"] == "http://agent-forge.agent-forge.svc.cluster.local:3103"
    assert config["MEMORY_URL"] == "http://agent-memory.agent-memory.svc.cluster.local:3000"
    assert config["LIBRIO_URL"] == "http://agent-librio.agent-librio.svc.cluster.local:3000"
    assert config["MODELS_URL"] == "http://agent-models.agent-models.svc.cluster.local:3400"
    assert config["MODELS_PUBLIC_URL"] == "https://models.agrt.dev"
    assert not any(name.startswith("LLM_PROVIDER_") or "_DELEGATION_" in name for name in config)
    refs = {entry["secretKey"]: entry["remoteRef"]["key"]
            for entry in resource("agent-studio", platform, "ExternalSecret", "agent-studio-external")["spec"]["data"]}
    # Studio and each service read the same registered client ID.
    for service in ("forge", "memory", "librio"):
        assert refs[f"{service.upper()}_OIDC_CLIENT_ID"] == f"/k8s/{platform}-demo/agent-{service}/oidc-client-id"
    assert not any(name.startswith("LLM_PROVIDER_") or name.endswith(("_DELEGATION_SECRET", "_POLICY_SECRET")) for name in refs)
    assert not any(doc["kind"] in {"Role", "RoleBinding", "Job"} for doc in manifests("agent-studio", platform))
    assert resource("agent-studio", platform, "Deployment", "agent-studio-ticker")["spec"]["replicas"] == 1


@pytest.mark.parametrize("platform", ["eks", "k3s"])
@pytest.mark.parametrize("tag", ["v0.1.0", "v0.2.3"])
def test_forge_and_sandbox_use_exactly_the_same_release(platform, tag):
    documents = manifests("agent-forge", platform, tag)
    config = next(doc["data"] for doc in documents if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "agent-forge-sandbox")
    deployment = next(doc for doc in documents if doc["kind"] == "Deployment")
    repository, image_tag = deployment["spec"]["template"]["spec"]["containers"][0]["image"].rsplit(":", 1)
    assert image_tag == tag
    assert config["FORGE_SANDBOX_IMAGE"] == f"{repository}:sandbox-{tag}"
    assert len([doc for doc in documents if doc["kind"] == "Deployment"]) == 1


@pytest.mark.parametrize("platform", ["eks", "k3s"])
@pytest.mark.parametrize("chart", ["agent-studio", "agent-memory", "agent-librio", "agent-forge"])
def test_namespace_registry_refresh_and_explicit_ingress(chart, platform):
    registry = resource(chart, platform, "ExternalSecret", "ecr-registry")["spec"]
    assert registry["refreshInterval"] == "1h"
    assert registry["dataFrom"][0]["sourceRef"]["generatorRef"]["kind"] == "ECRAuthorizationToken"
    policy = resource(chart, platform, "NetworkPolicy", chart)["spec"]
    peers = {item["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"] for item in policy["ingress"][0]["from"] if "namespaceSelector" in item}
    assert "agent-studio" in peers
    assert ("kube-system" if platform == "k3s" else "istio-system") in peers
    if platform == "k3s":
        gateway = [peer for peer in policy["ingress"][0]["from"]
                   if peer.get("namespaceSelector", {}).get("matchLabels") == {"kubernetes.io/metadata.name": "kube-system"}]
        assert gateway == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "traefik", "app.kubernetes.io/instance": "traefik-kube-system"}}}]
        assert "traefik-gateway" not in peers
    if chart == "agent-studio":
        assert {"agent-forge", "agent-librio"} <= peers
    assert "agent-forge-workspaces" in peers if chart == "agent-forge" else "agent-forge-workspaces" not in peers


@pytest.mark.parametrize("chart,name", [("postgresql", "postgres")])
def test_eks_databases_keep_the_existing_persistent_storage(chart, name):
    application = yaml.safe_load((ROOT / "apps/eks" / f"{chart}.yaml").read_text())
    source = application["spec"]["template"]["spec"]["source"]
    assert source["path"] == f"charts/{chart}"
    workload = resource(chart, "eks", "StatefulSet", name)["spec"]
    assert workload["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] == "20Gi"
    assert workload["template"]["spec"]["containers"][0]["resources"]["requests"]


@pytest.mark.parametrize("platform", ["eks", "k3s"])
@pytest.mark.parametrize("chart", ["agent-librio", "agent-forge"])
@pytest.mark.parametrize("maintenance", [False, True])
def test_new_service_maintenance_stops_only_its_processes(platform, chart, maintenance, tmp_path):
    from test_agent_memory_maintenance import render
    environment = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
    environment[chart.replace("-", "_") + "_maintenance"] = maintenance
    docs = render(chart, environment, tmp_path)
    deployments = [doc for doc in docs if doc["kind"] == "Deployment"]
    expected = {chart, "agent-librio-worker"} if chart == "agent-librio" else {chart}
    assert {doc["metadata"]["name"] for doc in deployments} == expected
    if maintenance:
        assert all(doc["spec"]["replicas"] == 0 for doc in deployments)
        assert not any(doc["kind"] == "HorizontalPodAutoscaler" for doc in docs)
    if chart == "agent-forge":
        assert any(doc["kind"] == "Namespace" and doc["metadata"]["name"] == "agent-forge-workspaces" for doc in docs)


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_librio_pool_preserves_provider_slots_and_cleanup_capacity(platform):
    config = resource("agent-librio", platform, "ConfigMap", "agent-librio")["data"]
    assert int(config["AI_ADMISSION_POOL_SIZE"]) >= int(config["AI_PROVIDER_MAX_CONCURRENCY"]) + 1
    assert 0 < int(config["AI_BATCH_MAX_CONCURRENCY"]) < int(config["AI_PROVIDER_MAX_CONCURRENCY"])


@pytest.mark.parametrize("platform", ["eks", "k3s"])
@pytest.mark.parametrize("chart,port", [("agent-studio", 3000), ("agent-memory", 3000), ("agent-librio", 3000), ("agent-forge", 3103)])
def test_agent_service_route_and_pod_share_the_same_port(chart, port, platform):
    service = resource(chart, platform, "Service", chart)["spec"]
    deployment = resource(chart, platform, "Deployment", chart)["spec"]["template"]["spec"]
    route = resource(chart, platform, "HTTPRoute", chart)["spec"]
    service_port = service["ports"][0]
    container_ports = {item["name"]: item["containerPort"] for item in deployment["containers"][0]["ports"]}
    target = service_port["targetPort"]
    resolved_target = container_ports[target] if isinstance(target, str) else target
    assert service_port["port"] == resolved_target == port
    assert service_port["name"] in container_ports
    assert all(backend["port"] == port for rule in route["rules"] for backend in rule.get("backendRefs", []))
    if chart == "agent-librio":
        worker = resource(chart, platform, "Service", "agent-librio-worker")["spec"]["ports"][0]
        assert worker["port"] == worker["targetPort"] == port
    if chart == "agent-studio":
        ticker = resource(chart, platform, "Deployment", "agent-studio-ticker")["spec"]["template"]["spec"]["containers"][0]
        assert "http://agent-studio:3000" in ticker["command"][-1]


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_mcp_servers_are_read_only(platform):
    kubernetes = resource("mcp-kubernetes", platform, "Deployment", "mcp-kubernetes")
    assert "--read-only" in kubernetes["spec"]["template"]["spec"]["containers"][0]["args"]
    roles = [doc for doc in manifests("mcp-kubernetes", platform) if doc["kind"] in ("ClusterRole", "Role")]
    assert roles
    verbs = {verb for role in roles for rule in role["rules"] for verb in rule["verbs"]}
    assert verbs == {"get", "list", "watch"}

    grafana = resource("mcp-grafana", platform, "Deployment", "mcp-grafana")
    args = grafana["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--disable-write" in args and "--enable-query" not in args

    argocd = resource("mcp-argocd", platform, "ConfigMap", "mcp-argocd")
    assert argocd["data"]["MCP_READ_ONLY"] == "true"


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_studio_metrics_require_the_shared_bearer(platform):
    secret = resource("agent-studio", platform, "ExternalSecret", "agent-studio-external")
    refs = {entry["secretKey"]: entry["remoteRef"]["key"] for entry in secret["spec"]["data"]}
    assert refs["METRICS_BEARER_TOKEN"] == f"/k8s/{platform}-demo/agent-studio/metrics-bearer-token"
    assert "MEMORY_ORGANIZATION_ID" not in refs and "LIBRIO_ORGANIZATION_ID" not in refs
    if platform == "eks":
        monitor = resource("agent-studio", platform, "ServiceMonitor", "agent-studio")["spec"]
        assert monitor["endpoints"][0]["authorization"]["credentials"] == {
            "name": "agent-studio-external", "key": "METRICS_BEARER_TOKEN"}

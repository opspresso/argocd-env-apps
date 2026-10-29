"""Rendered contracts of rovr: sandbox isolation, the internal listener, secrets and its database."""
from ipaddress import ip_network
from pathlib import Path
import subprocess

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import pytest
import yaml

from running_environment import running_values
from workload_policy import policy_errors

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/rovr"


def render(*overrides, cluster="eks-demo"):
    if not list((CHART / "charts").glob("*.tgz")):
        pytest.skip(f"Run helm dependency update {CHART}")
    if cluster == "eks-demo":
        values = running_values("rovr", "eks")
    else:
        env = yaml.safe_load((ROOT / "env" / f"{cluster}.yaml").read_text())
        jinja = Environment(loader=FileSystemLoader(CHART), undefined=StrictUndefined)
        values = jinja.get_template("values-template.yaml.j2").render(env)
    result = subprocess.run(["helm", "template", "rovr", str(CHART), "--namespace", "rovr",
                             "--api-versions", "monitoring.coreos.com/v1",
                             "-f", str(CHART / "values.yaml"), "-f", str(CHART / "values-prod.yaml"), "-f", "-", *overrides],
                            input=values, capture_output=True, text=True, check=True)
    assert not policy_errors(result.stdout, "eks")
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def find(docs, kind, name, namespace=None):
    return next(item for item in docs if item["kind"] == kind and item["metadata"]["name"] == name
                and (namespace is None or item["metadata"].get("namespace") == namespace))


def test_sandbox_namespace_permissions_and_capacity():
    docs = render()
    ns = "rovr-sandboxes"
    namespace = find(docs, "Namespace", ns)
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    assert "Prune=false" in namespace["metadata"]["annotations"]["argocd.argoproj.io/sync-options"]
    assert find(docs, "ServiceAccount", "sandbox", ns)["automountServiceAccountToken"] is False
    assert find(docs, "Role", "rovr-sandbox", ns)["rules"] == [
        {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["get", "list", "create", "delete"]},
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
        {"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]},
        {"apiGroups": [""], "resources": ["persistentvolumeclaims"], "verbs": ["list", "create", "delete"]},
    ]
    assert find(docs, "RoleBinding", "rovr-sandbox", ns)["subjects"] == [{"kind": "ServiceAccount", "name": "rovr", "namespace": "rovr"}]
    assert not any(item["kind"] in {"ClusterRole", "ClusterRoleBinding"} for item in docs)
    quota = find(docs, "ResourceQuota", "rovr-sandbox", ns)["spec"]["hard"]
    assert quota == {
        "pods": "6", "count/jobs.batch": "6", "persistentvolumeclaims": "24", "requests.storage": "240Gi",
        "requests.cpu": "12", "limits.cpu": "12", "requests.memory": "24Gi", "limits.memory": "24Gi",
        "requests.ephemeral-storage": "48Gi", "limits.ephemeral-storage": "48Gi",
    }


def test_sandboxes_reach_only_dns_public_https_and_the_internal_listener():
    docs = render()
    ns = "rovr-sandboxes"
    assert find(docs, "NetworkPolicy", "sandbox-isolation", ns)["spec"] == {
        "podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []}
    policy = find(docs, "NetworkPolicy", "sandbox-egress", ns)["spec"]
    assert policy["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "rovr-sandbox"}}
    dns, public, server = policy["egress"]
    assert {"ipBlock": {"cidr": "172.20.0.10/32"}} in dns["to"]
    assert {"169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"} <= set(public["to"][0]["ipBlock"]["except"])
    assert all(ip_network(c).version == 4 for c in public["to"][0]["ipBlock"]["except"])
    assert public["ports"] == [{"protocol": "TCP", "port": 80}, {"protocol": "TCP", "port": 443}]
    service = find(docs, "Service", "rovr")
    assert server == {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "rovr"}},
                              "podSelector": {"matchLabels": service["spec"]["selector"]}}],
                      "ports": [{"protocol": "TCP", "port": 8081}]}
    ingress = find(docs, "NetworkPolicy", "rovr", "rovr")["spec"]["ingress"]
    internal = next(rule for rule in ingress if rule["ports"] == [{"protocol": "TCP", "port": 8081}])
    assert internal["from"] == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "rovr-sandboxes"}},
                                 "podSelector": {"matchLabels": {"app.kubernetes.io/name": "rovr-sandbox"}}}]


def test_public_route_targets_only_the_public_listener():
    docs = render()
    ports = {p["name"]: p["port"] for p in find(docs, "Service", "rovr")["spec"]["ports"]}
    assert ports == {"http": 8080, "internal": 8081}
    route = find(docs, "HTTPRoute", "rovr")["spec"]
    assert route["hostnames"] == ["rovr.dev"]
    assert [ref["port"] for rule in route["rules"] for ref in rule["backendRefs"]] == [8080]
    container = find(docs, "Deployment", "rovr")["spec"]["template"]["spec"]["containers"][0]
    assert {p["name"]: p["containerPort"] for p in container["ports"]} == {"http": 8080, "internal": 8081}


def test_sandbox_settings_follow_the_server_image():
    config = find(render("--set", "app.image.tag=v1.2.3"), "ConfigMap", "rovr-sandbox")["data"]
    assert config["ROVR_SANDBOX_IMAGE"] == "396608815058.dkr.ecr.ap-northeast-2.amazonaws.com/rovr:sandbox-v1.2.3"
    assert config["ROVR_SANDBOX_SERVER_URL"] == "http://rovr.rovr.svc.cluster.local:8081"
    assert config["ROVR_SANDBOX_NODE_POOL"] == "rovr-sandboxes"
    assert config["ROVR_SANDBOX_IMAGE_PULL_SECRET"] == "ecr-registry"
    assert find(render(), "ExternalSecret", "ecr-registry", "rovr-sandboxes")["spec"]["target"]["name"] == "ecr-registry"


@pytest.mark.parametrize("setting", ["sandbox.maxRuns=3", "sandbox.memory=8Gi", "image.tag=v9.9.9"])
def test_sandbox_changes_restart_the_server(setting):
    def checksum(docs):
        return find(docs, "Deployment", "rovr")["spec"]["template"]["metadata"]["annotations"]["checksum/sandbox"]
    assert checksum(render()) != checksum(render("--set", "app." + setting))


def test_server_runs_hardened_with_fixed_replicas_and_its_secrets():
    docs = render()
    deployment = find(docs, "Deployment", "rovr")
    pod = deployment["spec"]["template"]["spec"]
    assert deployment["spec"]["replicas"] == 2
    assert not any(item["kind"] == "HorizontalPodAutoscaler" for item in docs)
    assert pod["automountServiceAccountToken"] is True
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True
    refs = {e["secretKey"]: e["remoteRef"]["key"] for e in find(docs, "ExternalSecret", "rovr-external")["spec"]["data"]}
    assert all(key.startswith("/k8s/eks-demo/rovr/") for key in refs.values())
    assert {"DATABASE_URL", "ROVR_SESSION_SECRET", "ROVR_RUN_TOKEN_SECRET", "GITHUB_APP_PRIVATE_KEY",
            "GITHUB_APP_CLIENT_ID", "GITHUB_WEBHOOK_SECRET", "ANTHROPIC_API_KEY", "ROVR_SECRET_KEY"} <= set(refs)
    assert "OPENAI_API_KEY" not in refs  # enabled per cluster once the parameter exists
    config = find(docs, "ConfigMap", "rovr")["data"]
    assert config["ROVR_PUBLIC_URL"] == "https://rovr.dev"


def test_database_bootstrap_stays_in_the_postgresql_namespace():
    docs = render()
    secret = find(docs, "ExternalSecret", "rovr-postgresql", "agent-studio")
    assert secret["spec"]["data"][0]["remoteRef"]["key"] == "/k8s/eks-demo/rovr/postgres-password"
    job = find(docs, "Job", "rovr-db-bootstrap", "agent-studio")
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "Sync"
    env = {e["name"]: e["valueFrom"]["secretKeyRef"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["PGPASSWORD"] == {"name": "postgresql-credentials", "key": "postgres-password"}
    assert env["ROVR_PASSWORD"] == {"name": "rovr-postgresql", "key": "password"}
    script = job["spec"]["template"]["spec"]["containers"][0]["command"][-1]
    assert "NOSUPERUSER" in script and "CREATE DATABASE rovr OWNER rovr" in script
    # The administrator password never leaves the PostgreSQL namespace.
    assert not any(item["kind"] == "ExternalSecret" and item["metadata"].get("namespace") in (None, "rovr")
                   and "postgres-password" in str(item["spec"]) and item["metadata"]["name"] != "rovr-postgresql" for item in docs)


def test_clusters_without_rovr_run_nothing():
    docs = render(cluster="eks-demo-a")
    assert find(docs, "Deployment", "rovr")["spec"]["replicas"] == 0
    assert not any(item["kind"] in {"Namespace", "Role", "Job", "ExternalSecret", "HTTPRoute"} for item in docs)

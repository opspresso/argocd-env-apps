"""Include every process-owned pool and two simultaneous rollout generations."""
from copy import deepcopy
import math
from pathlib import Path

from test_agent_platform import resource
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
AGENT_CHARTS = ("agent-studio", "agent-memory", "agent-knowledge", "agent-forge", "agent-models", "agent-delivery")
OPERATIONS_RESERVE = 20


def charts_for(platform):
    # Follow declared Applications, not the presence of generated output: a missing values file must not hide a service.
    paths = set()
    for source in (ROOT / "apps" / platform).glob("*.yaml"):
        application = yaml.safe_load(source.read_text())
        spec = application.get("spec", {})
        spec = spec.get("template", {}).get("spec", spec)
        path = spec.get("source", {}).get("path")
        if path:
            paths.add(path)
    return tuple(chart for chart in AGENT_CHARTS if f"charts/{chart}" in paths)


def rovr_declared(platform):
    return (ROOT / "apps" / platform / "rovr.yaml").exists()


def database_connections(platform):
    """Per-generation ceilings from rendered settings and the services' owned-pool contracts.

    Fixed pools are documented with their owning source in docs/production-capacity.md.
    The services are separate repositories: CI does not import an arbitrary sibling checkout.
    """
    def maximum_replicas(chart):
        if platform == "eks":
            return resource(chart, platform, "HorizontalPodAutoscaler", chart)["spec"]["maxReplicas"]
        return resource(chart, platform, "Deployment", chart)["spec"]["replicas"]

    charts = charts_for(platform)
    config = {chart: resource(chart, platform, "ConfigMap", chart)["data"] for chart in charts if chart != "agent-forge"}
    def pool(chart):
        return int(config[chart]["DATABASE_POOL_SIZE"])
    # Knowledge web and worker share settings but own separate pools. The worker has no HTTP readiness pool.
    knowledge = sum(int(config["agent-knowledge"][key])
                    for key in ("DATABASE_POOL_SIZE", "DATABASE_QUEUE_POOL_SIZE", "AI_ADMISSION_POOL_SIZE")) + 2 + 2
    per_process = {
        "agent-studio": pool("agent-studio") + 4 + 1,  # content locks and readiness; the ticker only calls HTTP
        "agent-memory": pool("agent-memory") + 2 + 2 + 1,  # background sessions, OIDC locks, readiness
        "agent-knowledge": knowledge + 1,
        "agent-forge": 12,  # main.ts: refresh leases and readiness use the same query pool
        "agent-models": pool("agent-models") + 4 + 1 + 1,  # OIDC locks, readiness, lease renewal
    }
    if "agent-delivery" in charts:
        per_process["agent-delivery"] = pool("agent-delivery") + 4 + 1  # OIDC locks and readiness
    connections = {chart: maximum_replicas(chart) * per_process[chart] for chart in charts}
    worker = resource("agent-knowledge", platform, "Deployment", "agent-knowledge-worker")["spec"]["replicas"]
    connections["agent-knowledge-worker"] = worker * knowledge
    # rovr uses a separate database on the same PostgreSQL instance; its leader lock shares this pool.
    if rovr_declared(platform):
        rovr_config = resource("rovr", platform, "ConfigMap", "rovr")["data"]
        replicas = resource("rovr", platform, "Deployment", "rovr")["spec"]["replicas"]
        connections["rovr"] = replicas * int(rovr_config["ROVR_DATABASE_MAX_CONNS"])
    return connections


@pytest.mark.parametrize("platform", ["eks", "k3s"])
def test_database_budget_covers_application_growth_and_rollout_overlap(platform):
    connections = database_connections(platform)
    total = 2 * sum(connections.values())
    postgres = resource("postgresql", platform, "StatefulSet", "postgres")["spec"]["template"]["spec"]["containers"][0]
    capacity = int(next(arg.split("=", 1)[1] for arg in postgres["args"] if arg.startswith("max_connections=")))
    assert capacity >= total + OPERATIONS_RESERVE, (platform, connections, total, capacity)


@pytest.mark.parametrize("platform,chart", [(platform, chart) for platform in ("eks", "k3s")
                                           for chart in charts_for(platform) + (("rovr",) if rovr_declared(platform) else ())])
def test_rollouts_never_exceed_the_two_generations_in_the_budget(chart, platform):
    """The budget doubles every process; no rollout may surge beyond its own replica count."""
    from test_agent_platform import manifests
    for deployment in (doc for doc in manifests(chart, platform) if doc["kind"] == "Deployment"):
        name = deployment["metadata"]["name"]
        replicas = deployment["spec"].get("replicas")
        if replicas is None:
            replicas = resource(chart, platform, "HorizontalPodAutoscaler", name)["spec"]["maxReplicas"]
        strategy = deployment["spec"]["strategy"]
        if strategy["type"] == "Recreate":
            continue
        surge = strategy["rollingUpdate"]["maxSurge"]
        surge = math.ceil(replicas * int(surge[:-1]) / 100) if isinstance(surge, str) else surge
        assert surge <= replicas, name


def test_studio_scales_on_control_plane_requests_and_cpu():
    metrics = resource("agent-studio", "eks", "HorizontalPodAutoscaler", "agent-studio")["spec"]["metrics"]
    assert metrics[0]["pods"]["metric"]["name"] == "agent_studio_active_execution_requests"
    assert metrics[1]["containerResource"]["container"] == "app"
    assert metrics[1]["containerResource"]["name"] == "cpu"


def test_delivery_pool_growth_cannot_escape_the_shared_database_budget(monkeypatch):
    original = resource

    def increased_delivery(chart, platform, kind, name, **overrides):
        value = deepcopy(original(chart, platform, kind, name, **overrides))
        if chart == "agent-delivery" and kind == "ConfigMap":
            value["data"]["DATABASE_POOL_SIZE"] = "100"
        return value

    monkeypatch.setitem(globals(), "resource", increased_delivery)
    with pytest.raises(AssertionError):
        test_database_budget_covers_application_growth_and_rollout_overlap("eks")


def test_forge_refresh_leases_share_the_fixed_query_pool(monkeypatch):
    original = resource
    replicas = original("agent-forge", "eks", "HorizontalPodAutoscaler", "agent-forge")["spec"]["maxReplicas"]
    # Forge main.ts owns one max12 pool; refreshLeases.ts borrows it, as does readiness.
    forge_only_capacity = 2 * replicas * 12 + OPERATIONS_RESERVE

    def forge_only(chart, platform, kind, name, **overrides):
        value = deepcopy(original(chart, platform, kind, name, **overrides))
        if kind == "HorizontalPodAutoscaler" and chart != "agent-forge":
            value["spec"]["maxReplicas"] = 0
        if kind == "Deployment" and name in ("agent-knowledge-worker", "rovr"):
            value["spec"]["replicas"] = 0
        if chart == "postgresql" and kind == "StatefulSet":
            container = value["spec"]["template"]["spec"]["containers"][0]
            container["args"] = [f"max_connections={forge_only_capacity}"]
        return value

    monkeypatch.setitem(globals(), "resource", forge_only)
    test_database_budget_covers_application_growth_and_rollout_overlap("eks")


def test_rovr_pool_growth_cannot_escape_the_shared_database_budget(monkeypatch):
    original = resource

    def increased_rovr(chart, platform, kind, name, **overrides):
        value = deepcopy(original(chart, platform, kind, name, **overrides))
        if chart == "rovr" and kind == "ConfigMap":
            value["data"]["ROVR_DATABASE_MAX_CONNS"] = "100"
        return value

    monkeypatch.setitem(globals(), "resource", increased_rovr)
    with pytest.raises(AssertionError):
        test_database_budget_covers_application_growth_and_rollout_overlap("eks")


def test_knowledge_worker_growth_consumes_its_own_connections(monkeypatch):
    original = resource

    def increased_worker(chart, platform, kind, name, **overrides):
        value = deepcopy(original(chart, platform, kind, name, **overrides))
        if name == "agent-knowledge-worker" and kind == "Deployment":
            value["spec"]["replicas"] = 20
        return value

    monkeypatch.setitem(globals(), "resource", increased_worker)
    with pytest.raises(AssertionError):
        test_database_budget_covers_application_growth_and_rollout_overlap("eks")


def test_delivery_maintenance_keeps_the_resumed_capacity_in_the_fixture(tmp_path, monkeypatch):
    import running_environment
    import yaml

    env = yaml.safe_load((ROOT / "env/eks-demo.yaml").read_text())
    env["agent_delivery_maintenance"] = True
    (tmp_path / "env").mkdir()
    (tmp_path / "env/eks-demo.yaml").write_text(yaml.safe_dump(env))
    chart = tmp_path / "charts/agent-delivery"
    chart.mkdir(parents=True)
    (chart / "values-template.yaml.j2").write_text((ROOT / "charts/agent-delivery/values-template.yaml.j2").read_text())
    monkeypatch.setattr(running_environment, "ROOT", tmp_path)
    rendered = yaml.safe_load(running_environment.running_values("agent-delivery", "eks"))
    assert rendered["app"]["replicaCount"] == env["replicas"]
    assert rendered["app"]["autoscaling"]["enabled"] is True

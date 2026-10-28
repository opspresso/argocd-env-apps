"""Include every process-owned pool and two simultaneous rollout generations."""
import math

from test_agent_platform import resource
import pytest


@pytest.mark.parametrize("platform,expected", [("eks", 446), ("k3s", 162)])
def test_database_budget_covers_application_growth_and_rollout_overlap(platform, expected):
    def maximum_replicas(chart):
        if platform == "eks":
            return resource(chart, platform, "HorizontalPodAutoscaler", chart)["spec"]["maxReplicas"]
        return resource(chart, platform, "Deployment", chart)["spec"]["replicas"]

    config = {chart: resource(chart, platform, "ConfigMap", chart)["data"]
              for chart in ("agent-studio", "agent-memory", "agent-knowledge", "agent-models")}
    studio_pool = int(config["agent-studio"]["DATABASE_POOL_SIZE"])
    # Studio web is the only Studio process with a database; the ticker only calls HTTP.
    # Content locks use a separate max4 pool in Studio, plus one readiness connection.
    studio = maximum_replicas("agent-studio") * (studio_pool + 1 + 4)
    # Memory: query pool + readiness 1 + background session locks 2 + OIDC refresh locks 2.
    memory = maximum_replicas("agent-memory") * (int(config["agent-memory"]["DATABASE_POOL_SIZE"]) + 1 + 2 + 2)
    # Knowledge web and worker both build the maintenance pool 2 and OIDC refresh pool 2.
    # Platform retention runs only in the worker and uses the query pool.
    # Worker health is process-only and never opens the HTTP readiness pool.
    knowledge_pool = sum(int(config["agent-knowledge"][key]) for key in ("DATABASE_POOL_SIZE", "DATABASE_QUEUE_POOL_SIZE", "AI_ADMISSION_POOL_SIZE")) + 2 + 2
    knowledge_worker = resource("agent-knowledge", platform, "Deployment", "agent-knowledge-worker")["spec"]["replicas"]
    knowledge = maximum_replicas("agent-knowledge") * (knowledge_pool + 1) + knowledge_worker * knowledge_pool
    # Forge: fixed query pool 12 + OIDC refresh locks 2; readiness uses the query pool.
    forge = maximum_replicas("agent-forge") * (12 + 2)
    # Models maxDatabaseConnections: query pool + OIDC refresh locks 4 + readiness 1 + lease renewal 1.
    models = maximum_replicas("agent-models") * (int(config["agent-models"]["DATABASE_POOL_SIZE"]) + 6)
    total = 2 * (studio + memory + knowledge + forge + models)
    assert total == expected
    postgres = resource("postgresql", platform, "StatefulSet", "postgres")["spec"]["template"]["spec"]["containers"][0]
    capacity = int(next(arg.split("=", 1)[1] for arg in postgres["args"] if arg.startswith("max_connections=")))
    assert capacity >= total + 20


@pytest.mark.parametrize("platform", ["eks", "k3s"])
@pytest.mark.parametrize("chart", ["agent-studio", "agent-memory", "agent-knowledge", "agent-forge", "agent-models"])
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

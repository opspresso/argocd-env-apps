"""Include every process-owned pool and two simultaneous rollout generations."""
from test_agent_platform import resource
import pytest


@pytest.mark.parametrize("platform,expected", [("eks", 490), ("k3s", 192)])
def test_database_budget_covers_application_growth_and_rollout_overlap(platform, expected):
    def maximum_replicas(chart):
        if platform == "eks":
            return resource(chart, platform, "HorizontalPodAutoscaler", chart)["spec"]["maxReplicas"]
        return resource(chart, platform, "Deployment", chart)["spec"]["replicas"]

    config = {chart: resource(chart, platform, "ConfigMap", chart)["data"]
              for chart in ("agent-studio", "agent-memory", "agent-librio", "agent-models")}
    studio_pool = int(config["agent-studio"]["DATABASE_POOL_SIZE"])
    workspace = resource("agent-studio", platform, "Deployment", "agent-studio-workspace-worker")["spec"]["replicas"]
    audio = resource("agent-studio", platform, "Deployment", "agent-studio-audio-worker")["spec"]["replicas"]
    # Content locks use a separate max4 pool in Studio. Only HTTP web runs readiness.
    # The Workspace readiness subprocess issues one query, then closes its pool.
    studio = maximum_replicas("agent-studio") * (studio_pool + 1 + 4) + (workspace + audio) * (studio_pool + 4) + workspace
    # Memory: query pool + readiness 1 + background session locks 2 + OIDC refresh locks 2.
    memory = maximum_replicas("agent-memory") * (int(config["agent-memory"]["DATABASE_POOL_SIZE"]) + 1 + 2 + 2)
    # Librio web and worker both build the maintenance pool 2 and OIDC refresh pool 2.
    # Worker health is process-only and never opens the HTTP readiness pool.
    librio_pool = sum(int(config["agent-librio"][key]) for key in ("DATABASE_POOL_SIZE", "DATABASE_QUEUE_POOL_SIZE", "AI_ADMISSION_POOL_SIZE")) + 2 + 2
    librio_worker = resource("agent-librio", platform, "Deployment", "agent-librio-worker")["spec"]["replicas"]
    librio = maximum_replicas("agent-librio") * (librio_pool + 1) + librio_worker * librio_pool
    # Forge: fixed query pool 12 + OIDC refresh locks 2; readiness uses the query pool.
    forge = maximum_replicas("agent-forge") * (12 + 2)
    # Models: query pool (readiness uses it) + OIDC refresh locks 4.
    models = maximum_replicas("agent-models") * (int(config["agent-models"]["DATABASE_POOL_SIZE"]) + 4)
    total = 2 * (studio + memory + librio + forge + models)
    assert total == expected
    postgres = resource("postgresql", platform, "StatefulSet", "postgres")["spec"]["template"]["spec"]["containers"][0]
    capacity = int(next(arg.split("=", 1)[1] for arg in postgres["args"] if arg.startswith("max_connections=")))
    assert capacity >= total + 20


def test_studio_scales_on_control_plane_requests_and_cpu():
    metrics = resource("agent-studio", "eks", "HorizontalPodAutoscaler", "agent-studio")["spec"]["metrics"]
    assert metrics[0]["pods"]["metric"]["name"] == "agent_studio_active_execution_requests"
    assert metrics[1]["containerResource"]["container"] == "app"
    assert metrics[1]["containerResource"]["name"] == "cpu"

"""Critical services run on the on-demand core nodes; restartable workloads stay on Spot."""
from pathlib import Path
import subprocess

import pytest
import yaml
from running_environment import running_values

ROOT = Path(__file__).resolve().parents[1]
CORE = {"key": "opspresso.com/tier", "operator": "In", "values": ["core"]}
TOLERATION = {"key": "opspresso.com/tier", "operator": "Equal", "value": "core", "effect": "NoSchedule"}
PREFERRED = {
    "agent-studio": ["agent-studio", "agent-studio-ticker"],
    "agent-memory": ["agent-memory"],
    "agent-models": ["agent-models"],
    "agent-runway": ["agent-runway"],
    "agent-forge": ["agent-forge"],
    "agent-knowledge": ["agent-knowledge", "agent-knowledge-worker"],
    "rovr": ["rovr"],
}


def render(chart):
    path = ROOT / "charts" / chart
    if not list((path / "charts").glob("*.tgz")):
        pytest.skip(f"Run helm dependency update {path}")
    phase = [str(path / "values-prod.yaml")] if (path / "values-prod.yaml").exists() else []
    files = [arg for value in [str(path / "values.yaml"), *phase, "-"] for arg in ("-f", value)]
    result = subprocess.run(["helm", "template", chart, str(path), "--namespace", chart, *files],
                            input=running_values(chart, "eks"), capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def pod_spec(docs, kind, name):
    return next(doc for doc in docs if doc["kind"] == kind and doc["metadata"]["name"] == name)["spec"]["template"]


@pytest.mark.parametrize("chart", sorted(PREFERRED))
def test_replicated_services_prefer_core_and_spill_over(chart):
    docs = render(chart)
    for name in PREFERRED[chart]:
        template = pod_spec(docs, "Deployment", name)
        spec = template["spec"]
        assert TOLERATION in spec["tolerations"], name
        node = spec["affinity"]["nodeAffinity"]
        # Preferred only: HPA growth and rollout surge must still schedule on Spot when core is full.
        assert "requiredDuringSchedulingIgnoredDuringExecution" not in node, name
        assert node["preferredDuringSchedulingIgnoredDuringExecution"] == [{"weight": 100, "preference": {"matchExpressions": [CORE]}}]


@pytest.mark.parametrize("chart", ["postgresql", "delivery-pool"])
def test_shared_postgresql_requires_core_and_blocks_voluntary_disruption(chart):
    template = pod_spec(render(chart), "StatefulSet", "postgres")
    spec = template["spec"]
    assert TOLERATION in spec["tolerations"]
    terms = spec["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
    assert terms == [{"matchExpressions": [CORE]}]
    assert template["metadata"]["annotations"]["karpenter.sh/do-not-disrupt"] == "true"

"""RUNWAY_ENVIRONMENTS must satisfy Agent Runway's boot validation (agent-platform docs/APP_DELIVERY.md)."""

import json
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"^[a-z][a-z0-9]{1,11}$")
FIELDS = {"name", "approval", "sandbox"}


def rendered_profiles():
    for path in sorted((ROOT / "charts/agent-runway").glob("*/values-*.yaml")):
        data = yaml.safe_load(path.read_text())["app"].get("configmap", {}).get("data", {})
        if "RUNWAY_ENVIRONMENTS" in data:
            yield path.name, json.loads(data["RUNWAY_ENVIRONMENTS"])


def test_every_rendered_profile_is_valid():
    profiles = list(rendered_profiles())
    assert profiles, "no cluster renders RUNWAY_ENVIRONMENTS"
    for name, environments in profiles:
        assert environments, name
        for environment in environments:
            # Agent Runway refuses any other field: pipelines hold the trigger and the promotion order.
            assert set(environment) == FIELDS, (name, environment)
            assert NAME.match(environment["name"]), (name, environment)
            assert environment["approval"] in ("none", "owner"), (name, environment)
            assert isinstance(environment["sandbox"], bool), (name, environment)
        names = [environment["name"] for environment in environments]
        assert len(names) == len(set(names)), name
        assert sum(environment["sandbox"] for environment in environments) <= 1, name


def test_eks_demo_uses_the_default_profile():
    profile = dict(rendered_profiles())["values-eks-demo.yaml"]
    assert profile == [
        {"name": "dev", "approval": "none", "sandbox": True},
        {"name": "prod", "approval": "owner", "sandbox": False},
    ]


def test_no_cluster_sets_the_v1_workflow_template():
    # Agent Runway v2 refuses to start while RUNWAY_WORKFLOW_TEMPLATE is set.
    for path in sorted((ROOT / "charts/agent-runway").glob("*/values-*.yaml")):
        data = yaml.safe_load(path.read_text())["app"].get("configmap", {}).get("data", {})
        assert "RUNWAY_WORKFLOW_TEMPLATE" not in data, path.name

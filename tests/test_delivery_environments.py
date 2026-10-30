"""DELIVERY_ENVIRONMENTS must satisfy Agent Runway's boot validation (agent-platform docs/DELIVERY_V2.md D1)."""

import json
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"^[a-z][a-z0-9]{1,11}$")
FIELDS = {"name", "primary", "approval", "sandbox", "requiresPrevious"}


def rendered_profiles():
    for path in sorted((ROOT / "charts/agent-delivery").glob("*/values-*.yaml")):
        data = yaml.safe_load(path.read_text())["app"].get("configmap", {}).get("data", {})
        if "DELIVERY_ENVIRONMENTS" in data:
            yield path.name, json.loads(data["DELIVERY_ENVIRONMENTS"])


def test_every_rendered_profile_is_valid():
    profiles = list(rendered_profiles())
    assert profiles, "no cluster renders DELIVERY_ENVIRONMENTS"
    for name, environments in profiles:
        assert environments, name
        for environment in environments:
            assert set(environment) == FIELDS, (name, environment)
            assert NAME.match(environment["name"]), (name, environment)
            assert environment["approval"] in ("none", "owner"), (name, environment)
            assert all(isinstance(environment[key], bool) for key in ("primary", "sandbox", "requiresPrevious"))
        names = [environment["name"] for environment in environments]
        assert len(names) == len(set(names)), name
        assert sum(environment["primary"] for environment in environments) == 1, name
        assert sum(environment["sandbox"] for environment in environments) <= 1, name
        # The first environment has no previous one to be promoted from.
        assert not environments[0]["requiresPrevious"], name


def test_eks_demo_uses_the_default_profile():
    profile = dict(rendered_profiles())["values-eks-demo.yaml"]
    assert profile == [
        {"name": "dev", "primary": False, "approval": "none", "sandbox": True, "requiresPrevious": False},
        {"name": "prod", "primary": True, "approval": "owner", "sandbox": False, "requiresPrevious": True},
    ]


def test_no_cluster_sets_the_v1_workflow_template():
    # Agent Runway v2 refuses to start while DELIVERY_WORKFLOW_TEMPLATE is set.
    for path in sorted((ROOT / "charts/agent-delivery").glob("*/values-*.yaml")):
        data = yaml.safe_load(path.read_text())["app"].get("configmap", {}).get("data", {})
        assert "DELIVERY_WORKFLOW_TEMPLATE" not in data, path.name

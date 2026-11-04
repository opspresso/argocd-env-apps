"""Render helpers and platform-independent MCP resource flags."""

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]


def render(chart, value_files, extra_args=()):
    if not shutil.which("helm"):
        pytest.skip("Helm is required to validate deployment manifests")
    chart_path = ROOT / chart
    definition = yaml.safe_load((chart_path / "Chart.yaml").read_text())
    if definition.get("dependencies") and not list((chart_path / "charts").glob("*.tgz")):
        pytest.skip(f"Run helm dependency update {chart} before this render check")
    command = ["helm", "template", "local-test", str(chart_path)]
    for value_file in value_files:
        command += ["-f", str(chart_path / value_file)]
    return subprocess.run(command + list(extra_args), capture_output=True, text=True)


@pytest.mark.parametrize("chart", ["mcp-argocd", "mcp-cloudwatch", "mcp-grafana", "mcp-kubernetes"])
def test_mcp_flags_are_independent_of_platform_routing(chart):
    context = yaml.safe_load((ROOT / "env/eks-demo.yaml").read_text())
    context.update(resources=False, autoscaling=False)
    template = Environment(loader=FileSystemLoader(ROOT / "charts" / chart)).get_template("values-template.yaml.j2")
    values = yaml.safe_load(template.render(context))["app"]
    assert values["resources"] is None
    assert values["autoscaling"]["enabled"] is False

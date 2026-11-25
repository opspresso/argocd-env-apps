"""Cluster credentials must never come from a shared default or another cluster."""

from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader
import pytest
import yaml

import validate
from workload_policy import policy_errors


ROOT = Path(__file__).resolve().parents[1]
# Charts whose ExternalSecret exists only in the generated cluster values, with the env key of their settings.
DATA_CHARTS = {"postgresql": "postgresql", "delivery-pool": "delivery_pool"}
ENV_FILES = sorted((ROOT / "env").glob("*.yaml"))


def helm(chart, values=None):
    if not shutil.which("helm"):
        pytest.skip("Helm is required for deployment contract tests")
    path = ROOT / "charts" / chart
    definition = yaml.safe_load((path / "Chart.yaml").read_text())
    if definition.get("dependencies") and not list((path / "charts").glob("*.tgz")):
        pytest.skip(f"Run helm dependency update charts/{chart} first")
    args = ["helm", "template", f"{chart}-test", str(path), "-f", str(path / "values.yaml")]
    if values:
        args += ["-f", str(values)]
    return subprocess.run(args, capture_output=True, text=True)


def test_shared_values_do_not_contain_cluster_specific_secret_defaults():
    clusters = [yaml.safe_load(path.read_text())["cluster"] for path in ENV_FILES]
    for path in (ROOT / "charts").glob("*/values.yaml"):
        values = yaml.safe_load(path.read_text()) or {}
        assert "secretPrefix" not in values, path
        parsed = yaml.safe_dump(values)
        assert all(f"/k8s/{cluster}" not in parsed for cluster in clusters), path


def external_secret_keys(manifest):
    return [entry["remoteRef"]["key"]
            for doc in yaml.safe_load_all(manifest) if doc and doc["kind"] == "ExternalSecret"
            for entry in doc["spec"].get("data", [])]


@pytest.mark.parametrize("chart", DATA_CHARTS)
def test_missing_cluster_values_read_no_credentials_instead_of_a_default(chart):
    result = helm(chart)
    assert result.returncode == 0, result.stderr
    assert external_secret_keys(result.stdout) == []


@pytest.mark.parametrize("chart", DATA_CHARTS)
def test_empty_eks_service_settings_preserve_common_defaults(chart):
    context = yaml.safe_load((ROOT / "env/eks-demo.yaml").read_text())
    context[DATA_CHARTS[chart]] = {}
    # core_tier is a cluster placement, not a service setting; tests/test_core_tier.py covers it.
    context["core_tier"] = False
    template = Environment(loader=FileSystemLoader(ROOT / "charts" / chart)).get_template("values-template.yaml.j2")
    # The cluster file owns only the raw resource lists; it overrides no common value.
    assert set(yaml.safe_load(template.render(context))) <= {"raw", "bootstrap"}


@pytest.mark.parametrize("chart", DATA_CHARTS)
@pytest.mark.parametrize("env_file", ENV_FILES, ids=lambda path: path.stem)
def test_data_charts_render_only_the_selected_clusters_secrets(chart, env_file, tmp_path):
    context = yaml.safe_load(env_file.read_text())
    template = Environment(loader=FileSystemLoader(ROOT / "charts" / chart)).get_template("values-template.yaml.j2")
    values_file = tmp_path / "cluster.yaml"
    values_file.write_text(template.render(context))
    result = helm(chart, values_file)
    assert result.returncode == 0, result.stderr
    refs = external_secret_keys(result.stdout)
    assert refs
    assert all(key.startswith(f"/k8s/{context['cluster']}/") for key in refs)
    assert policy_errors(result.stdout, context["env"]) == []


@pytest.mark.parametrize("chart", ["agent-studio", "agent-memory", "agent-knowledge", "agent-forge"])
def test_application_secrets_follow_a_new_cluster_name(chart):
    context = yaml.safe_load((ROOT / "env/eks-demo.yaml").read_text())
    context["cluster"] = "eks-other"
    template = Environment(loader=FileSystemLoader(ROOT / "charts" / chart)).get_template("values-template.yaml.j2")
    generated = template.render(context)
    assert "/k8s/eks-demo/" not in generated
    assert "/k8s/common/" not in generated
    assert "/k8s/eks-other/agent-platform/database-url" in generated


@pytest.mark.parametrize("cluster", ["eks-demo", "eks-demo-a", "local-demo"])
@pytest.mark.parametrize("scope", ["root", "global"])
def test_validator_rejects_wrong_prefix_before_helm(cluster, scope, tmp_path):
    chart = tmp_path / "chart"
    chart.mkdir()
    values = {"secretPrefix": "/k8s/wrong-cluster"}
    (chart / "values.yaml").write_text(yaml.safe_dump({"global": values} if scope == "global" else values))
    target = {"name": "data", "namespace": "default", "cluster": cluster,
              "chart": str(chart), "appset": "apps/eks/data.yaml", "value_files": ["values.yaml"]}
    with patch.object(validate.subprocess, "run") as run:
        error = validate.render(target, None)
    assert f"secretPrefix must match the target cluster: /k8s/{cluster}" == error
    run.assert_not_called()


def test_validator_accepts_generated_cluster_values_without_a_label(tmp_path):
    (tmp_path / "eks").mkdir()
    (tmp_path / "values.yaml").write_text("{}\n")
    (tmp_path / "eks/values-eks-demo-a.yaml").write_text("secretPrefix: /k8s/eks-demo-a\n")
    target = {"name": "data", "namespace": "default", "chart": str(tmp_path),
              "appset": "apps/eks/data.yaml", "value_files": ["values.yaml", "eks/values-eks-demo-a.yaml"]}
    result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    with patch.object(validate.subprocess, "run", return_value=result) as run:
        assert validate.render(target, None) is None
    run.assert_called_once()


def test_validator_rejects_a_cluster_file_for_another_target(tmp_path):
    (tmp_path / "eks").mkdir()
    (tmp_path / "eks/values-eks-demo-a.yaml").write_text("secretPrefix: /k8s/eks-demo\n")
    target = {"name": "data", "namespace": "default", "cluster": "eks-demo", "chart": str(tmp_path),
              "appset": "apps/eks/data.yaml", "value_files": ["eks/values-eks-demo-a.yaml"]}
    with patch.object(validate.subprocess, "run") as run:
        assert validate.render(target, None) == "cluster values must match the target cluster: eks-demo"
    run.assert_not_called()


@pytest.mark.parametrize("chart", DATA_CHARTS)
def test_validator_rejects_another_clusters_values_without_secret_prefix(chart):
    # The generated file has no secretPrefix; its ExternalSecret still reads /k8s/eks-demo-a/.
    path = ROOT / "charts" / chart
    target = {"name": chart, "namespace": chart, "cluster": "eks-demo", "chart": str(path),
              "appset": f"apps/eks/{chart}.yaml", "value_files": ["values.yaml", "eks/values-eks-demo-a.yaml"]}
    with patch.object(validate.subprocess, "run") as run:
        assert validate.render(target, None) == "cluster values must match the target cluster: eks-demo"
    run.assert_not_called()

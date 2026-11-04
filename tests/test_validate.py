import sys

import pytest

import validate


def test_wrapper_templates_are_rejected_but_dependency_templates_are_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, "CHARTS_DIR", str(tmp_path))
    chart = tmp_path / "demo"
    dependency = chart / "charts" / "upstream" / "templates"
    dependency.mkdir(parents=True)
    (dependency / "deployment.yaml").write_text("kind: Deployment\n")
    assert validate.check_templates() == []
    (chart / "templates").mkdir()
    (chart / "templates/_helpers.tpl").write_text("{{ define \"override\" }}{{ end }}")
    assert "wrapper templates are not allowed" in validate.check_templates()[0][1]


def test_all_charts_includes_undeployed_phases_and_unused_cluster_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "env").mkdir()
    (tmp_path / "env/eks-unused.yaml").write_text("env: eks\ncluster: eks-unused\nphase: prod\n")
    for name in ("deployed", "unused"):
        chart = tmp_path / "charts" / name
        chart.mkdir(parents=True)
        (chart / "Chart.yaml").write_text(f"name: {name}\n")
        (chart / "values.yaml").write_text("{}")
        (chart / "values-prod.yaml").write_text("{}")
    (tmp_path / "charts/deployed/eks").mkdir()
    (tmp_path / "charts/deployed/eks/values-eks-unused.yaml").write_text("{}")
    targets = [{"chart": "charts/deployed", "namespace": "app", "env_files": [None], "value_files": ["values.yaml"]}]
    extra = validate.additional_targets(targets)
    assert [(t["chart"], t["value_files"]) for t in extra] == [
        ("charts/deployed", ["values.yaml", "values-prod.yaml", "eks/values-eks-unused.yaml"]),
        ("charts/unused", ["values.yaml", "values-prod.yaml"]),
    ]


@pytest.mark.parametrize("exists", [False, True])
def test_empty_or_missing_target_directory_fails(tmp_path, monkeypatch, capsys, exists):
    root = tmp_path / "repo"
    (root / "charts").mkdir(parents=True)
    if exists:
        (root / "apps").mkdir()
    monkeypatch.setattr(validate, "__file__", str(root / "scripts/validate.py"))
    monkeypatch.setattr(sys, "argv", ["validate.py"])
    monkeypatch.chdir(root)
    assert validate.main() == 1
    assert "no" in capsys.readouterr().out.lower()


def test_failed_dependencies_are_not_rendered_for_another_target(monkeypatch):
    targets = [{"chart": "charts/demo", "env_files": [None], "appset": "apps/a.yaml"},
               {"chart": "charts/demo", "env_files": [None], "appset": "apps/b.yaml"}]
    monkeypatch.setattr(sys, "argv", ["validate.py"])
    monkeypatch.setattr(validate, "load_targets", lambda dirs: targets)
    monkeypatch.setattr(validate, "check_templates", lambda only: [])
    monkeypatch.setattr(validate, "update_dependencies", lambda chart: "dependency failed")
    monkeypatch.setattr(validate, "render", lambda *args: pytest.fail("must not render stale dependencies"))
    assert validate.main() == 1


def test_malformed_env_is_reported_as_a_render_failure(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / "bad.yaml"
    env_file.write_text("cluster: [\n")
    target = {"chart": "charts/demo", "env_files": [str(env_file)], "appset": "apps/a.yaml"}
    monkeypatch.setattr(sys, "argv", ["validate.py"])
    monkeypatch.setattr(validate, "load_targets", lambda dirs: [target])
    monkeypatch.setattr(validate, "check_templates", lambda only: [])
    monkeypatch.setattr(validate, "update_dependencies", lambda chart: None)
    assert validate.main() == 1
    assert "FAIL" in capsys.readouterr().out


def test_load_targets_includes_direct_applications(tmp_path):
    apps = tmp_path / "apps" / "eks"
    apps.mkdir(parents=True)
    (apps / "agent-studio.yaml").write_text(
        """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: agent-studio-direct
spec:
  source:
    path: charts/agent-studio
    helm:
      valueFiles:
        - values.yaml
  destination:
    namespace: agent-studio
"""
    )

    targets = validate.load_targets([str(tmp_path / "apps")])

    assert len(targets) == 1
    assert targets[0]["chart"] == "charts/agent-studio"
    assert targets[0]["env_files"] == [None]
    assert targets[0]["value_files"] == ["values.yaml"]

"""Fresh-volume PostgreSQL contracts shared by EKS and local Kubernetes."""

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["eks", "local"])
def manifests(request, local_env, tmp_path):
    if not shutil.which("helm"):
        pytest.skip("Helm is required to validate deployment manifests")
    chart = ROOT / "charts/postgresql"
    command = ["helm", "template", "postgresql-test", str(chart), "-f", str(chart / "values.yaml")]
    if request.param == "local":
        template = Environment(loader=FileSystemLoader(chart), undefined=StrictUndefined).get_template("values-template.yaml.j2")
        values = tmp_path / "values-local-test.yaml"
        values.write_text(template.render(local_env))
    else:
        values = chart / request.param / f"values-{request.param}-demo.yaml"
    command += ["-f", str(values)]
    rendered = subprocess.run(command, check=True, text=True, capture_output=True).stdout
    return {doc["kind"]: doc for doc in yaml.safe_load_all(rendered) if doc}


def test_bootstrap_connects_as_the_user_created_by_the_image(manifests):
    container = manifests["StatefulSet"]["spec"]["template"]["spec"]["containers"][0]
    user = next(env["value"] for env in container["env"] if env["name"] == "POSTGRES_USER")
    bootstrap = manifests["Job"]["spec"]["template"]["spec"]["containers"][0]["command"][-1]
    assert f"pg_isready -h postgres -U {user}" in bootstrap
    assert f"psql -h postgres -U {user}" in bootstrap
    assert "ALTER ROLE" not in bootstrap


def test_official_entrypoint_owns_empty_volume_initialization(manifests):
    statefulset = manifests["StatefulSet"]
    assert statefulset["metadata"]["name"] == "postgres"
    assert statefulset["spec"]["serviceName"] == "postgres-hl"
    assert statefulset["spec"]["selector"]["matchLabels"] == {
        "app.kubernetes.io/name": "postgresql",
        "app.kubernetes.io/instance": "postgresql-test",
        "app.kubernetes.io/component": "primary",
    }
    assert statefulset["spec"]["volumeClaimTemplates"][0]["metadata"]["name"] == "data"
    pod = manifests["StatefulSet"]["spec"]["template"]["spec"]
    assert not pod.get("initContainers")
    container = pod["containers"][0]
    assert "command" not in container
    assert next(env["value"] for env in container["env"] if env["name"] == "PGDATA") == "/var/lib/postgresql/data"
    assert next(mount["mountPath"] for mount in container["volumeMounts"] if mount["name"] == "data") == "/var/lib/postgresql"
    for probe in ["readinessProbe", "livenessProbe"]:
        command = container[probe]["exec"]["command"][-1]
        assert '"${POSTGRES_USER}"' in command
        assert "$${" not in command

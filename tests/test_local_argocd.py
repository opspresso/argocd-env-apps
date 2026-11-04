from pathlib import Path

import yaml
import pytest
from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("platform", ["eks"])
def test_applications_use_the_existing_controller(platform):
    root = yaml.safe_load((ROOT / f"apps-{platform}.yaml").read_text())
    assert root["metadata"]["namespace"] == root["spec"]["destination"]["namespace"] == "argocd"
    assert root["spec"]["project"] == "apps"
    assert root["spec"]["source"]["path"] == f"apps/{platform}"
    paths = list((ROOT / "apps" / platform).glob("*.yaml"))
    assert paths
    for path in paths:
        application = yaml.safe_load(path.read_text())
        assert application["metadata"].get("namespace", "argocd") == "argocd"
        spec = application["spec"]
        if application["kind"] == "ApplicationSet":
            spec = spec["template"]["spec"]
        assert spec["project"] == "apps"


def test_argocd_mcp_uses_local_server_and_local_token_only_on_local(local_env):
    template = Environment(loader=FileSystemLoader(ROOT / "charts/mcp-argocd")).get_template("values-template.yaml.j2")
    for platform in ("local", "eks"):
        context = local_env if platform == "local" else yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
        app = yaml.safe_load(template.render(context))["app"]
        if platform == "local":
            assert app["configmap"]["data"]["ARGOCD_BASE_URL"] == "http://argocd-server.argocd.svc.cluster.local"
            assert app["externalSecrets"]["enabled"] is False
            assert app["additionalSecret"]["names"] == ["mcp-argocd-external"]
        else:
            assert app["configmap"]["data"]["ARGOCD_BASE_URL"] == "http://argocd-server.argocd.svc.cluster.local"
            assert "externalSecrets" not in app

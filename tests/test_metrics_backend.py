import pytest
import yaml

from test_platform_resources import render


@pytest.mark.parametrize("chart", ["agent-studio", "agent-memory", "agent-knowledge", "agent-forge", "sample-node", "sample-grpc"])
@pytest.mark.parametrize("platform,has_monitor", [("eks", True), ("k3s", False)])
def test_service_monitor_requires_a_local_prometheus_operator(chart, platform, has_monitor):
    result = render(f"charts/{chart}", ["values.yaml", f"{platform}/values-{platform}-demo.yaml"],
                    ["--api-versions", "monitoring.coreos.com/v1"])
    assert result.returncode == 0, result.stderr
    manifests = [item for item in yaml.safe_load_all(result.stdout) if item]
    monitors = [item for item in manifests if item["kind"] == "ServiceMonitor"]
    assert bool(monitors) is has_monitor
    if has_monitor:
        assert all(item["metadata"]["labels"]["release"] == "prometheus-eks-demo" for item in monitors)
    assert any(item["kind"] == "Service" for item in manifests)

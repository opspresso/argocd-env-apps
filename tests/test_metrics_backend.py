import pytest
import yaml

from test_platform_resources import render


@pytest.mark.parametrize("chart", ["agent-studio", "agent-memory", "agent-knowledge", "agent-forge"])
def test_prometheus_backend_renders_a_service_monitor(chart):
    result = render(f"charts/{chart}", ["values.yaml", "eks/values-eks-demo.yaml"],
                    ["--api-versions", "monitoring.coreos.com/v1"])
    assert result.returncode == 0, result.stderr
    manifests = [item for item in yaml.safe_load_all(result.stdout) if item]
    monitors = [item for item in manifests if item["kind"] == "ServiceMonitor"]
    assert monitors
    assert all(item["metadata"]["labels"]["release"] == "prometheus-eks-demo" for item in monitors)
    assert any(item["kind"] == "Service" for item in manifests)

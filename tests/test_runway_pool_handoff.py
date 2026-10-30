"""The pool accepts the new service identity while existing clients keep access."""
from pathlib import Path
import subprocess
import yaml


def test_pool_policy_keeps_existing_clients_and_adds_only_runway():
    chart = Path(__file__).resolve().parents[1] / "charts/delivery-pool"
    rendered = subprocess.run(["helm", "template", "delivery-pool-eks-demo", str(chart), "--namespace", "delivery-pool",
                               "-f", str(chart / "values.yaml"), "-f", str(chart / "eks/values-eks-demo.yaml")],
                              check=True, capture_output=True, text=True).stdout
    policy = next(item for item in yaml.safe_load_all(rendered) if item and item.get("kind") == "NetworkPolicy"
                  and item["metadata"]["name"] == "postgres")
    ingress = policy["spec"]["ingress"]
    assert len(ingress) == 1 and ingress[0]["ports"] == [{"protocol": "TCP", "port": 5432}]
    namespaces = [peer["namespaceSelector"]["matchLabels"] for peer in ingress[0]["from"] if "namespaceSelector" in peer]
    assert namespaces == [{"kubernetes.io/metadata.name": "agent-delivery"},
                          {"kubernetes.io/metadata.name": "agent-runway"},
                          {"kubernetes.io/metadata.name": "agent-forge-workspaces"},
                          {"opspresso.com/delivery-tenant": "true"}]

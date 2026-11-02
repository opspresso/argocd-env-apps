"""The prepared chart must use the new identity at every runtime boundary."""

from test_agent_platform import resource


def test_runway_chart_uses_its_own_runtime_identity():
    config = resource("agent-runway", "eks", "ConfigMap", "agent-runway")["data"]
    assert config["RUNWAY_CLUSTER"] == "eks-demo"
    assert config["RUNWAY_SECRET_STORE"] == "ssm"
    assert config["RUNWAY_KMS_KEY_ID"] == "alias/agent-runway"
    assert config["RUNWAY_BUCKET"] == "agent-runway-objects"
    assert not any(key.startswith("DELIVERY_") for key in config)

    secret = resource("agent-runway", "eks", "ExternalSecret", "agent-runway-external")
    refs = {entry["secretKey"]: entry["remoteRef"]["key"] for entry in secret["spec"]["data"]}
    assert refs["AUTH_SECRET"] == "/k8s/eks-demo/agent-runway/auth-secret"
    assert refs["OIDC_CLIENT_ID"] == "/k8s/eks-demo/agent-runway/oidc-client-id"
    assert refs["RUNWAY_POOL_DATABASE_URL"] == "/k8s/eks-demo/agent-runway/pool-database-url"

    deployment = resource("agent-runway", "eks", "Deployment", "agent-runway")
    pod = deployment["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "agent-runway"
    assert pod["containers"][0]["image"].startswith("396608815058.dkr.ecr.ap-northeast-2.amazonaws.com/agent-runway:")


def test_tenant_policy_guards_both_transition_roles():
    import subprocess
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    chart = root / "charts/delivery-tenants"
    rendered = subprocess.run(["helm", "template", "delivery-tenants-eks-demo", str(chart),
                               "-f", str(chart / "values.yaml"), "-f", str(chart / "eks/values-eks-demo.yaml")],
                              check=True, capture_output=True, text=True).stdout
    policy = next(item for item in yaml.safe_load_all(rendered) if item and item.get("kind") == "ValidatingAdmissionPolicy")
    role = next(item["expression"] for item in policy["spec"]["variables"] if item["name"] == "tenantRole")
    assert "role/agent-delivery-tenant-secrets" in role
    assert "role/agent-runway-tenant-secrets" in role
    assert " || " in role
    assert policy["spec"]["failurePolicy"] == "Fail"
    assert any("!variables.tenantRole" in item["expression"] and "variables.tenantNamespace" in item["expression"]
               for item in policy["spec"]["validations"])

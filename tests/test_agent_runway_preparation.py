"""The prepared chart must use the new identity at every runtime boundary."""

import re

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
    assert "role/agent-runway-tenant-secrets" in role
    assert "agent-delivery" not in role
    assert policy["spec"]["failurePolicy"] == "Fail"
    assert any("!variables.tenantRole" in item["expression"] and "variables.tenantNamespace" in item["expression"]
               for item in policy["spec"]["validations"])


def test_workflow_submission_policy_binds_the_runway_identity_and_namespace():
    import subprocess
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    chart = root / "charts/delivery-tenants"
    rendered = subprocess.run(["helm", "template", "delivery-tenants-eks-demo", str(chart),
                               "-f", str(chart / "values.yaml"), "-f", str(chart / "eks/values-eks-demo.yaml")],
                              check=True, capture_output=True, text=True).stdout
    docs = {(item["kind"], item["metadata"]["name"]): item for item in yaml.safe_load_all(rendered) if item}
    policy = docs[("ValidatingAdmissionPolicy", "runway-delivery-submissions")]["spec"]
    binding = docs[("ValidatingAdmissionPolicyBinding", "runway-delivery-submissions")]["spec"]
    assert binding == {"policyName": "runway-delivery-submissions", "validationActions": ["Deny"]}
    assert policy["failurePolicy"] == "Fail"

    # The policy must follow the namespace Runway submits to and the ServiceAccount it runs as.
    config = resource("agent-runway", "eks", "ConfigMap", "agent-runway")["data"]
    assert policy["matchConstraints"]["namespaceSelector"] == {
        "matchLabels": {"kubernetes.io/metadata.name": config["RUNWAY_WORKFLOW_NAMESPACE"]}}
    application = yaml.safe_load((root / "apps/eks/agent-runway.yaml").read_text())
    namespace = application["spec"]["template"]["spec"]["destination"]["namespace"]
    account = resource("agent-runway", "eks", "Deployment", "agent-runway")["spec"]["template"]["spec"]["serviceAccountName"]
    assert policy["matchConditions"] == [{
        "name": "runway", "expression": f"request.userInfo.username == 'system:serviceaccount:{namespace}:{account}'"}]
    # Runway reads its template names from RUNWAY_*_TEMPLATE and falls back to these names.
    assert not any(key.endswith("_TEMPLATE") for key in config)
    rules = {resource_name for rule in policy["matchConstraints"]["resourceRules"] for resource_name in rule["resources"]}
    assert rules == {"workflows", "secrets"}
    # Every WorkflowTemplate Runway submits by default (agent-runway src/lib/config.ts) must be admitted, or that
    # Target kind's deploys and the golden image bakes are denied at submission.
    reference = next(item["expression"] for item in policy["validations"] if "workflowTemplateRef.name in" in item["expression"])
    admitted = set(re.search(r"workflowTemplateRef\.name in \[([^\]]*)\]", reference).group(1).replace("'", "").replace(" ", "").split(","))
    assert admitted == {"app-build", "app-deploy", "app-remove", "app-deploy-eb", "app-remove-eb", "app-deploy-asg", "app-remove-asg",
                        "resource-sync", "image-bake"}


def test_prometheus_scrapes_runway_metrics_with_the_app_bearer_token():
    monitor = resource("agent-runway", "eks", "ServiceMonitor", "agent-runway")
    assert monitor["metadata"]["labels"]["release"] == "prometheus-eks-demo"
    endpoint, = monitor["spec"]["endpoints"]
    service = resource("agent-runway", "eks", "Service", "agent-runway")["spec"]
    assert endpoint["path"] == "/metrics"
    assert endpoint["port"] in {port["name"] for port in service["ports"]}
    credentials = endpoint["authorization"]["credentials"]
    token = resource("agent-runway", "eks", "ExternalSecret", credentials["name"])["spec"]
    assert token["target"]["name"] == credentials["name"]
    remote, = [item["remoteRef"]["key"] for item in token["data"] if item["secretKey"] == credentials["key"]]
    app = resource("agent-runway", "eks", "ExternalSecret", "agent-runway-external")["spec"]["data"]
    assert {"secretKey": "METRICS_BEARER_TOKEN", "remoteRef": {"key": remote}} in [
        {"secretKey": item["secretKey"], "remoteRef": {"key": item["remoteRef"]["key"]}} for item in app]
    assert remote == "/k8s/eks-demo/agent-runway/metrics-bearer-token"

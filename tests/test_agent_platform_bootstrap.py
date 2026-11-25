"""Secret bootstrap preserves identity and all existing credentials."""
import base64
from unittest.mock import patch
import pytest
import bootstrap_agent_platform as bootstrap

PREFIX = "/k8s/eks-demo"


def fake_secrets(existing):
    def invoke(_region, service, action, *args, payload=None):
        assert service == "secretsmanager"
        if action == "batch-get-secret-value":
            names = args[args.index("--secret-id-list") + 1:]
            assert len(names) <= 20
            return {"SecretValues": [{"Name": name, "SecretString": existing[name]} for name in names if name in existing],
                    "Errors": [{"SecretId": name, "ErrorCode": "ResourceNotFoundException"}
                               for name in names if name not in existing]}
        assert action == "create-secret" and set(payload) == {"Name", "SecretString"}
        return {}
    return invoke


def test_a_read_failure_other_than_a_missing_secret_stops_the_bootstrap():
    def refused(_region, _service, _action, *args, payload=None):
        names = args[args.index("--secret-id-list") + 1:]
        return {"SecretValues": [], "Errors": [{"SecretId": names[0], "ErrorCode": "AccessDeniedException"}]}
    with patch.object(bootstrap, "aws", side_effect=refused):
        with pytest.raises(RuntimeError, match="AccessDeniedException"):
            bootstrap.bootstrap("eks-demo", "ap-northeast-2")


def test_check_reports_missing_without_writes_or_fabricated_installation_ids(capsys):
    with patch.object(bootstrap, "aws", side_effect=fake_secrets({})) as aws:
        assert bootstrap.bootstrap("eks-demo", "ap-northeast-2") == 1
    assert all(call.args[2] == "batch-get-secret-value" for call in aws.call_args_list)
    output = capsys.readouterr().out
    for name in bootstrap.STUDIO_PROVIDED:
        assert f"{PREFIX}/agent-studio/{name}" in output
    for service in bootstrap.SERVICE_CLIENTS:
        assert f"{PREFIX}/{service}/oidc-client-id" in output
    assert "organization-id" not in output.replace(f"{PREFIX}/agent-forge/organization-id", "")
    desired = bootstrap.parameter_values(PREFIX, {})
    assert f"{PREFIX}/agent-memory/organization-id" not in desired
    assert f"{PREFIX}/agent-knowledge/organization-id" not in desired
    assert f"{PREFIX}/agent-forge/model-roles" not in desired
    # Agent services keep every parameter under the cluster prefix.
    assert all(name.startswith(f"{PREFIX}/") for name in desired)
    assert not any(f"{PREFIX}/agent-studio/{name}" in desired for name in bootstrap.STUDIO_PROVIDED)
    # Studio issues OIDC clients; random values would never match a registration.
    assert not any("oidc-client" in name for name in desired)
    assert not any(part in name for name in desired for part in ("delegat", "policy", "/credentials", "neo4j"))


def test_apply_preserves_db_password_and_creates_models_secrets_without_logging(capsys):
    password = "existing-password:escaped/@"
    existing = {f"{PREFIX}/agent-studio/postgres-password": password}
    with patch.object(bootstrap, "aws", side_effect=fake_secrets(existing)) as aws:
        assert bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True) == 0
    writes = [call.kwargs["payload"] for call in aws.call_args_list if call.args[2] == "create-secret"]
    values = {**existing, **{entry["Name"]: entry["SecretString"] for entry in writes}}
    assert values[f"{PREFIX}/agent-platform/database-url"].endswith(":5432/agent_platform")
    assert "existing-password%3Aescaped%2F%40@" in values[f"{PREFIX}/agent-platform/database-url"]
    assert not any(entry["Name"].endswith("/agent-studio/postgres-password") for entry in writes)
    keys = [values[f"{PREFIX}/{service}/aes-encryption-key"] for service in ("agent-studio", "agent-models")]
    for key in keys:
        assert len(base64.b64decode(key, validate=True)) == 32 and base64.b64encode(base64.b64decode(key)).decode() == key
    assert keys[0] != keys[1]
    for path in ("agent-studio/auth-secret", "agent-studio/schedule-scan-token", "agent-memory/auth-secret",
                 "agent-knowledge/auth-secret", "agent-forge/auth-secret", "agent-models/auth-secret"):
        assert len(values[f"{PREFIX}/{path}"]) >= 32
    output = capsys.readouterr().out
    assert all(value not in output for value in values.values())


def test_existing_parameters_are_never_rotated():
    values = bootstrap.parameter_values(PREFIX, {})
    with patch.object(bootstrap, "aws", side_effect=fake_secrets(values)) as aws:
        assert bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True) == 0
    assert all(call.args[2] == "batch-get-secret-value" for call in aws.call_args_list)


def test_inconsistent_existing_database_url_fails_before_any_write():
    existing = {f"{PREFIX}/agent-platform/database-url": "inconsistent"}
    with patch.object(bootstrap, "aws", side_effect=fake_secrets(existing)) as aws:
        with pytest.raises(RuntimeError, match="Inconsistent connection parameters"):
            bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True)
    assert all(call.args[2] == "batch-get-secret-value" for call in aws.call_args_list)


SOURCE = "/k8s/eks-demo-a"
TARGET = "/k8s/eks-demo"


def source_values():
    return {f"{SOURCE}/{path}": f"value-of-{path}" for path in bootstrap.CONTINUITY}


def test_copy_from_moves_continuity_values_and_generates_only_cluster_specific_ones(capsys):
    existing = {**source_values(), f"{TARGET}/agent-studio/postgres-password": "target-password"}
    with patch.object(bootstrap, "aws", side_effect=fake_secrets(existing)) as aws:
        assert bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True, copy_from="eks-demo-a") == 0
    writes = {call.kwargs["payload"]["Name"]: call.kwargs["payload"] for call in aws.call_args_list
              if call.args[2] == "create-secret"}
    for path in bootstrap.CONTINUITY:
        assert writes[f"{TARGET}/{path}"]["SecretString"] == f"value-of-{path}"
    assert all(name.startswith(TARGET) for name in writes)
    assert "target-password" in writes[f"{TARGET}/agent-platform/database-url"]["SecretString"]
    assert f"{TARGET}/agent-studio/postgres-password" not in writes
    assert writes[f"{TARGET}/agent-studio/metrics-bearer-token"]["SecretString"] not in source_values().values()
    output = capsys.readouterr().out
    assert all(entry["SecretString"] not in output for entry in writes.values())
    assert "Prepare from Studio" not in output


def test_copy_from_refuses_a_target_value_that_differs_from_the_source():
    existing = {**source_values(), f"{TARGET}/agent-models/aes-encryption-key": "another-key"}
    with patch.object(bootstrap, "aws", side_effect=fake_secrets(existing)) as aws:
        with pytest.raises(RuntimeError, match="agent-models/aes-encryption-key"):
            bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True, copy_from="eks-demo-a")
    assert all(call.args[2] == "batch-get-secret-value" for call in aws.call_args_list)


def test_copy_from_requires_every_source_value_before_any_write():
    existing = source_values()
    del existing[f"{SOURCE}/agent-forge/organization-id"]
    with patch.object(bootstrap, "aws", side_effect=fake_secrets(existing)) as aws:
        with pytest.raises(RuntimeError, match="agent-forge/organization-id"):
            bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True, copy_from="eks-demo-a")
    assert all(call.args[2] == "batch-get-secret-value" for call in aws.call_args_list)


def test_delivery_clusters_get_pool_credentials_with_a_matching_admin_url(capsys):
    with patch.object(bootstrap, "aws", side_effect=fake_secrets({})) as aws:
        assert bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True, delivery=True) == 0
    writes = {call.kwargs["payload"]["Name"]: call.kwargs["payload"]["SecretString"] for call in aws.call_args_list
              if call.args[2] == "create-secret"}
    admin = writes[f"{TARGET}/agent-runway/pool-admin-password"]
    assert writes[f"{TARGET}/agent-runway/pool-database-url"] == (
        f"postgresql://delivery_admin:{admin}@postgres.delivery-pool.svc.cluster.local:5432/postgres")
    assert len(writes[f"{TARGET}/agent-runway/auth-secret"]) >= 32
    assert len(writes[f"{TARGET}/agent-runway/metrics-bearer-token"]) >= 32
    assert writes[f"{TARGET}/delivery-pool/postgres-password"] != admin
    assert f"{TARGET}/agent-runway/oidc-client-id" in capsys.readouterr().out
    assert not any("oidc-client" in name for name in writes)


def test_clusters_without_delivery_get_no_delivery_parameters():
    assert not any("delivery" in name for name in bootstrap.parameter_values(TARGET, {}))


def test_rovr_clusters_get_generated_secrets_and_a_matching_database_url(capsys):
    prefix = "/k8s/eks-demo"
    existing = {f"{prefix}/rovr/postgres-password": "pw:/@"}
    with patch.object(bootstrap, "aws", side_effect=fake_secrets(existing)) as aws:
        assert bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True, rovr=True) == 0
    writes = {call.kwargs["payload"]["Name"]: call.kwargs["payload"]["SecretString"]
              for call in aws.call_args_list if call.args[2] == "create-secret"}
    url = writes[f"{prefix}/rovr/database-url"]
    assert url == "postgresql://rovr:pw%3A%2F%40@postgres.agent-studio.svc.cluster.local:5432/rovr?sslmode=disable"
    assert f"{prefix}/rovr/postgres-password" not in writes
    for name in ("session-secret", "run-token-secret", "metrics-bearer-token", "secret-key"):
        assert len(writes[f"{prefix}/rovr/{name}"]) >= 32
    # GitHub App and provider credentials are prepared by people, never generated.
    assert not any(name.endswith(("github-app-id", "github-client-secret", "anthropic-api-key")) for name in writes)
    output = capsys.readouterr().out
    assert f"{prefix}/rovr/github-app-private-key" in output
    assert "pw:/@" not in output


def test_clusters_without_rovr_get_no_rovr_parameters():
    assert not any("/rovr/" in name for name in bootstrap.parameter_values("/k8s/eks-demo-a", {}))

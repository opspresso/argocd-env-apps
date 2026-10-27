"""Secret bootstrap preserves identity and all existing credentials."""
import base64
from unittest.mock import patch
import pytest
import bootstrap_agent_platform as bootstrap

PREFIX = "/k8s/k3s-demo"


def fake_ssm(existing):
    def invoke(_region, service, action, *args, payload=None):
        if action == "get-parameters":
            names = args[args.index("--names") + 1:args.index("--with-decryption")]
            assert len(names) <= 10
            return {"Parameters": [{"Name": name, "Value": existing[name]} for name in names if name in existing]}
        assert service == "ssm" and action == "put-parameter"
        return {}
    return invoke


def test_check_reports_missing_without_writes_or_fabricated_installation_ids(capsys):
    with patch.object(bootstrap, "aws", side_effect=fake_ssm({})) as aws:
        assert bootstrap.bootstrap("k3s-demo", "ap-northeast-2") == 1
    assert all(call.args[2] == "get-parameters" for call in aws.call_args_list)
    output = capsys.readouterr().out
    assert f"{PREFIX}/agent-forge/model-roles" in output
    for service in bootstrap.SERVICE_CLIENTS:
        assert f"{PREFIX}/{service}/oidc-client-id" in output
    assert "organization-id" not in output.replace(f"{PREFIX}/agent-forge/organization-id", "")
    desired = bootstrap.parameter_values(PREFIX, {})
    assert f"{PREFIX}/agent-memory/organization-id" not in desired
    assert f"{PREFIX}/agent-librio/organization-id" not in desired
    assert f"{PREFIX}/agent-forge/model-roles" not in desired
    # Studio issues OIDC clients; random values would never match a registration.
    assert not any("oidc-client" in name for name in desired)
    assert not any(part in name for name in desired for part in ("delegat", "policy", "/credentials", "neo4j"))


def test_apply_preserves_db_password_and_creates_models_secrets_without_logging(capsys):
    password = "existing-password:escaped/@"
    existing = {f"{PREFIX}/agent-studio/postgres-password": password}
    with patch.object(bootstrap, "aws", side_effect=fake_ssm(existing)) as aws:
        assert bootstrap.bootstrap("k3s-demo", "ap-northeast-2", apply=True) == 0
    writes = [call.kwargs["payload"] for call in aws.call_args_list if call.args[2] == "put-parameter"]
    assert all(entry["Type"] == "SecureString" and entry["Overwrite"] is False for entry in writes)
    values = {**existing, **{entry["Name"]: entry["Value"] for entry in writes}}
    assert values[f"{PREFIX}/agent-platform/database-url"].endswith(":5432/agent_platform")
    assert "existing-password%3Aescaped%2F%40@" in values[f"{PREFIX}/agent-platform/database-url"]
    assert not any(entry["Name"].endswith("/agent-studio/postgres-password") for entry in writes)
    key = values[f"{PREFIX}/agent-models/encryption-key"]
    assert len(base64.b64decode(key, validate=True)) == 32 and base64.b64encode(base64.b64decode(key)).decode() == key
    assert len(values[f"{PREFIX}/agent-models/auth-secret"]) >= 32
    output = capsys.readouterr().out
    assert all(value not in output for value in values.values())


def test_existing_parameters_are_never_rotated():
    values = bootstrap.parameter_values(PREFIX, {})
    with patch.object(bootstrap, "aws", side_effect=fake_ssm(values)) as aws:
        assert bootstrap.bootstrap("k3s-demo", "ap-northeast-2", apply=True) == 0
    assert all(call.args[2] == "get-parameters" for call in aws.call_args_list)


def test_inconsistent_existing_database_url_fails_before_any_write():
    existing = {f"{PREFIX}/agent-platform/database-url": "inconsistent"}
    with patch.object(bootstrap, "aws", side_effect=fake_ssm(existing)) as aws:
        with pytest.raises(RuntimeError, match="Inconsistent connection parameters"):
            bootstrap.bootstrap("k3s-demo", "ap-northeast-2", apply=True)
    assert all(call.args[2] == "get-parameters" for call in aws.call_args_list)


SOURCE = "/k8s/k3s-demo"
TARGET = "/k8s/eks-demo"


def source_values():
    return {f"{SOURCE}/{path}": f"value-of-{path}" for path in bootstrap.CONTINUITY}


def test_copy_from_moves_continuity_values_and_generates_only_cluster_specific_ones(capsys):
    existing = {**source_values(), f"{TARGET}/agent-studio/postgres-password": "target-password"}
    with patch.object(bootstrap, "aws", side_effect=fake_ssm(existing)) as aws:
        assert bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True, copy_from="k3s-demo") == 0
    writes = {call.kwargs["payload"]["Name"]: call.kwargs["payload"] for call in aws.call_args_list
              if call.args[2] == "put-parameter"}
    for path in bootstrap.CONTINUITY:
        assert writes[f"{TARGET}/{path}"]["Value"] == f"value-of-{path}"
    assert all(entry["Overwrite"] is False and entry["Type"] == "SecureString" for entry in writes.values())
    assert all(name.startswith(TARGET) for name in writes)
    assert "target-password" in writes[f"{TARGET}/agent-platform/database-url"]["Value"]
    assert f"{TARGET}/agent-studio/postgres-password" not in writes
    assert writes[f"{TARGET}/agent-studio/metrics-bearer-token"]["Value"] not in source_values().values()
    output = capsys.readouterr().out
    assert all(entry["Value"] not in output for entry in writes.values())
    assert "Prepare from Studio" not in output


def test_copy_from_refuses_a_target_value_that_differs_from_the_source():
    existing = {**source_values(), f"{TARGET}/agent-models/encryption-key": "another-key"}
    with patch.object(bootstrap, "aws", side_effect=fake_ssm(existing)) as aws:
        with pytest.raises(RuntimeError, match="agent-models/encryption-key"):
            bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True, copy_from="k3s-demo")
    assert all(call.args[2] == "get-parameters" for call in aws.call_args_list)


def test_copy_from_requires_every_source_value_before_any_write():
    existing = source_values()
    del existing[f"{SOURCE}/agent-forge/organization-id"]
    with patch.object(bootstrap, "aws", side_effect=fake_ssm(existing)) as aws:
        with pytest.raises(RuntimeError, match="agent-forge/organization-id"):
            bootstrap.bootstrap("eks-demo", "ap-northeast-2", apply=True, copy_from="k3s-demo")
    assert all(call.args[2] == "get-parameters" for call in aws.call_args_list)

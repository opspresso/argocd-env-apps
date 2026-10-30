"""The rename preserves existing parameters and never overwrites a conflicting destination."""

import pytest
import copy_runway_parameters as migration


SOURCE = "/k8s/eks-demo/agent-delivery"
TARGET = "/k8s/eks-demo/agent-runway"


def fake_aws(store):
    def invoke(_region, service, action, payload):
        assert service == "ssm"
        if action == "describe-parameters":
            prefix = payload["ParameterFilters"][0]["Values"][0]
            return {"Parameters": [dict(Name=name, **entry["metadata"]) for name, entry in store.items()
                                   if name.startswith(prefix + "/")]}
        if action == "get-parameter":
            entry = store[payload["Name"]]
            return {"Parameter": {"Name": payload["Name"], "Value": entry["value"], "Version": entry["metadata"]["Version"]}}
        if action == "list-tags-for-resource":
            return {"TagList": store[payload["ResourceId"]]["tags"]}
        assert action == "put-parameter" and payload["Overwrite"] is False
        assert payload["Name"] not in store
        store[payload["Name"]] = {"value": payload["Value"], "tags": payload.get("Tags", []), "metadata": {
            "Type": payload["Type"], "Version": 1, "Tier": payload["Tier"], "KeyId": payload.get("KeyId"),
            "DataType": payload["DataType"], "Description": payload.get("Description"),
        }}
        return {"Version": 1}
    return invoke


def entry(value, version=1):
    return {"value": value, "tags": [{"Key": "owner", "Value": "platform"}], "metadata": {
        "Type": "SecureString", "Version": version, "Tier": "Standard", "KeyId": "alias/aws/ssm",
        "DataType": "text", "Description": None,
    }}


def test_copy_keeps_source_and_is_idempotent(monkeypatch):
    store = {f"{SOURCE}/auth-secret": entry("private fixture")}
    monkeypatch.setattr(migration, "aws", fake_aws(store))
    assert migration.copy_parameters("region", SOURCE, TARGET) == (1, 1)
    assert f"{TARGET}/auth-secret" not in store
    assert migration.copy_parameters("region", SOURCE, TARGET, True) == (1, 1)
    assert store[f"{TARGET}/auth-secret"] == store[f"{SOURCE}/auth-secret"]
    assert migration.copy_parameters("region", SOURCE, TARGET, True) == (1, 0)


def test_conflicting_destination_blocks_all_writes(monkeypatch):
    store = {f"{SOURCE}/a": entry("one"), f"{SOURCE}/b": entry("two"), f"{TARGET}/b": entry("different")}
    monkeypatch.setattr(migration, "aws", fake_aws(store))
    with pytest.raises(RuntimeError, match="no value was overwritten"):
        migration.copy_parameters("region", SOURCE, TARGET, True)
    assert f"{TARGET}/a" not in store
    assert store[f"{TARGET}/b"]["value"] == "different"


def test_history_is_not_silently_discarded(monkeypatch):
    store = {f"{SOURCE}/auth-secret": entry("private fixture", version=2)}
    monkeypatch.setattr(migration, "aws", fake_aws(store))
    with pytest.raises(RuntimeError, match="history or policy migration"):
        migration.copy_parameters("region", SOURCE, TARGET, True)
    assert f"{TARGET}/auth-secret" not in store

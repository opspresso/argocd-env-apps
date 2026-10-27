#!/usr/bin/env python3
"""Create missing platform secrets without rotating credentials or changing databases."""

import argparse
import base64
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
from urllib.parse import quote

import yaml


def aws(region, *arguments, payload=None):
    command = ["aws", "--region", region, "--output", "json", *arguments]
    if payload is None:
        result = subprocess.run(command, capture_output=True, text=True)
    else:
        # Private request files keep credentials out of argv and error output.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as request:
            json.dump(payload, request)
            request.flush()
            result = subprocess.run(command + ["--cli-input-json", f"file://{request.name}"],
                                    capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"AWS {' '.join(arguments[:2])} failed (exit {result.returncode})")
    return json.loads(result.stdout)


SERVICE_CLIENTS = ("agent-models", "agent-memory", "agent-librio", "agent-forge")

# Values that existing data or registrations depend on. A migrated database stays readable only when the
# new cluster uses the same keys, Forge organization, Studio-issued clients and Models role selections.
CONTINUITY = (
    "agent-forge/checkpoint-secret", "agent-forge/credential-secret", "agent-forge/model-secret",
    "agent-forge/session-secret", "agent-forge/organization-id", "agent-forge/model-roles",
    "agent-forge/github-client-id", "agent-forge/github-client-secret",
    "agent-models/auth-secret", "agent-models/encryption-key", "agent-librio/better-auth-secret",
    *(f"{service}/oidc-client-{part}" for service in SERVICE_CLIENTS for part in ("id", "secret")),
)


def parameter_values(prefix, existing):
    values = {}

    def secret(path, generate=lambda: secrets.token_hex(32)):
        key = f"{prefix}/{path}"
        values[key] = existing.get(key) or generate()
        return values[key]

    postgres = secret("agent-studio/postgres-password")
    values[f"{prefix}/agent-platform/database-url"] = (
        f"postgresql://agent_studio:{quote(postgres, safe='')}"
        "@postgres.agent-studio.svc.cluster.local:5432/agent_platform")
    for service in ("agent-studio", "agent-memory", "agent-librio", "agent-forge"):
        secret(f"{service}/metrics-bearer-token")
    for path in ("agent-librio/better-auth-secret", "agent-forge/checkpoint-secret",
                 "agent-forge/credential-secret", "agent-forge/model-secret", "agent-forge/session-secret",
                 "agent-models/auth-secret"):
        secret(path)
    # Models requires canonical base64 of exactly 32 bytes.
    secret("agent-models/encryption-key", lambda: base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    # Forge owns issuer-qualified principals; its organization is a configured namespace.
    # Memory/Librio organization IDs must instead come from their initialized installations.
    values[f"{prefix}/agent-forge/organization-id"] = existing.get(
        f"{prefix}/agent-forge/organization-id", f"agent-platform-{prefix.rsplit('/', 1)[-1]}")
    return values


def read_parameters(region, names):
    entries = []
    # SSM GetParameters accepts at most ten names per request.
    for offset in range(0, len(names), 10):
        response = aws(region, "ssm", "get-parameters", "--names", *names[offset:offset + 10], "--with-decryption")
        entries.extend(response["Parameters"])
    return {entry["Name"]: entry["Value"] for entry in entries}


def continuity_values(region, source, prefix):
    """Source values for CONTINUITY, keyed by target path; every source value must exist."""
    names = [f"/k8s/{source}/{path}" for path in CONTINUITY]
    values = read_parameters(region, names)
    missing = [name for name in names if name not in values]
    if missing:
        raise RuntimeError("Source cluster lacks continuity parameters: " + ", ".join(missing))
    return {f"{prefix}/{path}": values[f"/k8s/{source}/{path}"] for path in CONTINUITY}


def bootstrap(cluster, region, apply=False, copy_from=None):
    prefix = f"/k8s/{cluster}"
    copied = continuity_values(region, copy_from, prefix) if copy_from else {}
    names = list(dict.fromkeys([*parameter_values(prefix, {}), *copied]))
    existing = read_parameters(region, names)
    desired = {**parameter_values(prefix, {**copied, **existing}), **copied}
    mismatches = [name for name, value in existing.items() if value != desired[name]]
    if mismatches:
        raise RuntimeError("Inconsistent connection parameters; review without rotating: " + ", ".join(mismatches))
    missing = [name for name in names if name not in existing]
    for name in missing:
        if apply:
            aws(region, "ssm", "put-parameter", payload={
                "Name": name, "Value": desired[name], "Type": "SecureString", "Overwrite": False
            })
        source = f" (from {copy_from})" if name in copied else ""
        print(f"{'Created' if apply else 'Missing'}: {name}{source}")
    print(f"{len(existing)} existing, {len(missing)} {'created' if apply else 'missing'} parameters")
    if copy_from:
        return 0 if apply or not missing else 1
    # Studio issues each service client, and Forge roles name central Models selections.
    # Operators prepare these after registration; they are never random secret material.
    for service in SERVICE_CLIENTS:
        print(f"Prepare from Studio Platform connections: {prefix}/{service}/oidc-client-id, oidc-client-secret")
    print(f"Prepare and validate separately: {prefix}/agent-forge/model-roles")
    return 0 if apply or not missing else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", default="k3s-demo")
    parser.add_argument("--apply", action="store_true", help="Create missing SecureStrings; never overwrite")
    parser.add_argument("--copy-from", metavar="CLUSTER",
                        help="Copy data-continuity values (keys, Forge organization, OIDC clients) from this cluster")
    args = parser.parse_args()
    if args.copy_from == args.cluster:
        parser.error("--copy-from must name a different cluster")
    env_path = Path(__file__).resolve().parents[1] / "env" / f"{args.cluster}.yaml"
    env = yaml.safe_load(env_path.read_text())
    if env.get("env") not in {"eks", "k3s"} or env.get("cluster") != args.cluster:
        parser.error("Select a cluster declared in env/<cluster>.yaml")
    account = aws(env["aws_region"], "sts", "get-caller-identity")["Account"]
    if account != str(env["aws_account_id"]):
        parser.error("AWS account does not match the selected cluster")
    return bootstrap(args.cluster, env["aws_region"], args.apply, args.copy_from)


if __name__ == "__main__":
    raise SystemExit(main())

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


SERVICE_CLIENTS = ("agent-models", "agent-memory", "agent-knowledge", "agent-forge")

# Values that existing data or registrations depend on. A migrated database stays readable only when the
# new cluster uses the same keys, Forge organization and Studio-issued clients.
CONTINUITY = (
    "agent-studio/auth-secret", "agent-studio/aes-encryption-key", "agent-memory/auth-secret",
    "agent-forge/checkpoint-secret", "agent-forge/model-secret",
    "agent-forge/auth-secret", "agent-forge/organization-id",
    "agent-models/auth-secret", "agent-models/aes-encryption-key", "agent-knowledge/auth-secret",
    *(f"{service}/oidc-client-{part}" for service in SERVICE_CLIENTS for part in ("id", "secret")),
)

# Studio parameters that come from the Google OAuth client, the plugins repository and the Git hosts' OAuth
# applications, never generated.
STUDIO_PROVIDED = ("google-client-id", "google-client-secret", "plugins-repo-token", "git-host-secrets")


# rovr parameters that come from the GitHub App and model providers, never generated.
ROVR_PROVIDED = ("github-app-id", "github-app-private-key", "github-client-id", "github-client-secret",
                 "github-webhook-secret", "anthropic-api-key")


def parameter_values(prefix, existing, delivery=False, rovr=False):
    values = {}

    def secret(path, generate=lambda: secrets.token_hex(32)):
        key = f"{prefix}/{path}"
        values[key] = existing.get(key) or generate()
        return values[key]

    postgres = secret("agent-studio/postgres-password")
    values[f"{prefix}/agent-platform/database-url"] = (
        f"postgresql://agent_studio:{quote(postgres, safe='')}"
        "@postgres.agent-studio.svc.cluster.local:5432/agent_platform")
    for service in ("agent-studio", "agent-memory", "agent-knowledge", "agent-forge"):
        secret(f"{service}/metrics-bearer-token")
    for path in ("agent-studio/auth-secret", "agent-studio/schedule-scan-token", "agent-memory/auth-secret",
                 "agent-knowledge/auth-secret", "agent-forge/checkpoint-secret",
                 "agent-forge/model-secret", "agent-forge/auth-secret", "agent-models/auth-secret"):
        secret(path)
    # Studio and Models require canonical base64 of exactly 32 bytes.
    for path in ("agent-studio/aes-encryption-key", "agent-models/aes-encryption-key"):
        secret(path, lambda: base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    # Forge owns issuer-qualified principals; its organization is a configured namespace.
    # Memory/Knowledge organization IDs must instead come from their initialized installations.
    values[f"{prefix}/agent-forge/organization-id"] = existing.get(
        f"{prefix}/agent-forge/organization-id", f"agent-platform-{prefix.rsplit('/', 1)[-1]}")
    if delivery:
        secret("agent-runway/auth-secret")
        secret("agent-runway/metrics-bearer-token")
        secret("delivery-pool/postgres-password")
        # Delivery administers the pool as a non-superuser; the pool bootstrap sets this password.
        admin = secret("agent-runway/pool-admin-password")
        values[f"{prefix}/agent-runway/pool-database-url"] = (
            f"postgresql://delivery_admin:{quote(admin, safe='')}"
            "@postgres.delivery-pool.svc.cluster.local:5432/postgres")
    if rovr:
        # rovr owns a database and role on the shared PostgreSQL; its chart's bootstrap Job sets this password.
        password = secret("rovr/postgres-password")
        values[f"{prefix}/rovr/database-url"] = (
            f"postgresql://rovr:{quote(password, safe='')}"
            "@postgres.agent-studio.svc.cluster.local:5432/rovr?sslmode=disable")
        # secret-key seals the provider keys accounts store; replacing it makes them unreadable.
        for path in ("rovr/session-secret", "rovr/run-token-secret", "rovr/metrics-bearer-token", "rovr/secret-key"):
            secret(path)
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


def bootstrap(cluster, region, apply=False, copy_from=None, delivery=False, rovr=False):
    prefix = f"/k8s/{cluster}"
    copied = continuity_values(region, copy_from, prefix) if copy_from else {}
    names = list(dict.fromkeys([*parameter_values(prefix, {}, delivery, rovr), *copied]))
    existing = read_parameters(region, names)
    desired = {**parameter_values(prefix, {**copied, **existing}, delivery, rovr), **copied}
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
    print("Prepare from the Google OAuth client (when Google sign-in is enabled), the plugins repository and "
          "the Git hosts' OAuth applications (when agent_studio.git_hosts is set): "
          + ", ".join(f"{prefix}/agent-studio/{name}" for name in STUDIO_PROVIDED))
    if rovr:
        print("Prepare from the rovr GitHub App and model providers: "
              + ", ".join(f"{prefix}/rovr/{name}" for name in ROVR_PROVIDED))
    if copy_from:
        if delivery:
            print(f"Prepare from Studio Platform connections: {prefix}/agent-runway/oidc-client-id, oidc-client-secret")
        return 0 if apply or not missing else 1
    # Studio issues each service client. Operators prepare these after registration; they are never random.
    for service in SERVICE_CLIENTS + (("agent-runway",) if delivery else ()):
        print(f"Prepare from Studio Platform connections: {prefix}/{service}/oidc-client-id, oidc-client-secret")
    return 0 if apply or not missing else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--apply", action="store_true", help="Create missing SecureStrings; never overwrite")
    parser.add_argument("--copy-from", metavar="CLUSTER",
                        help="Copy data-continuity values (keys, Forge organization, OIDC clients) from this cluster")
    args = parser.parse_args()
    if args.copy_from == args.cluster:
        parser.error("--copy-from must name a different cluster")
    env_path = Path(__file__).resolve().parents[1] / "env" / f"{args.cluster}.yaml"
    env = yaml.safe_load(env_path.read_text())
    if env.get("env") != "eks" or env.get("cluster") != args.cluster:
        parser.error("Select a cluster declared in env/<cluster>.yaml")
    account = aws(env["aws_region"], "sts", "get-caller-identity")["Account"]
    if account != str(env["aws_account_id"]):
        parser.error("AWS account does not match the selected cluster")
    return bootstrap(args.cluster, env["aws_region"], args.apply, args.copy_from, "agent_runway" in env, "rovr" in env)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Copy version-one Delivery parameters to Runway without rotating or deleting them."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import yaml


def aws(region, service, action, payload):
    # A private, automatically removed request file keeps values out of argv and logs.
    command = ["aws", "--region", region, "--output", "json", service, action,
               "--cli-input-json"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as request:
        json.dump(payload, request)
        request.flush()
        result = subprocess.run(command + [f"file://{request.name}"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"AWS {service} {action} failed (exit {result.returncode})")
    return json.loads(result.stdout or "{}")


def parameters(region, prefix):
    response = aws(region, "ssm", "describe-parameters", {"ParameterFilters": [
        {"Key": "Path", "Option": "Recursive", "Values": [prefix]},
    ]})
    return {item["Name"]: item for item in response["Parameters"]}


def get(region, name):
    return aws(region, "ssm", "get-parameter", {"Name": name, "WithDecryption": True})["Parameter"]


def tags(region, name):
    return sorted(aws(region, "ssm", "list-tags-for-resource", {"ResourceType": "Parameter", "ResourceId": name})["TagList"],
                  key=lambda item: (item["Key"], item["Value"]))


def copy_parameters(region, source, destination, apply=False):
    source_entries = parameters(region, source)
    existing = parameters(region, destination)
    plans = []
    for name, meta in sorted(source_entries.items()):
        if not name.startswith(source + "/"):
            raise RuntimeError("SSM returned a parameter outside the source prefix")
        target = destination + name[len(source):]
        # SSM cannot preserve historical versions through PutParameter. Refuse to silently discard them.
        if meta.get("Version") != 1 or meta.get("Policies") or meta.get("Type") != "SecureString":
            raise RuntimeError(f"Parameter needs a separate history or policy migration: {name}")
        value = get(region, name)
        if value["Version"] != meta["Version"]:
            raise RuntimeError(f"Source parameter changed during inspection: {name}")
        source_tags = tags(region, name)
        target_meta = existing.get(target)
        if target_meta:
            target_value = get(region, target)
            if (target_meta.get("Version") != 1 or target_meta.get("Policies")
                    or any(target_meta.get(key) != meta.get(key) for key in ("Type", "Tier", "KeyId", "DataType", "Description"))
                    or target_value["Value"] != value["Value"] or tags(region, target) != source_tags):
                raise RuntimeError(f"Destination parameter differs; no value was overwritten: {target}")
        else:
            plans.append((name, target, meta, value["Value"], source_tags))

    # All conflicts are checked before the first write. Overwrite stays false.
    for _, target, meta, value, source_tags in plans:
        if apply:
            request = {"Name": target, "Value": value, "Type": meta["Type"], "Overwrite": False,
                       "Tier": meta.get("Tier", "Standard"), "DataType": meta.get("DataType", "text")}
            if meta.get("KeyId"):
                request["KeyId"] = meta["KeyId"]
            if meta.get("Description"):
                request["Description"] = meta["Description"]
            if source_tags:
                request["Tags"] = source_tags
            aws(region, "ssm", "put-parameter", request)
    if apply:
        # A concurrent source update must block cutover; never print the values.
        for name, target, _, value, _ in plans:
            if get(region, name)["Value"] != value or get(region, target)["Value"] != value:
                raise RuntimeError(f"Parameter changed while copying: {name}")
        if parameters(region, source) != source_entries or copy_parameters(region, source, destination)[1]:
            raise RuntimeError("Parameter set changed while copying; inspect before cutover")
    return len(source_entries), len(plans)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    env = yaml.safe_load((Path(__file__).resolve().parents[1] / "env" / f"{args.cluster}.yaml").read_text())
    if env.get("cluster") != args.cluster or env.get("env") != "eks":
        parser.error("select an EKS cluster declared in env/<cluster>.yaml")
    region = env["aws_region"]
    if aws(region, "sts", "get-caller-identity", {})["Account"] != str(env["aws_account_id"]):
        parser.error("AWS account does not match the selected cluster")
    for source, destination in ((f"/k8s/{args.cluster}/agent-delivery", f"/k8s/{args.cluster}/agent-runway"),
                                (f"/agent-delivery/{args.cluster}", f"/agent-runway/{args.cluster}")):
        count, pending = copy_parameters(region, source, destination, args.apply)
        print(f"{source}: {count} source parameters; {pending} {'created' if args.apply else 'to copy'}")


if __name__ == "__main__":
    main()

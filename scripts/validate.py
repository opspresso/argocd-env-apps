#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render every Application or ApplicationSet the way Argo CD would, and fail on any error.

Reads apps/**/*.yaml, expands the git files generator against the env files it
names, and runs `helm template` with the exact valueFiles Argo CD passes -
including values-<phase>.yaml, whose phase comes from the env file. A broken
template or a missing values file then fails CI instead of surfacing as a
failed sync on the cluster.

Also enforces that every <platform>/values-<cluster>.yaml has a values-template.yaml.j2
to come from - a hand-written one is indistinguishable from a render and drifts
without anyone noticing.

    ./scripts/validate.py                 # every Application or ApplicationSet in apps/
    ./scripts/validate.py -r agent-studio  # one chart

A chart with no Application or ApplicationSet in apps/ is not deployed and is
not checked.
"""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

from workload_policy import policy_errors, target_platform
from chart import discover_phases


APPSET_DIR = "apps"
CHARTS_DIR = "charts"

TEMPLATE = "values-template.yaml.j2"
VALUES_RE = re.compile(r"^values-.+\.yaml$")

# ApplicationSet substitutes {{key}} from the env file the generator matched.
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def parse_args():
    p = argparse.ArgumentParser(description="Helm render check")
    p.add_argument("-r", "--reponame", help="only this chart")
    p.add_argument("--all-charts", action="store_true", help="also render undeployed charts and generated cluster values")
    p.add_argument(
        "-d", "--dir", action="append", dest="dirs",
        help="ApplicationSet directory (repeatable, default: {})".format(APPSET_DIR),
    )
    return p.parse_args()


def expand(text, env):
    def replace(match):
        key = match.group(1)
        if key not in env:
            raise KeyError("{{%s}} is not in the env file" % key)
        return str(env[key])

    return PLACEHOLDER.sub(replace, text)


def load_targets(dirs):
    """One entry per Application or ApplicationSet: chart and value files."""
    targets = []

    for directory in dirs:
        if not os.path.isdir(directory):
            raise ValueError("no application directory: {}".format(directory))
        for current, _, names in os.walk(directory):
            for name in sorted(names):
                if not name.endswith(".yaml"):
                    continue

                path = os.path.join(current, name)
                with open(path, "r") as file:
                    doc = yaml.safe_load(file)

                if not doc or doc.get("kind") not in {"Application", "ApplicationSet"}:
                    continue

                if doc["kind"] == "ApplicationSet":
                    template = doc["spec"]["template"]
                    source = template["spec"]["source"]
                    env_files = [
                        entry["path"]
                        for generator in doc["spec"]["generators"]
                        for entry in generator["git"]["files"]
                    ]
                    if not env_files:
                        raise ValueError("no environment files in {}".format(path))
                    name = template["metadata"]["name"]
                    cluster = (template["metadata"].get("labels") or {}).get("opspresso.com/cluster")
                    namespace = template["spec"]["destination"]["namespace"]
                else:
                    source = doc["spec"]["source"]
                    env_files = [None]
                    name = doc["metadata"]["name"]
                    cluster = (doc["metadata"].get("labels") or {}).get("opspresso.com/cluster")
                    namespace = doc["spec"]["destination"]["namespace"]

                targets.append({
                    "appset": path,
                    "chart": source["path"],
                    "value_files": source.get("helm", {}).get("valueFiles", []),
                    "env_files": env_files,
                    "name": name,
                    "namespace": namespace,
                    "cluster": cluster,
                })

    return targets


def check_templates(only=None):
    """Every <platform>/values-<cluster>.yaml has to be a render, never hand-written.

    A hand-maintained one looks exactly like a generated one, so it survives
    every build while quietly drifting from the template beside it.
    """
    failures = []

    for name in sorted(os.listdir(CHARTS_DIR)):
        chart = os.path.join(CHARTS_DIR, name)

        if only and name != only:
            continue

        if not os.path.isdir(chart):
            continue
        local_templates = [path for path in (Path(chart) / "templates").rglob("*") if path.is_file()]
        if local_templates:
            failures.append((chart, "wrapper templates are not allowed; use app, cronjob, vendor charts or raw dependencies"))
        if os.path.exists(os.path.join(chart, TEMPLATE)):
            continue

        for entry in sorted(os.listdir(chart)):
            directory = os.path.join(chart, entry)

            if not os.path.isdir(directory):
                continue

            found = sorted(f for f in os.listdir(directory) if VALUES_RE.match(f))

            if found:
                failures.append((
                    directory,
                    "{} has no {}, so {} cannot be regenerated".format(
                        chart, TEMPLATE, ", ".join(found)
                    ),
                ))

    return failures


def additional_targets(targets):
    """Cover every wrapper and generated environment beyond deployed Applications."""
    covered = set()
    namespaces = {}
    for target in targets:
        namespaces[target["chart"]] = target["namespace"]
        for env_file in target["env_files"]:
            env = yaml.safe_load(Path(env_file).read_text()) if env_file else {}
            covered.add((target["chart"], tuple(expand(value, env) for value in target["value_files"])))
    extra = []
    for definition in sorted(Path(CHARTS_DIR).glob("*/Chart.yaml")):
        chart = definition.parent
        phases = discover_phases(".", chart.name)
        environments = sorted(path for platform in ("eks", "local")
                              for path in (chart / platform).glob("values-*.yaml"))
        variants = []
        for environment in environments:
            cluster = environment.stem.removeprefix("values-")
            config = yaml.safe_load((Path("env") / f"{cluster}.yaml").read_text())
            files = ["values.yaml"]
            if phases:
                files.append(f"values-{config['phase']}.yaml")
            files.append(str(environment.relative_to(chart)))
            variants.append((cluster, files, str(environment)))
        if not environments:
            variants = [(None, ["values.yaml"] + ([f"values-{phase}.yaml"] if phase else []), str(chart))
                        for phase in phases or [None]]
        for cluster, files, source in variants:
            if (str(chart), tuple(files)) in covered:
                continue
            extra.append({"chart": str(chart), "name": f"{chart.name}-{cluster}" if cluster else chart.name,
                          "namespace": namespaces.get(str(chart), chart.name), "cluster": cluster,
                          "appset": source, "value_files": files, "env_files": [None]})
    return extra


def update_dependencies(chart):
    """Fetch the upstream charts pinned in Chart.yaml."""
    print("# deps", chart, flush=True)

    result = subprocess.run(
        ["helm", "dependency", "update", chart], capture_output=True, text=True
    )

    if result.returncode != 0:
        return result.stderr.strip() or result.stdout.strip()

    return None


def render(target, env_file):
    """helm template one Application or ApplicationSet target."""
    env = {}
    if env_file:
        with open(env_file, "r") as file:
            env = yaml.safe_load(file)
        if not isinstance(env, dict):
            return "environment file must contain a mapping: {}".format(env_file)

    args = ["helm", "template", expand(target["name"], env), target["chart"]]
    args += ["--namespace", expand(target["namespace"], env)]

    prefix_present = False
    prefix = None
    value_clusters = set()
    for value_file in target["value_files"]:
        resolved = Path(expand(value_file, env))
        path = os.path.join(target["chart"], resolved)

        # Argo CD fails the sync when a listed valueFile is missing, so a
        # chart that was never rendered has to fail here too.
        if not os.path.exists(path):
            return "missing values file: {}".format(path)

        try:
            with open(path) as file:
                values = yaml.safe_load(file) or {}
        except yaml.YAMLError:
            return f"invalid YAML values file: {path}"
        if not isinstance(values, dict):
            return f"values file must contain a mapping: {path}"
        global_values = values.get("global") or {}
        if not isinstance(global_values, dict):
            return f"global values must contain a mapping: {path}"
        if "ssmPrefix" in global_values:
            prefix_present = True
            prefix = global_values["ssmPrefix"]
        elif "ssmPrefix" in values:
            prefix_present = True
            prefix = values["ssmPrefix"]
        if resolved.parent.name in {"eks", "local"} and resolved.stem.startswith("values-"):
            value_clusters.add(resolved.stem.removeprefix("values-"))
        args += ["-f", path]

    if prefix_present:
        cluster = env.get("cluster") or expand(target.get("cluster") or "", env)
        if not cluster and len(value_clusters) == 1:
            cluster = next(iter(value_clusters))
        if not cluster or len(value_clusters) > 1:
            return "ssmPrefix requires exactly one cluster context"
        if value_clusters and cluster not in value_clusters:
            return f"cluster values must match the target cluster: {cluster}"
        if prefix != f"/k8s/{cluster}":
            return f"ssmPrefix must match the target cluster: /k8s/{cluster}"

    result = subprocess.run(args, capture_output=True, text=True)

    if result.returncode != 0:
        return result.stderr.strip() or result.stdout.strip()

    errors = policy_errors(result.stdout, target_platform(target, env))
    return "\n".join(errors) if errors else None


def main():
    args = parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)

    try:
        targets = load_targets(args.dirs or [APPSET_DIR])
        if args.all_charts:
            targets += additional_targets(targets)
    except (ValueError, KeyError, OSError, yaml.YAMLError) as error:
        print("FAIL {}".format(error))
        return 1
    if not targets:
        print("no Application or ApplicationSet targets found")
        return 1

    if args.reponame:
        wanted = "charts/{}".format(args.reponame)
        targets = [t for t in targets if t["chart"] == wanted]

        if not targets:
            print("no ApplicationSet uses {}".format(wanted))
            return 1

    failures = check_templates(args.reponame)
    rendered = 0
    prepared = {}

    for target in targets:
        if target["chart"] not in prepared:
            prepared[target["chart"]] = update_dependencies(target["chart"])

            if prepared[target["chart"]]:
                failures.append((target["chart"], prepared[target["chart"]]))
        if prepared[target["chart"]]:
            continue

        for env_file in target["env_files"]:
            print("# render {} {}".format(target["appset"], env_file or "direct"), flush=True)

            try:
                error = render(target, env_file)
            except (ValueError, KeyError, IOError, yaml.YAMLError) as exception:
                error = str(exception)

            rendered += 1

            if error:
                failures.append(
                    ("{} {}".format(target["appset"], env_file), error)
                )

    print("\n{} renders, {} failures".format(rendered, len(failures)))

    for where, why in failures:
        print("\nFAIL {}\n{}".format(where, why))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

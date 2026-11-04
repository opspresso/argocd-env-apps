"""local omits compute reservations; EKS requires explicit resource budgets."""

from pathlib import Path

import yaml
import re


PLATFORMS = {"eks", "local"}
UNRESERVED_PLATFORMS = {"local"}
AUTOSCALERS = {"HorizontalPodAutoscaler", "VerticalPodAutoscaler", "ScaledObject", "ScaledJob"}
VM_WORKLOADS = {"VMAgent", "VMAlert", "VMAlertmanager", "VMAuth", "VMCluster", "VMSingle"}


def target_platform(target, env):
    if env.get("env"):
        return env["env"]
    return next((part for part in reversed(Path(target["appset"]).parts[:-1]) if part in PLATFORMS), "eks")


def policy_errors(manifests, platform):
    if platform == "eks":
        return production_resource_errors(manifests)
    if platform not in UNRESERVED_PLATFORMS:
        return []
    errors = []

    def inspect(value, owner, path="", vm_workload=False):
        if isinstance(value, list):
            for index, child in enumerate(value):
                inspect(child, owner, f"{path}[{index}]", vm_workload)
        elif isinstance(value, dict):
            for key, child in value.items():
                location = f"{path}.{key}" if path else key
                if vm_workload and key in {"resources", "configReloaderResources"} and not isinstance(child, dict):
                    errors.append(f"{owner} {location} must be an object when present")
                if (key == "resources" or key.endswith("Resources")) and isinstance(child, dict):
                    for field in ("requests", "limits"):
                        quantities = child.get(field) or {}
                        if isinstance(quantities, dict) and any(name != "storage" for name in quantities):
                            errors.append(f"{owner} {location}.{field} reserves compute")
                if key in {"autoscaling", "hpa", "vpa"} and isinstance(child, dict) and child.get("enabled") is True:
                    errors.append(f"{owner} {location}.enabled must be false")
                if key == "autoscaleEnabled" and child is True:
                    errors.append(f"{owner} {location} must be false")
                inspect(child, owner, location, vm_workload)

    for document in yaml.safe_load_all(manifests):
        if not document or document.get("kind") == "CustomResourceDefinition":
            continue
        kind = document.get("kind")
        owner = f"{kind}/{document.get('metadata', {}).get('name', '?')}"
        if kind in AUTOSCALERS:
            errors.append(f"{owner} is not allowed on {platform}")
        if kind == "ResourceQuota":
            for name in document.get("spec", {}).get("hard", {}):
                # Compute quotas force dynamic Pods to declare resources too.
                if (name in {"cpu", "memory", "ephemeral-storage"}
                        or (name.startswith(("requests.", "limits.")) and name != "requests.storage")):
                    errors.append(f"{owner} spec.hard.{name} requires compute resources on {platform}")
        if kind == "VMCluster":
            for component in ("vminsert", "vmselect", "vmstorage"):
                config = document.get("spec", {}).get(component)
                if isinstance(config, dict) and config.get("useDefaultResources") is not False:
                    errors.append(f"{owner} spec.{component}.useDefaultResources must be false")
        elif kind in VM_WORKLOADS and document.get("spec", {}).get("useDefaultResources") is not False:
            errors.append(f"{owner} spec.useDefaultResources must be false")
        inspect(document, owner, vm_workload=kind in VM_WORKLOADS)
    return errors

def production_resource_errors(manifests):
    """Check Pod specs and operator resources, never arbitrary ConfigMap data."""
    errors = []

    def positive(quantity):
        if isinstance(quantity, bool):
            return False
        match = re.fullmatch(r"([+]?(?:\d+(?:\.\d*)?|\.\d+))(?:(?:[eE][+-]?\d+)|[KMGTPE]i|[numkMGTPE])?", str(quantity))
        return bool(match and float(match.group(1)) > 0)

    def budget(resources, owner, path):
        resources = resources or {}
        if not isinstance(resources, dict):
            errors.append(f"{owner} {path} must be an object")
            return
        for section, resource in (("requests", "cpu"), ("requests", "memory"), ("limits", "memory")):
            quantities = resources.get(section) or {}
            if not isinstance(quantities, dict) or not positive(quantities.get(resource)):
                errors.append(f"{owner} {path}.{section}.{resource} must reserve a positive EKS budget")

    def at(value, *keys):
        for key in keys:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    def pod(spec, owner, path):
        if spec is None:
            return
        if not isinstance(spec, dict):
            errors.append(f"{owner} {path} must be a Pod spec object")
            return
        for field in ("containers", "initContainers"):
            containers = spec.get(field) or []
            if not isinstance(containers, list):
                errors.append(f"{owner} {path}.{field} must be a list")
                continue
            for index, container in enumerate(containers):
                location = f"{path}.{field}[{index}]"
                if not isinstance(container, dict):
                    errors.append(f"{owner} {location} must be a container object")
                else:
                    budget(container.get("resources"), owner, location + ".resources")

    for document in yaml.safe_load_all(manifests):
        if not document or document.get("kind") == "CustomResourceDefinition":
            continue
        kind = document.get("kind")
        owner = f"{kind}/{document.get('metadata', {}).get('name', '?')}"
        if kind in {"Prometheus", "Alertmanager", "ThanosRuler"}:
            budget(at(document, "spec", "resources"), owner, "spec.resources")
            pod(document.get("spec"), owner, "spec")
        elif kind == "Pod":
            pod(document.get("spec"), owner, "spec")
        pod(at(document, "spec", "template", "spec"), owner, "spec.template.spec")
        if kind == "PodTemplate":
            pod(at(document, "template", "spec"), owner, "template.spec")
        if kind == "CronJob":
            pod(at(document, "spec", "jobTemplate", "spec", "template", "spec"), owner, "spec.jobTemplate.spec.template.spec")
        if kind in {"AnalysisTemplate", "ClusterAnalysisTemplate"}:
            for index, metric in enumerate(at(document, "spec", "metrics") or []):
                pod(at(metric, "provider", "job", "spec", "template", "spec"), owner,
                    f"spec.metrics[{index}].provider.job.spec.template.spec")
    return errors

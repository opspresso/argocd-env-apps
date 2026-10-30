#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
from ipaddress import ip_network
import yaml

from jinja2 import Environment, FileSystemLoader, StrictUndefined


PLATFORM = "eks"
PLATFORMS = {"eks", "k3s", "local"}


def parse_args():
    p = argparse.ArgumentParser(description="Helm chart gen")
    p.add_argument("-r", "--reponame", required=True, help="chart name")
    p.add_argument("-p", "--platform", choices=["eks", "k3s", "local"], default=PLATFORM)
    return p.parse_args()


def validate_agent_oidc(context):
    """Studio alone connects to an external IdP; the other services sign in through Studio."""
    for service in ("memory", "knowledge", "forge", "models"):
        settings = context.get(f"agent_{service}")
        if isinstance(settings, dict):
            for key in ("oidc", "google_sign_in", "sign_in_domains"):
                if key in settings:
                    raise ValueError(f"agent_{service}.{key} is not supported; the service signs in through Studio OIDC")
    settings = context.get("agent_studio")
    if not isinstance(settings, dict):
        return
    if "google_sign_in" in settings and not isinstance(settings["google_sign_in"], bool):
        raise ValueError("agent_studio.google_sign_in must be a boolean")
    oidc = settings.get("oidc")
    if oidc is None:
        return
    if not isinstance(oidc, dict):
        raise ValueError("agent_studio.oidc must be a mapping")
    for key in ("issuer", "client_id_parameter", "client_secret_parameter"):
        if not isinstance(oidc.get(key), str) or not oidc[key].strip():
            raise ValueError(f"agent_studio.oidc.{key} is required")
    if not isinstance(oidc.get("profile_claims", {}), dict):
        raise ValueError("agent_studio.oidc.profile_claims must be a mapping")
    mode = oidc.get("logout_mode", "app")
    if not isinstance(mode, str) or mode not in {"app", "provider"}:
        raise ValueError("agent_studio.oidc.logout_mode is not supported")


def gen_repos(args):
    template_name = "values-template.yaml.j2"
    template_path = "charts/{}/{}".format(args.reponame, template_name)

    if not os.path.isfile(template_path):
        raise FileNotFoundError("template not found: {}".format(template_path))
    print("# gen_values", template_path)

    e = Environment(
        loader=FileSystemLoader("charts/{}/".format(args.reponame)),
        undefined=StrictUndefined,
    )
    t = e.get_template(template_name)

    gen_values(t, args.reponame, args.platform)


def gen_values(t, reponame, platform=PLATFORM):
    rendered = []
    for env_file in sorted(os.listdir("env")):
        if env_file.endswith(".yaml"):
            env_path = "env/{}".format(env_file)

            with open(env_path, "r") as file:
                v = yaml.safe_load(file)

                # The ApplicationSet reads "{{env}}/values-{{cluster}}.yaml",
                # so the directory has to follow the env field.
                if not isinstance(v, dict) or v.get("env") not in PLATFORMS:
                    raise ValueError("{} must declare a supported env".format(env_path))
                if v.get("cluster") != os.path.splitext(env_file)[0]:
                    raise ValueError("{} cluster must match its filename".format(env_path))
                for key in ("resources", "autoscaling", "agent_studio_maintenance", "agent_memory_maintenance", "agent_knowledge_maintenance", "agent_forge_maintenance", "agent_models_maintenance", "agent_runway_maintenance", "storage_gc_maintenance"):
                    if key in v and not isinstance(v[key], bool):
                        raise ValueError("{} {} must be a boolean".format(env_path, key))
                if v.get("storage_gc_maintenance"):
                    paused = all(v.get(f"agent_{service}_maintenance") is True
                                 for service in ("studio", "memory", "knowledge", "forge"))
                    if v.get("env") != "k3s" or not paused or v.get("minio", {}).get("console") is not False:
                        raise ValueError("storage_gc_maintenance requires k3s, all Agent services paused and minio.console false")
                validate_agent_oidc(v)
                proxies = v.get("trusted_proxy_cidrs", [])
                if not isinstance(proxies, list):
                    raise ValueError("{} trusted_proxy_cidrs must be a list".format(env_path))
                for proxy in proxies:
                    if not isinstance(proxy, str):
                        raise ValueError("{} trusted_proxy_cidrs must contain IP/CIDR strings".format(env_path))
                    try:
                        ip_network(proxy, strict=False)
                    except ValueError as error:
                        raise ValueError("{} trusted_proxy_cidrs must contain valid IP/CIDR ranges".format(env_path)) from error
                metrics = v.get("metrics")
                if metrics is not None and (
                    not isinstance(metrics, dict)
                    or metrics.get("backend") not in {"prometheus", "grafana-cloud"}
                ):
                    raise ValueError("{} metrics.backend must be prometheus or grafana-cloud".format(env_path))

                if v.get("env") != platform:
                    continue

                d = t.render(v)
                if not isinstance(yaml.safe_load(d), dict):
                    raise ValueError("{} must render a values mapping".format(env_path))

                save_root = "charts/{}/{}".format(reponame, platform)
                save_path = "{}/values-{}".format(save_root, env_file)
                rendered.append((save_path, d))

    # Validate every selected environment before replacing any generated file.
    for save_path, content in rendered:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "w") as file:
            print("# save", save_path)
            file.write(content)


def main():
    args = parse_args()

    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    gen_repos(args)


if __name__ == "__main__":
    main()

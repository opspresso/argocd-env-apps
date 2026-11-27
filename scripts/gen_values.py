#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
from ipaddress import ip_network
import yaml

from jinja2 import Environment, FileSystemLoader, StrictUndefined


PLATFORM = "eks"
PLATFORMS = {"eks", "local"}


def parse_args():
    p = argparse.ArgumentParser(description="Helm chart gen")
    p.add_argument("-r", "--reponame", required=True, help="chart name")
    p.add_argument("-p", "--platform", choices=["eks", "local"], default=PLATFORM)
    return p.parse_args()


def validate_agentops_identity(context):
    """AgentOps Identity signs in through one OIDC provider; the import runs off, count or apply."""
    settings = context.get("agentops")
    if not isinstance(settings, dict) or "identity" not in settings:
        return
    identity = settings["identity"]
    if not isinstance(identity, dict):
        raise ValueError("agentops.identity must be a mapping")
    if not isinstance(identity.get("oidc_issuer"), str) or not identity["oidc_issuer"].startswith("https://"):
        raise ValueError("agentops.identity.oidc_issuer must be an https URL")
    if identity.get("import", "off") not in {"off", "count", "apply"}:
        raise ValueError("agentops.identity.import must be off, count or apply")
    domains = identity.get("enrollment_domains", [])
    if not isinstance(domains, list) or not all(isinstance(domain, str) and domain for domain in domains):
        raise ValueError("agentops.identity.enrollment_domains must be a list of domains")


def validate_agent_oidc(context):
    """AgentOps Identity is the only sign-in; no Agent service connects to an IdP or holds Git hosts."""
    for service in ("studio", "memory", "knowledge", "forge", "models", "runway"):
        settings = context.get(f"agent_{service}")
        if isinstance(settings, dict):
            for key in ("oidc", "google_sign_in", "password_sign_in", "sign_in_domains", "git_hosts"):
                if key in settings:
                    raise ValueError(f"agent_{service}.{key} is not supported; sign-in and Git hosts are agentops.identity and agentops.git_hosts")


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
                for key in ("resources", "autoscaling", "agent_studio_maintenance", "agent_memory_maintenance", "agent_knowledge_maintenance", "agent_forge_maintenance", "agent_models_maintenance", "agent_runway_maintenance", "agentops_maintenance"):
                    if key in v and not isinstance(v[key], bool):
                        raise ValueError("{} {} must be a boolean".format(env_path, key))
                validate_agent_oidc(v)
                validate_agentops_identity(v)
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
                    or metrics.get("backend") != "prometheus"
                ):
                    raise ValueError("{} metrics.backend must be prometheus".format(env_path))

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

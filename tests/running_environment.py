"""Render normal operation explicitly, independent of an environment's maintenance state."""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]


def running_values(chart, platform):
    env = yaml.safe_load((ROOT / "env" / f"{platform}-demo.yaml").read_text())
    env["agent_studio_maintenance"] = False
    env["agent_memory_maintenance"] = False
    env["agent_knowledge_maintenance"] = False
    env["agent_forge_maintenance"] = False
    env["agent_models_maintenance"] = False
    jinja = Environment(loader=FileSystemLoader(ROOT / "charts" / chart), undefined=StrictUndefined)
    return jinja.get_template("values-template.yaml.j2").render(env)

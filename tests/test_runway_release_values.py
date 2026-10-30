"""A newly created Runway chart must accept the release pipeline's first image."""
from pathlib import Path
import shutil
import pytest
import yaml
import chart


@pytest.mark.parametrize("phase", ["alpha", "prod"])
def test_first_runway_release_populates_version_fields(tmp_path, phase):
    source = Path(__file__).resolve().parents[1] / "charts/agent-runway" / f"values-{phase}.yaml"
    target = tmp_path / "charts/agent-runway"
    target.mkdir(parents=True)
    shutil.copy(source, target / source.name)
    result = chart.update_values(str(tmp_path), "agent-runway", phase, "v1.2.3", "app")
    app = yaml.safe_load(Path(result).read_text())["app"]
    assert app["image"]["tag"] == "v1.2.3"
    assert app["configmap"]["data"]["VERSION"] == "v1.2.3"
    assert {item["name"]: item["value"] for item in app["env"]}["VERSION"] == "v1.2.3"

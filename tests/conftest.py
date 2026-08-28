import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# threadly_common is a real package; import it off libs/.
sys.path.insert(0, str(ROOT / "libs" / "common"))


def _load(name: str, path: Path) -> None:
    """Load a pure-logic service module by file path. The three services all
    use an `app` package name, so they can't share sys.path — this sidesteps
    the collision without installing anything."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


_load("orch_planner", ROOT / "services" / "orchestrator" / "app" / "planner.py")
_load("gmail_extractor", ROOT / "services" / "gmail" / "app" / "extractor.py")
_load("inference_pii", ROOT / "services" / "inference" / "app" / "pii.py")
_load("gateway_ratelimit", ROOT / "services" / "gateway" / "app" / "ratelimit.py")

import logging
from pathlib import Path
from typing import Any, TypeVar, overload

from steam_play_manager.models import BaseManifest, IdleGame, IdleManifest, UnlockGame, UnlockManifest

logger = logging.getLogger(__name__)

_ManifestT = TypeVar("_ManifestT", bound=BaseManifest)


def load_manifest(path: str | Path, model: type[_ManifestT] = UnlockManifest) -> _ManifestT:
    """Loads and validates a manifest JSON file using a dataclass model."""
    p = Path(path)
    if not p.exists():
        return model()
    try:
        return model.load_from_file(p)
    except Exception as e:
        logger.error(f"Failed to load manifest from {path}: {e}")
        return model()


@overload
def get_category_games(manifest_data: UnlockManifest, category_name: str) -> list[UnlockGame]: ...


@overload
def get_category_games(manifest_data: IdleManifest, category_name: str) -> list[IdleGame]: ...


def get_category_games(manifest_data: UnlockManifest | IdleManifest, category_name: str) -> list[Any]:
    """Resolve manifest games belonging to a category name, or all games for 'all'."""
    if category_name.lower() == "all":
        return list(manifest_data.games)
    categories = manifest_data.categories
    if category_name not in categories:
        return []
    cat_ids = set(categories[category_name])
    return [g for g in manifest_data.games if g.app_id in cat_ids]

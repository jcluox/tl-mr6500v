import json
from pathlib import Path

MAX_SEEN = 1000


class SeenStore:
    """Remembers which messages were already handled, so nothing is pushed twice after a restart."""

    def __init__(self, path: Path):
        self._path = path
        self._seen: list[str] = []
        self.initialized = False
        if path.exists():
            try:
                data = json.loads(path.read_text())
                self._seen = data.get("seen", [])
                self.initialized = data.get("initialized", False)
            except (json.JSONDecodeError, OSError):
                pass
        self._set = set(self._seen)

    def __contains__(self, uid: str) -> bool:
        return uid in self._set

    def add(self, uid: str) -> None:
        if uid not in self._set:
            self._set.add(uid)
            self._seen.append(uid)

    def save(self) -> None:
        self.initialized = True
        self._seen = self._seen[-MAX_SEEN:]
        self._set = set(self._seen)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"initialized": True, "seen": self._seen}))
        tmp.replace(self._path)

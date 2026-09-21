"""Container entrypoint: same dashboard as `python backend/server.py`, bound for
Docker (0.0.0.0) instead of the source's `127.0.0.1` default.

`backend/server.py`'s `serve()` takes no host argument from the CLI (only a port,
`sys.argv[1]`), and it is a shared teammate-maintained file — this wrapper avoids
editing it just to change a bind address for one deployment target. Port is
configurable via $PORT so it matches whatever the container/Dokploy expects.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.server import serve  # noqa: E402

if __name__ == "__main__":
    serve(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))

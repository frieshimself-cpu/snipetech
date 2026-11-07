"""snipetech: decide *what* to snipe and *who* to copy on pump.fun, from on-chain evidence."""

import hashlib
import os

__version__ = "0.1.0"


def code_identity(root: str | os.PathLike | None = None) -> str:
    """The snipetech source as it is on disk now: a hash of the path, size and modification time of every module file
    under `root` (default: this package), bytecode caches left out. `remote update` resets the checkout under the jobs
    the bot started, which go on running the modules they imported; a process started after it (a lab's worker)
    imports the new files. A process whose `code_identity()` is not its CODE_ID no longer has its own code on disk."""
    root = os.path.abspath(os.path.dirname(__file__) if root is None else root)
    digest = hashlib.sha256(root.encode())
    for top, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(f for f in files if f.endswith(".py")):
            path = os.path.join(top, name)
            try:
                st = os.stat(path)
            except OSError:  # removed since it was listed: left out, which changes the identity as a removal should
                continue
            digest.update(f"{os.path.relpath(path, root)}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return digest.hexdigest()


# The source this process imports its snipetech modules from, as it was when the process first imported snipetech: a
# module imported later came from these same files as long as `code_identity()` still returns this.
CODE_ID = code_identity()

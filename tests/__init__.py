import atexit
import os
import shutil
import tempfile

# Keep the tests away from the real ~/.local/state and ~/.cache.
_home = tempfile.mkdtemp(prefix="hold2tg-tests-")
atexit.register(shutil.rmtree, _home, True)
os.environ["XDG_STATE_HOME"] = os.path.join(_home, "state")
os.environ["XDG_CACHE_HOME"] = os.path.join(_home, "cache")

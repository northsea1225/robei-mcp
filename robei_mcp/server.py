"""Package-path service entry used by lifecycle health identity checks."""
from .lifecycle import run_server


if __name__ == "__main__":
    run_server()

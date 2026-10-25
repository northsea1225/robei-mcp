from .lifecycle import run_server


def main() -> None:
    """Console-script entry point (``robei-mcp``) and ``python -m robei_mcp``."""
    run_server()


if __name__ == "__main__":
    main()

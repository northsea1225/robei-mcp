from .server_app import mcp


def main() -> None:
    """Console-script entry point (``robei-mcp``) and ``python -m robei_mcp``."""
    mcp.run()


if __name__ == "__main__":
    main()

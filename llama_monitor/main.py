"""Entry point for llama-monitor daemon."""

import sys


def main():
    """Main entry point - dispatches to CLI or daemon."""
    from llama_monitor.cli import main as cli_main
    
    try:
        cli_main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

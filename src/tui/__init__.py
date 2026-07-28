from __future__ import annotations

from src.tui.app import XClawApp


def main(
    task: str,
    workdir: str,
    config,
    backend,
) -> None:
    app = XClawApp(config=config, backend=backend, task=task, workdir=workdir)
    app.run()

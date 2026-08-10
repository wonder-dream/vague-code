from __future__ import annotations

from vague_code.tui.app import VagueCodeApp


def main(
    task: str,
    workdir: str,
    config,
    backend,
    provider: str = "deepseek",
) -> None:
    app = VagueCodeApp(config=config, backend=backend, task=task, workdir=workdir,
                       provider=provider)
    app.run()

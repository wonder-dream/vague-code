from __future__ import annotations

from vague_code.tui.app import VagueCodeApp


def main(
    task: str,
    workdir: str,
    config,
    backend,
    provider: str = "deepseek",
    file_config: dict | None = None,
    needs_setup: bool = False,
) -> None:
    app = VagueCodeApp(config=config, backend=backend, task=task, workdir=workdir,
                       provider=provider, file_config=file_config, needs_setup=needs_setup)
    app.run()

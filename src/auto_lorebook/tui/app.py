"""ProcessApp: top-level Textual application for the process pipeline."""

from __future__ import annotations

import asyncio
import logging
import queue
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual.app import App
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label

from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import reading_pipeline, source_store, ytdlp
from auto_lorebook import review as review_mod
from auto_lorebook import source_id as sid_mod
from auto_lorebook.commands.ingest import ResolvedSource, new_info
from auto_lorebook.tui.resume import detect_stage
from auto_lorebook.tui.reviewer import TuiReviewer
from auto_lorebook.tui.screens.context import ContextScreen
from auto_lorebook.tui.screens.progress import ProgressScreen
from auto_lorebook.tui.screens.reading import ReadingScreen
from auto_lorebook.tui.screens.review import ReviewScreen
from auto_lorebook.tui.state import Stage

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from auto_lorebook.config import Config
    from auto_lorebook.tui.state import PipelineState

_logger = logging.getLogger(__name__)


class _DoneScreen(Screen):
    """Completion screen shown when all stages finish."""

    BINDINGS: ClassVar[list] = [Binding("q", "quit", "Quit")]

    def __init__(self, *, source_id: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type]
        self._source_id = source_id

    def compose(self) -> ComposeResult:
        yield Label(
            f"[bold green]Done![/bold green]  Pipeline complete for "
            f"[bold]{self._source_id}[/bold].\n\nPress [b]q[/b] to quit."
        )
        yield Footer()

    def action_quit(self) -> None:
        self.dismiss(None)


class _ErrorScreen(Screen):
    """Error screen shown when a stage fails."""

    BINDINGS: ClassVar[list] = [Binding("q", "quit", "Quit")]

    def __init__(self, *, message: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type]
        self._message = message

    def compose(self) -> ComposeResult:
        yield Label(
            f"[bold red]Error:[/bold red]  {self._message}\n\nPress [b]q[/b] to quit."
        )
        yield Footer()

    def action_quit(self) -> None:
        self.dismiss(None)


class ProcessApp(App):
    """TUI orchestrator for the end-to-end source pipeline."""

    TITLE = "auto-lorebook process"
    CSS = ""

    def __init__(self, *, cfg: Config, state: PipelineState) -> None:
        super().__init__()
        self._cfg = cfg
        self._state = state

    def compose(self) -> ComposeResult:
        yield Label("[bold]Starting…[/bold]", id="start-label")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._drive_pipeline, thread=False, exit_on_error=False)

    # ---- pipeline driver ---------------------------------------------------

    async def _drive_pipeline(self) -> None:
        """Drive the stage machine until DONE or user quits."""
        source_id = self._state.source_id
        wiki_repo = self._state.wiki_repo_path

        while True:
            stage = self._state.stage
            ok: bool
            if stage == Stage.INGEST:
                ok = await self._do_ingest()
            elif stage == Stage.CONTEXT:
                ok = await self._do_context()
            elif stage == Stage.READING_GEN:
                ok = await self._do_reading_gen()
            elif stage == Stage.READING_GATE:
                ok = await self._do_reading_gate()
            elif stage == Stage.PLAN:
                ok = await self._do_plan()
            elif stage == Stage.EXTRACT:
                ok = await self._do_extract()
            elif stage == Stage.REVIEW_GATE:
                ok = await self._do_review_gate()
            elif stage == Stage.DONE:
                await self._show_done()
                return
            else:
                return

            if not ok:
                return
            self._state.stage = detect_stage(source_id, wiki_repo)

    # ---- blocking helper (called in executor thread) -----------------------

    def _run_ingest(
        self,
        url_or_path: str,
        source_id: str,
        wiki_repo: Path,
        screen: ProgressScreen,
    ) -> None:
        """Fetch + store transcript; write info.yaml if absent."""
        video_id = sid_mod.extract_video_id(url_or_path)
        if video_id:
            self.call_from_thread(screen.append_log, f"Fetching {url_or_path}…")
            with tempfile.TemporaryDirectory(prefix="auto-lorebook-yt-") as tmp:
                fetched = ytdlp.fetch(url_or_path, Path(tmp))
                caption_type = "auto" if ".auto." in fetched.srt_path.name else "manual"
                resolved = ResolvedSource(
                    local_path=fetched.srt_path,
                    source_url=url_or_path,
                    source_type="youtube",
                    fetched_title=fetched.title,
                    fetched_duration=fetched.duration,
                    caption_type=caption_type,
                )
                self.call_from_thread(screen.append_log, "Storing transcript…")
                _, tf = source_store.copy_transcript(
                    resolved.local_path, source_id, resolved.source_type, wiki_repo
                )
            info_path = wiki_repo / "sources" / source_id / "info.yaml"
            if not info_path.exists():
                info_yaml_mod.write(
                    new_info(source_id, resolved, url_or_path, tf), info_path
                )
        else:
            local_path = Path(url_or_path)
            suffix = local_path.suffix.lower()
            src_type = (
                "srt" if suffix == ".srt" else "markdown" if suffix == ".md" else "text"
            )
            resolved = ResolvedSource(
                local_path=local_path,
                source_url=None,
                source_type=src_type,
            )
            self.call_from_thread(screen.append_log, "Storing transcript…")
            _, tf = source_store.copy_transcript(
                resolved.local_path, source_id, resolved.source_type, wiki_repo
            )
            info_path = wiki_repo / "sources" / source_id / "info.yaml"
            if not info_path.exists():
                info_yaml_mod.write(
                    new_info(source_id, resolved, url_or_path, tf), info_path
                )

    # ---- stage handlers ----------------------------------------------------

    async def _do_ingest(self) -> bool:
        url = self._state.url_or_path
        if url is None:
            await self._show_error("No URL or path provided for ingest.")
            return False
        source_id = self._state.source_id
        wiki_repo = self._state.wiki_repo_path
        screen = ProgressScreen(title=f"Ingesting {source_id}…")
        self.push_screen(screen)
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        error: Exception | None = None
        try:
            await loop.run_in_executor(
                None, lambda: self._run_ingest(url, source_id, wiki_repo, screen)
            )
        except Exception as e:  # noqa: BLE001
            error = e
        self.pop_screen()
        if error is not None:
            await self._show_error(f"Ingest failed: {error}")
            return False
        return True

    async def _do_context(self) -> bool:
        result = await self.push_screen_wait(
            ContextScreen(cfg=self._cfg, source_id=self._state.source_id)
        )
        if result is None:
            self.exit()
            return False
        source_id = self._state.source_id
        info_path = self._state.wiki_repo_path / "sources" / source_id / "info.yaml"
        info_yaml_mod.write(result, info_path)
        tombstone = reading_pipeline.pending_root(source_id) / "context.set"
        tombstone.parent.mkdir(parents=True, exist_ok=True)
        tombstone.touch()
        return True

    async def _do_reading_gen(self) -> bool:
        screen = ProgressScreen(title="Generating reading…")
        self.push_screen(screen)
        await asyncio.sleep(0)
        cfg, source_id = self._cfg, self._state.source_id
        loop = asyncio.get_running_loop()
        error: Exception | None = None
        try:
            await loop.run_in_executor(
                None, lambda: reading_pipeline.generate(cfg, source_id)
            )
        except Exception as e:  # noqa: BLE001
            error = e
        self.pop_screen()
        if error is not None:
            await self._show_error(f"Reading generation failed: {error}")
            return False
        return True

    async def _do_reading_gate(self) -> bool:
        result = await self.push_screen_wait(
            ReadingScreen(cfg=self._cfg, source_id=self._state.source_id)
        )
        if result is None:
            self.exit()
            return False
        action, _ = result
        source_id = self._state.source_id
        if action == "approve":
            screen = ProgressScreen(title="Approving reading…")
            self.push_screen(screen)
            await asyncio.sleep(0)
            cfg = self._cfg
            loop = asyncio.get_running_loop()
            error: Exception | None = None
            try:
                await loop.run_in_executor(
                    None, lambda: reading_pipeline.approve(cfg, source_id)
                )
            except Exception as e:  # noqa: BLE001
                error = e
            self.pop_screen()
            if error is not None:
                await self._show_error(f"Approve failed: {error}")
                return False
            return True
        if action == "reject":
            p = reading_pipeline.pending_reading_path(source_id)
            if p.exists():
                p.unlink()
            return True
        # quit
        self.exit()
        return False

    async def _do_plan(self) -> bool:
        screen = ProgressScreen(title="Running planner…")
        self.push_screen(screen)
        await asyncio.sleep(0)
        cfg, source_id = self._cfg, self._state.source_id
        loop = asyncio.get_running_loop()
        error: Exception | None = None
        try:
            await loop.run_in_executor(
                None, lambda: reading_pipeline.plan(cfg, source_id)
            )
        except Exception as e:  # noqa: BLE001
            error = e
        self.pop_screen()
        if error is not None:
            await self._show_error(f"Planning failed: {error}")
            return False
        return True

    async def _do_extract(self) -> bool:
        screen = ProgressScreen(title="Running extractor…")
        self.push_screen(screen)
        await asyncio.sleep(0)
        cfg, source_id = self._cfg, self._state.source_id
        loop = asyncio.get_running_loop()
        error: Exception | None = None
        try:
            await loop.run_in_executor(
                None, lambda: reading_pipeline.extract(cfg, source_id)
            )
        except Exception as e:  # noqa: BLE001
            error = e
        self.pop_screen()
        if error is not None:
            await self._show_error(f"Extraction failed: {error}")
            return False
        return True

    async def _do_review_gate(self) -> bool:
        pending: queue.Queue = queue.Queue()
        cancel_event = threading.Event()
        rev_screen = ReviewScreen(pending=pending, cancel_event=cancel_event)
        self.push_screen(rev_screen)
        await asyncio.sleep(0)
        cfg, source_id = self._cfg, self._state.source_id
        reviewer = TuiReviewer(
            app=self,
            cancel_event=cancel_event,
            pending=pending,
            show_bundle_fn=rev_screen.show_bundle,
            confirm_alias_fn=lambda *_: pending.put(True),  # noqa: FBT003
        )
        loop = asyncio.get_running_loop()
        error: Exception | None = None
        cancelled = False
        try:
            await loop.run_in_executor(
                None,
                lambda: review_mod.run(cfg=cfg, source_id=source_id, reviewer=reviewer),
            )
        except KeyboardInterrupt:
            cancelled = True
        except Exception as e:  # noqa: BLE001
            error = e
        self.pop_screen()
        if error is not None:
            await self._show_error(f"Review failed: {error}")
            return False
        if cancelled:
            self.exit()
            return False
        tombstone = reading_pipeline.pending_root(source_id) / "review.done"
        tombstone.parent.mkdir(parents=True, exist_ok=True)
        tombstone.touch()
        return True

    # ---- terminal screens --------------------------------------------------

    async def _show_done(self) -> None:
        await self.push_screen_wait(_DoneScreen(source_id=self._state.source_id))
        self.exit()

    async def _show_error(self, message: str) -> None:
        await self.push_screen_wait(_ErrorScreen(message=message))
        self.exit()

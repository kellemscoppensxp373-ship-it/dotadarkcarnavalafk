"""
executor.py — The orchestrator.

Wires the eyes (``vision``), the hands (``input_handler``), the domain knowledge
(``dota_logic``) and the memory (``learning``) into one resilient, cyclical state machine.

Architectural contract
----------------------
* **Sense → classify → act, every tick.** There is no "blind script" that assumes the UI
  advanced. Each tick re-reads the screen, re-classifies the state from Russian keywords,
  and only then acts. If Dota throws an unexpected modal, the very next tick sees it.
* **Never trust a click.** Every action is verified by a *subsequent* observation; failures
  feed ``learning`` and trigger escalating recovery rather than a crash.
* **Cooperative cancellation.** The GUI thread owns a ``threading.Event``; the loop checks
  it at every wait point, so Stop is instant and the hot-reloader can swap this module.
* **Injectable everything.** ``Executor`` takes its collaborators as arguments, so the test
  suite drives entire multi-cycle sessions with fakes and zero real sleeping.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("dc.executor")


# --------------------------------------------------------------------------------------
# Events emitted to the GUI
# --------------------------------------------------------------------------------------


@dataclass
class ExecutorEvents:
    """Callbacks into the GUI. All optional; all must be cheap and thread-safe."""

    on_log: Callable[[str, str], None] = lambda level, msg: None       # (level, message)
    on_state: Callable[[str], None] = lambda state: None
    on_cycle: Callable[[Any], None] = lambda record: None
    on_stats: Callable[[dict], None] = lambda stats: None
    on_action: Callable[[str], None] = lambda action: None


#: Человекочитаемые названия состояний для лога и интерфейса.
RU_STATES: dict[str, str] = {
    "unknown": "неизвестный экран",
    "dashboard": "главное меню",
    "queueing": "поиск игры",
    "match_found": "игра найдена",
    "hero_pick": "выбор героя",
    "in_game": "идёт матч",
    "post_game": "итоги матча",
    "safe_to_leave": "можно выходить",
    "reward_screen": "экран наград",
    "disconnected": "нет соединения",
    "error": "ошибка",
}


class StopRequested(Exception):
    """Raised internally to unwind the loop promptly when the user hits Stop."""


# --------------------------------------------------------------------------------------
# Executor
# --------------------------------------------------------------------------------------


class Executor:
    """Runs the Dark Carnival farming cycle until stopped."""

    def __init__(
        self,
        vision: Any,
        inputs: Any,
        logic: Any,
        brain: Any = None,
        config: Any = None,
        events: ExecutorEvents | None = None,
        *,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.vision = vision
        self.inputs = inputs
        self.logic = logic                      # the dota_logic module (hot-swappable)
        self.config = config if config is not None else logic.LoopConfig()
        self.events = events or ExecutorEvents()
        self.stop_event = stop_event or threading.Event()
        self.pause_event = pause_event or threading.Event()
        self._sleep_fn = sleep
        self._clock = clock

        if brain is None:
            try:
                from learning import LearningStore

                brain = LearningStore()
            except Exception:  # pragma: no cover - memory is optional
                log.warning("learning store unavailable; running without memory")
        self.brain = brain

        self.goal = self.config.goal()
        try:
            self.book = self.config.book()
        except Exception:  # pragma: no cover - нет диска/файла
            log.warning("не удалось загрузить таблицу билетов", exc_info=True)
            self.book = logic.TicketBook()
        self.state = logic.GameState.UNKNOWN
        self.cycle_index = 0
        self.rotation = 0
        self.current_pick: Any = None
        self.current_hero: Any = None
        self._cycle_started = self._clock()
        self._cycle_open = False
        self._cycle_victory = False
        self._recovery_attempts = 0
        self._last_evidence: dict[str, Any] = {}

    # ----------------------------------------------------------------------------------
    # Plumbing
    # ----------------------------------------------------------------------------------

    def log(self, msg: str, level: str = "info") -> None:
        getattr(log, level, log.info)(msg)
        try:
            self.events.on_log(level, msg)
        except Exception:  # pragma: no cover - a broken GUI must not kill the bot
            log.debug("on_log callback failed", exc_info=True)

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise StopRequested()

    def sleep(self, seconds: float) -> None:
        """Interruptible sleep: wakes immediately on Stop, honours Pause."""
        self._check_stop()
        deadline = self._clock() + max(0.0, seconds)
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._sleep_fn(min(0.25, remaining))
            self._check_stop()
        while self.pause_event.is_set():
            self._check_stop()
            self._sleep_fn(0.2)

    def screen_size(self) -> tuple[int, int]:
        try:
            return self.vision.screen.size()
        except Exception:  # pragma: no cover - headless
            return (1920, 1080)

    def region(self, name: str):
        w, h = self.screen_size()
        return self.logic.region_for(name, w, h)

    # ----------------------------------------------------------------------------------
    # Semantic primitives
    # ----------------------------------------------------------------------------------

    def look(self, region_name: str = "full", fresh: bool = True) -> dict[str, Any]:
        """One OCR pass → ``{intent: TextHit}``. The agent's heartbeat."""
        self._check_stop()
        region = self.region(region_name) if region_name != "full" else None
        evidence = self.vision.detect_state_keywords(region=region, fresh=fresh)
        self._last_evidence = evidence
        return evidence

    def find(self, intent: str, region_name: str = "full") -> Any:
        """Locate an intent, consulting the learned spatial prior first (fast path)."""
        self._check_stop()
        started = self._clock()

        if self.brain is not None:
            hot = self.brain.hot_region(intent, self.screen_size())
            if hot is not None:
                hit = self.vision.find_intent(intent, region=hot, fresh=True)
                if hit is not None:
                    self._learn_hit(intent, hit, self._clock() - started)
                    return hit

        region = self.region(region_name) if region_name != "full" else None
        hit = self.vision.find_intent(intent, region=region, fresh=True)
        if hit is not None:
            self._learn_hit(intent, hit, self._clock() - started)
        elif self.brain is not None:
            self.brain.record_miss(intent)
        return hit

    def _learn_hit(self, intent: str, hit: Any, latency: float) -> None:
        if self.brain is None:
            return
        try:
            self.brain.record_hit(intent, hit.center, self.screen_size(), latency)
        except Exception:  # pragma: no cover
            log.debug("failed to record hit", exc_info=True)

    def click_intent(self, intent: str, region_name: str = "full", required: bool = False) -> bool:
        """Read the screen for ``intent`` and click the centre of what was read."""
        hit = self.find(intent, region_name)
        if hit is None:
            if required:
                self.log(f"не нашёл на экране «{intent}»", "warning")
            return False
        self.inputs.click_hit(hit)
        self.events.on_action(f"click:{intent}")
        self.log(f"клик «{hit.matched_form or hit.text}» @{hit.center} (сходство {hit.score:.2f})")
        self.inputs.idle(0.3, 0.8)
        return True

    def wait_intent(self, intent: str, timeout: float, poll: float = 1.0) -> Any:
        """Interruptible polling wait for a semantic intent."""
        if self.brain is not None:
            timeout = self.brain.suggest_timeout(intent, timeout)
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            self._check_stop()
            hit = self.find(intent)
            if hit is not None:
                return hit
            self.sleep(poll)
        return None

    def wait_any(self, intents: Sequence[str], timeout: float, poll: float = 1.5) -> Any:
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            self._check_stop()
            hits = self.vision.find_all_intents(intents, fresh=True)
            if hits:
                self._learn_hit(hits[0].intent, hits[0], 0.0)
                return hits[0]
            self.sleep(poll)
        return None

    # ----------------------------------------------------------------------------------
    # State classification
    # ----------------------------------------------------------------------------------

    def detect_state(self) -> Any:
        evidence = self.look()
        state = self.logic.classify_state(evidence.keys())
        if state != self.state:
            self.log(f"состояние: {RU_STATES.get(self.state.value, self.state.value)} → {RU_STATES.get(state.value, state.value)}")
            self._recovery_attempts = 0
            try:
                self.events.on_state(state.value)
            except Exception:  # pragma: no cover
                pass
        self.state = state
        if self.brain is not None:
            self.brain.note_state(state.value)
        return state

    # ----------------------------------------------------------------------------------
    # State handlers — one method per GameState
    # ----------------------------------------------------------------------------------

    def _plan_cycle(self) -> bool:
        """Выбрать героя и аркан на этот цикл. ``False`` — играть не на чем."""
        pick = self.logic.plan_next_pick(
            self.book, self.goal,
            min_yield=self.config.min_ticket_yield,
            avoid=list(self.config.avoid_heroes),
            rotation=self.rotation,
        )
        self.rotation += 1
        if pick is None:
            self._cycle_open = False
            missing = self.book.missing(self.config.min_ticket_yield)
            names = ", ".join(self.logic.arcana_name_ru(k) for k in missing[:5])
            self.log(
                "НЕЧЕГО ФАРМИТЬ: не заполнена таблица билетов. Откройте вкладку "
                f"«Билеты» и укажите героя ×{self.config.min_ticket_yield} хотя бы для "
                f"одного аркана. Сейчас пусто: {names}", "error")
            self.stop_event.set()
            return False

        self.current_pick = pick
        self.current_hero = self.logic.resolve_hero(pick.hero_name)
        self.log(f"цикл {self.cycle_index + 1}: {pick.describe()}")
        if self.goal.target:
            self.log(f"прогресс — {self.goal.progress()}")
        return True

    def handle_dashboard(self) -> None:
        """Close the previous cycle (we are back at the menu), then start the next one.

        A cycle is only "done" once the client is back on the dashboard — closing it at
        «безопасно покинуть» would miss the reward screen that still follows.
        """
        if self._cycle_open:
            self._finish_cycle(victory=self._cycle_victory, completed=True)
            if self.config.max_cycles and self.cycle_index >= self.config.max_cycles:
                self.log(f"достигнут предел в {self.config.max_cycles} циклов — остановка")
                self.stop_event.set()
                return

        self._cycle_open = True
        self._cycle_victory = False
        if not self._plan_cycle():
            return
        self._cycle_started = self._clock()

        # Make sure we are on the co-op bots / Dark Carnival tab before queueing.
        if self.find("dark_carnival") is None:
            self.click_intent("coop_bots")

        if not self.click_intent("play", region_name="play_button"):
            self.click_intent("play", required=True)
        self.sleep(self.config.poll_interval)

    def handle_queueing(self) -> None:
        """Wait out the queue; the accept popup is what we really want."""
        hit = self.wait_any(["accept", "queue_found"], timeout=self.config.queue_timeout, poll=2.0)
        if hit is None:
            self.log("поиск игры затянулся — перезапускаю очередь", "warning")
            self.click_intent("cancel_search")

    def handle_match_found(self) -> None:
        """Click «Принять» as fast as the humanised mover allows."""
        if not self.click_intent("accept", region_name="center_modal"):
            self.click_intent("accept", required=True)
        # Confirm we actually left the popup, otherwise re-try once.
        self.sleep(1.5)
        if self.find("accept") is not None:
            self.log("окно «Принять» ещё на экране — жму повторно", "warning")
            self.click_intent("accept")

    def handle_hero_pick(self) -> None:
        """Dual-asset hero selection: search by RU name, confirm by 3D portrait."""
        hero = self.current_hero
        if hero is None:
            if not self._plan_cycle():
                return
            hero = self.current_hero
        self.log(f"берём героя «{hero.name_ru}»")

        # 1) Focus the search field by reading its Russian label.
        search = self.find("search_hero", region_name="hero_search")
        if search is not None:
            self.inputs.click_hit(search)
            self.inputs.clear_field()
            self.inputs.type_text(hero.name_ru)   # Cyrillic-safe (clipboard path)
            self.sleep(0.8)

        # 2) Confirm the hero visually via the high-res PORTRAIT asset in the pick grid.
        picked = self._click_hero_portrait(hero)
        if not picked:
            # 3) Fall back to reading the hero's localised name in the grid.
            self.log("портрет не распознан — ищу героя по названию (OCR)", "warning")
            name_hit = None
            for candidate in hero.all_names_ru:
                name_hit = self.vision.find_text(candidate, region=self.region("hero_grid"), fresh=True)
                if name_hit is not None:
                    break
            if name_hit is not None:
                self.inputs.click_hit(name_hit)
                picked = True

        if picked:
            self.sleep(0.6)
            self.click_intent("lock_in", region_name="hero_grid")
            self.click_intent("ready")
        else:
            self.log(f"не удалось выбрать «{hero.name_ru}» — герой будет назначен автоматически", "error")
            if self.brain is not None:
                self.brain.bump("hero_pick_failures")

    def _click_hero_portrait(self, hero: Any) -> bool:
        """Locate the hero's 3D portrait in the pick grid (Phase 3 dual-asset)."""
        try:
            hit = self.vision.find_template(
                hero.portrait_path(self.config.assets_dir),
                region=self.region("hero_grid"),
                key=hero.key,
            )
        except Exception:
            log.debug("template matching unavailable", exc_info=True)
            return False
        if hit is None:
            return False
        self.inputs.click_hit(hit)
        self.log(f"портрет найден: «{hero.name_ru}» (совпадение {hit.score:.2f})")
        return True

    def handle_in_game(self) -> None:
        """The bots play; we idle politely and watch for the end-of-game keywords."""
        self.inputs.wiggle()
        hit = self.wait_any(
            ["safe_to_leave", "victory", "defeat", "reconnect"],
            timeout=min(60.0, self.config.game_timeout),
            poll=5.0,
        )
        if hit is not None:
            self.log(f"сигнал из игры: «{hit.text}»")

    def handle_post_game(self) -> None:
        """Record the result and move toward the exit."""
        victory = "victory" in self._last_evidence
        self._cycle_victory = self._cycle_victory or victory
        self.log("РЕЗУЛЬТАТ: Победа" if victory else "РЕЗУЛЬТАТ: игра окончена (баннера победы нет)")
        if self.brain is not None and victory:
            self.brain.bump("victories")
        self._harvest_rewards()
        for intent in ("continue", "close"):
            if self.click_intent(intent):
                break

    def handle_safe_to_leave(self) -> None:
        """«Игру можно безопасно покинуть» — the whole point of the loop."""
        self.log("обнаружено «Игру можно безопасно покинуть» — выхожу из матча")
        self._cycle_victory = self._cycle_victory or ("victory" in self._last_evidence)
        self._harvest_rewards()
        if not self.config.leave_early:
            return
        if not self.click_intent("leave_game"):
            self.inputs.press("escape")
            self.sleep(0.8)
            self.click_intent("leave_game", region_name="center_modal")
        self.sleep(2.0)
        self.click_intent("close")

    def handle_reward_screen(self) -> None:
        self._harvest_rewards()
        self.click_intent("claim_reward")
        for intent in ("continue", "close"):
            if self.click_intent(intent):
                break

    def handle_disconnected(self) -> None:
        self.log("соединение потеряно — переподключаюсь", "warning")
        if self.brain is not None:
            self.brain.bump("disconnects")
        if not self.click_intent("reconnect", region_name="center_modal"):
            self.click_intent("reconnect")
        self.sleep(10.0)

    def handle_unknown(self) -> None:
        """Escalating recovery: nudge → dismiss modals → escape → report."""
        self._recovery_attempts += 1
        n = self._recovery_attempts
        self.log(f"экран не опознан (попытка {n}) — восстанавливаюсь", "warning")
        if self.brain is not None:
            self.brain.bump("unknown_states")

        if n == 1:
            self.inputs.wiggle()
        elif n == 2:
            for intent in ("continue", "close", "accept"):
                if self.click_intent(intent):
                    return
        elif n == 3:
            self.inputs.press("escape")
        else:
            self.log("всё ещё не понимаю экран. Вот что видят «глаза»:", "error")
            try:
                self.log(self.vision.describe_screen(), "debug")
            except Exception:  # pragma: no cover
                pass
            self._recovery_attempts = 0
        self.sleep(self.config.poll_interval)

    # ----------------------------------------------------------------------------------
    # Rewards & cycle bookkeeping
    # ----------------------------------------------------------------------------------

    def _harvest_rewards(self) -> list[str]:
        """Зачислить билеты за сыгранную игру.

        Если для героя нарезана пиксельная иконка — подтверждаем начисление
        визуально на экране наград (связка «эмодзи ↔ портрет» из ЭТАПА 3).
        Иконки нет — начисляем ожидаемое по таблице билетов и помечаем в логе.
        """
        pick = self.current_pick
        if pick is None:
            return []
        hero = self.current_hero
        emoji_hit = None
        emoji_path = hero.emoji_path(self.config.assets_dir) if hero is not None else ""
        if emoji_path:
            try:
                emoji_hit = self.vision.find_template(
                    emoji_path, region=self.region("reward_panel"), key=hero.key,
                )
            except Exception:
                emoji_hit = None
        if emoji_hit is not None:
            self.log(f"награда подтверждена иконкой «{hero.name_ru}»")
        else:
            self.log("иконка награды не сверена — зачисляем по таблице", "debug")
        self.goal.credit(pick.arcana, pick.yield_)
        tickets = [pick.arcana] * pick.yield_
        self.log(f"+{pick.yield_} бил. «{pick.arcana_ru}» · {self.goal.progress()}")
        return tickets

    def _finish_cycle(self, *, victory: bool, completed: bool, error: str = "") -> None:
        duration = self._clock() - self._cycle_started
        pick = self.current_pick
        tickets = [pick.arcana] * pick.yield_ if pick is not None else []
        record = None
        if self.brain is not None:
            try:
                from learning import CycleRecord

                record = CycleRecord(
                    index=self.cycle_index,
                    hero=self.current_hero.key if self.current_hero else "",
                    duration=round(duration, 1),
                    tickets=tickets,
                    victory=victory,
                    completed=completed,
                    error=error,
                )
                self.brain.record_cycle(record)
                self.events.on_stats(self.brain.summary())
            except Exception:  # pragma: no cover
                log.debug("cycle bookkeeping failed", exc_info=True)
        self.cycle_index += 1
        self.log(f"цикл {self.cycle_index} завершён за {duration:.0f} с "
                 f"(герой: {self.current_hero.name_ru if self.current_hero else '—'}, победа: {'да' if victory else 'нет'})")
        if record is not None:
            try:
                self.events.on_cycle(record)
            except Exception:  # pragma: no cover
                pass
        self._cycle_open = False
        self.current_hero = None
        self.current_pick = None
        self._cycle_started = self._clock()

    # ----------------------------------------------------------------------------------
    # Main loop
    # ----------------------------------------------------------------------------------

    def tick(self) -> Any:
        """Observe once and run the handler for the detected state. Returns the state."""
        state = self.detect_state()
        GS = self.logic.GameState
        handler = {
            GS.DASHBOARD: self.handle_dashboard,
            GS.QUEUEING: self.handle_queueing,
            GS.MATCH_FOUND: self.handle_match_found,
            GS.HERO_PICK: self.handle_hero_pick,
            GS.IN_GAME: self.handle_in_game,
            GS.POST_GAME: self.handle_post_game,
            GS.SAFE_TO_LEAVE: self.handle_safe_to_leave,
            GS.REWARD_SCREEN: self.handle_reward_screen,
            GS.DISCONNECTED: self.handle_disconnected,
        }.get(state, self.handle_unknown)
        handler()
        self._watchdog()
        return state

    def _watchdog(self) -> None:
        """Escape states that have outlived their plausible duration."""
        if self.brain is None:
            return
        limit = self.logic.STATE_TIMEOUTS.get(self.state, 120.0)
        if self.brain.is_stuck(limit):
            self.log(f"сторож: застряли в состоянии «{RU_STATES.get(self.state.value, self.state.value)}» дольше {limit:.0f} с — выхожу по Esc", "warning")
            self.brain.bump("watchdog_trips")
            self.inputs.press("escape")
            self.brain.state_history.clear()

    def should_continue(self) -> bool:
        if self.stop_event.is_set():
            return False
        if self.goal.target and self.goal.satisfied():
            self.log(f"цель по билетам достигнута — {self.goal.progress()}")
            return False
        if self.config.max_cycles and self.cycle_index >= self.config.max_cycles:
            self.log(f"достигнут предел в {self.config.max_cycles} циклов — остановка")
            return False
        return True

    def run(self) -> dict:
        """Blocking main loop. Call from a worker thread; stop via ``stop_event``."""
        self.log("=== Агент «Тёмный карнавал» запущен ===")
        self.log(f"режим: только герои с отдачей ×{self.config.min_ticket_yield} и выше")
        ready = self.book.configured(self.config.min_ticket_yield)
        self.log("арканы, готовые к фарму: "
                 + (", ".join(self.logic.arcana_name_ru(k) for k in ready) or "— нет —"))
        if self.config.ticket_target:
            self.log(f"цель — {self.goal.progress()}")
        try:
            while self.should_continue():
                while self.pause_event.is_set():
                    self._check_stop()
                    self._sleep_fn(0.2)
                try:
                    self.tick()
                except StopRequested:
                    raise
                except Exception as exc:  # one bad tick must never kill the session
                    self.log(f"сбой такта: {exc!r}", "error")
                    log.exception("tick failure")
                    if self.brain is not None:
                        self.brain.bump("tick_errors")
                    self.sleep(3.0)
                self.sleep(self.config.poll_interval)
        except StopRequested:
            self.log("получена команда «Стоп» — корректно завершаюсь")
        finally:
            if self._cycle_open:
                self._finish_cycle(victory=self._cycle_victory, completed=False,
                                   error="interrupted")
            if self.brain is not None:
                try:
                    self.brain.save()
                except Exception:  # pragma: no cover
                    pass
        summary = self.brain.summary() if self.brain is not None else {}
        self.log(f"=== сессия завершена === {summary}")
        return summary


# --------------------------------------------------------------------------------------
# Factory used by the thin GUI loader
# --------------------------------------------------------------------------------------


def build_executor(
    config: Any = None,
    events: ExecutorEvents | None = None,
    stop_event: threading.Event | None = None,
    pause_event: threading.Event | None = None,
) -> Executor:
    """Construct a fully wired Executor from the sibling external scripts.

    Called by ``main.py`` after a hot-reload, so every component is the freshly
    loaded version of its module.
    """
    import dota_logic
    import input_handler
    import learning
    import vision

    cfg = config if config is not None else dota_logic.LoopConfig()
    v = vision.Vision(config=vision.VisionConfig(assets_dir=cfg.assets_dir))
    i = input_handler.InputHandler(simulate=cfg.simulate)
    brain = learning.LearningStore()
    return Executor(
        vision=v, inputs=i, logic=dota_logic, brain=brain, config=cfg,
        events=events, stop_event=stop_event, pause_event=pause_event,
    )


__all__ = ["Executor", "ExecutorEvents", "StopRequested", "build_executor", "RU_STATES"]

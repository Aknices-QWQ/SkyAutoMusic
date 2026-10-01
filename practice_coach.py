"""Small local practice coach based on mastery tracking and tabular Q-learning.

The coach chooses the next practice action.  It does not generate notes, judge
posture, or claim to measure piano ability.  All state is deliberately small,
explainable, and JSON serialisable so it can stay in the user's local config.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any, Mapping


class PracticeCoach:
    """Track local practice evidence and recommend a safe next action.

    The table is intentionally tiny: a discretised state maps to five possible
    actions.  A moving-average mastery estimate supplies a stable signal while
    Q-learning lets repeated outcomes personalise the tie-breaks over time.
    """

    VERSION = 1
    DEFAULT_HISTORY_LIMIT = 120
    ACTIONS = ("repeat", "slow_down", "hints", "advance", "review")
    LABELS = {
        "repeat": "再练一轮",
        "slow_down": "降速练习",
        "hints": "保留亮键提示",
        "advance": "可以进入下一段",
        "review": "先复习旧段",
    }

    def __init__(self, data: Mapping[str, Any] | None = None, history_limit: int | None = None):
        raw = dict(data) if isinstance(data, Mapping) else {}
        configured_limit = history_limit if history_limit is not None else raw.get("history_limit", self.DEFAULT_HISTORY_LIMIT)
        try:
            configured_limit = int(configured_limit)
        except (TypeError, ValueError):
            configured_limit = self.DEFAULT_HISTORY_LIMIT
        self.history_limit = max(1, min(configured_limit, 500))
        self.mastery: dict[str, float] = {}
        for key, value in (raw.get("mastery") or {}).items() if isinstance(raw.get("mastery"), Mapping) else ():
            try:
                self.mastery[str(key)] = self._clamp(float(value))
            except (TypeError, ValueError):
                continue
        self.q_values: dict[str, dict[str, float]] = {}
        raw_q = raw.get("q_values")
        if isinstance(raw_q, Mapping):
            for state, values in raw_q.items():
                if not isinstance(values, Mapping):
                    continue
                clean = {}
                for action, value in values.items():
                    if action not in self.ACTIONS:
                        continue
                    try:
                        clean[str(action)] = self._clamp(float(value), -2.0, 2.0)
                    except (TypeError, ValueError):
                        continue
                if clean:
                    self.q_values[str(state)] = clean
        raw_history = raw.get("history")
        self.history = [dict(entry) for entry in raw_history if isinstance(entry, Mapping)][-self.history_limit:] \
            if isinstance(raw_history, list) else []
        self.last_actions = {
            str(key): str(value)
            for key, value in (raw.get("last_actions") or {}).items()
            if str(value) in self.ACTIONS
        } if isinstance(raw.get("last_actions"), Mapping) else {}

    @staticmethod
    def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def _timestamp(value: Any = None) -> str:
        if isinstance(value, str) and value:
            return value
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _context(self, context: Mapping[str, Any] | str | None = None, **values: Any) -> dict[str, Any]:
        if isinstance(context, str):
            result = {"piece": context}
        else:
            result = dict(context) if isinstance(context, Mapping) else {}
        result.update({key: value for key, value in values.items() if value is not None})
        piece = result.get("piece") or result.get("song") or result.get("lesson") or result.get("item") or "practice"
        segment = result.get("segment", result.get("chunk", 0))
        try:
            segment = max(0, int(segment))
        except (TypeError, ValueError):
            segment = 0
        result["piece"] = str(piece)
        result["segment"] = segment
        result["item"] = str(result.get("item") or f"{result['piece']}::{segment}")
        result["accuracy"] = self._clamp(self._number(result.get("accuracy"), 0.0))
        result["mistakes"] = max(0, int(self._number(result.get("mistakes"), 0)))
        result["streak"] = max(0, int(self._number(result.get("streak", result.get("clean_rounds")), 0)))
        result["tempo"] = max(40, min(240, int(self._number(result.get("tempo"), 60))))
        result["hint_used"] = bool(result.get("hint_used", result.get("hints", False)))
        result["duration"] = max(0.0, self._number(result.get("duration", result.get("duration_seconds")), 0.0))
        result["is_beginner"] = bool(result.get("is_beginner", False))
        return result

    def _item_key(self, context: Mapping[str, Any]) -> str:
        return str(context.get("item") or f"{context.get('piece', 'practice')}::{context.get('segment', 0)}")

    def _discrete_state(self, context: Mapping[str, Any], mastery: float | None = None) -> dict[str, Any]:
        item = self._item_key(context)
        value = self.mastery.get(item, 0.25) if mastery is None else mastery
        accuracy = context["accuracy"]
        if accuracy >= 0.9:
            accuracy_band = "high"
        elif accuracy >= 0.7:
            accuracy_band = "medium"
        else:
            accuracy_band = "low"
        if value >= 0.75:
            mastery_band = "high"
        elif value >= 0.45:
            mastery_band = "medium"
        else:
            mastery_band = "low"
        return {
            "item": item,
            "accuracy": accuracy_band,
            "mastery": mastery_band,
            "mistakes": "none" if context["mistakes"] == 0 else "some",
            "streak": "2+" if context["streak"] >= 2 else ("1" if context["streak"] else "0"),
            "tempo": "slow" if context["tempo"] <= 50 else ("fast" if context["tempo"] >= 90 else "normal"),
            "hints": bool(context["hint_used"]),
            "beginner": bool(context["is_beginner"]),
        }

    def state_key(self, context: Mapping[str, Any] | str | None = None, **values: Any) -> str:
        """Return a stable, compact state key for tests and diagnostics."""
        normal = self._context(context, **values)
        return json.dumps(self._discrete_state(normal), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _stale(self, item: str, now: datetime | None = None) -> bool:
        if not self.history:
            return False
        now = now or datetime.now(timezone.utc)
        for entry in reversed(self.history):
            if entry.get("item") != item:
                continue
            try:
                stamp = str(entry.get("time", "")).replace("Z", "+00:00")
                seen = datetime.fromisoformat(stamp)
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
                return (now - seen).total_seconds() >= 3 * 86400
            except (TypeError, ValueError):
                return False
        return False

    def _allowed_actions(self, context: Mapping[str, Any], mastery: float, stale: bool) -> list[str]:
        actions = ["repeat", "slow_down", "hints"]
        if context["is_beginner"]:
            actions.append("advance")
        if stale:
            actions.append("review")
        return actions

    def _heuristic_scores(self, context: Mapping[str, Any], mastery: float, stale: bool) -> dict[str, float]:
        scores = {action: 0.0 for action in self.ACTIONS}
        if context["mistakes"] or context["accuracy"] < 0.75 or mastery < 0.45:
            scores["slow_down"] += 2.0
            scores["hints"] += 1.3
            scores["repeat"] += 1.0
        elif context["accuracy"] >= 0.9 and mastery >= 0.7:
            scores["repeat"] += 1.0
            scores["advance"] += 2.0 if context["is_beginner"] else -1.0
        else:
            scores["repeat"] += 1.4
            scores["hints"] += 0.4
        if context["tempo"] <= 40:
            scores["slow_down"] -= 1.5
        if context["hint_used"]:
            scores["hints"] -= 0.2
        if stale:
            scores["review"] += 2.4
        return scores

    def recommend(self, context: Mapping[str, Any] | str | None = None, **values: Any) -> dict[str, Any]:
        """Choose the next action and return UI-ready explanation data."""
        normal = self._context(context, **values)
        item = self._item_key(normal)
        mastery = self.mastery.get(item, 0.25)
        stale = self._stale(item)
        allowed = self._allowed_actions(normal, mastery, stale)
        state = self.state_key(normal)
        q = self.q_values.get(state, {})
        scores = self._heuristic_scores(normal, mastery, stale)
        for action in allowed:
            scores[action] += q.get(action, 0.0)
        # Stable ordering is useful for a new item and keeps advice explainable.
        priority = {"slow_down": 0, "hints": 1, "repeat": 2, "review": 3, "advance": 4}
        action = max(allowed, key=lambda name: (scores[name], -priority[name]))
        if action == "slow_down":
            lower = max(40, normal["tempo"] - 5)
            text = f"降到 {lower} BPM"
        else:
            text = self.LABELS[action]
        if action == "advance":
            reason = "最近练习准确且稳定，可以尝试下一段。"
        elif action == "review":
            reason = "这段有几天没有练过，先复习一次。"
        elif action == "slow_down":
            reason = "先把错音减少，再逐步加速。"
        elif action == "hints":
            reason = "保留亮键提示，先建立键位记忆。"
        else:
            reason = "保持当前速度，再巩固一轮。"
        return {
            "action": action,
            "label": self.LABELS[action],
            "text": text,
            "message": f"教练建议：{text}",
            "reason": reason,
            "mastery": round(mastery, 3),
            "state": state,
            "allowed_actions": allowed,
            "tempo": max(40, normal["tempo"] - 5) if action == "slow_down" else normal["tempo"],
            "show_hints": action in ("hints", "slow_down") or normal["hint_used"],
        }

    def _reward(self, context: Mapping[str, Any], before: float, after: float) -> float:
        reward = (after - before) * 3.0
        reward += min(context["streak"], 3) * 0.15
        reward -= min(context["mistakes"], 6) * 0.18
        if context["accuracy"] >= 0.9:
            reward += 0.25
        if context["duration"] > 0 and context["duration"] < 5:
            reward -= 0.05
        return self._clamp(reward, -2.0, 2.0)

    def record_attempt(self, context: Mapping[str, Any] | str | None = None, **values: Any) -> dict[str, Any]:
        """Record one completed attempt and update mastery and Q values."""
        normal = self._context(context, **values)
        item = self._item_key(normal)
        before = self.mastery.get(item, 0.25)
        old_state = self.state_key(normal)
        quality = normal["accuracy"]
        quality -= min(0.3, normal["mistakes"] * 0.05)
        quality += min(0.15, normal["streak"] * 0.04)
        after = self._clamp(before + 0.30 * (self._clamp(quality) - before))
        self.mastery[item] = round(after, 6)

        action = str(normal.get("action") or normal.get("selected_action") or self.last_actions.get(item) or
                     self.recommend(normal)["action"])
        if action not in self.ACTIONS:
            action = "repeat"
        reward = self._reward(normal, before, after)
        next_state = self.state_key(normal)
        table = self.q_values.setdefault(old_state, {})
        current = table.get(action, 0.0)
        next_values = self.q_values.get(next_state, {})
        future = max(next_values.values(), default=0.0)
        table[action] = round(self._clamp(current + 0.35 * (reward + 0.80 * future - current), -2.0, 2.0), 6)
        self.last_actions[item] = action
        entry = {
            "time": self._timestamp(normal.get("time")),
            "item": item,
            "piece": normal["piece"],
            "segment": normal["segment"],
            "accuracy": round(normal["accuracy"], 4),
            "mistakes": normal["mistakes"],
            "streak": normal["streak"],
            "tempo": normal["tempo"],
            "hint_used": normal["hint_used"],
            "duration": round(normal["duration"], 2),
            "action": action,
            "reward": round(reward, 4),
            "mastery": self.mastery[item],
        }
        self.history.append(entry)
        if len(self.history) > self.history_limit:
            del self.history[:-self.history_limit]
        result = self.recommend(normal)
        result.update({"recorded": entry, "reward": round(reward, 4), "mastery": self.mastery[item]})
        return result

    def to_dict(self) -> dict[str, Any]:
        """Return a bounded JSON-compatible representation for config.json."""
        return {
            "version": self.VERSION,
            "history_limit": self.history_limit,
            "mastery": dict(self.mastery),
            "q_values": {state: dict(values) for state, values in self.q_values.items()},
            "history": list(self.history[-self.history_limit:]),
            "last_actions": dict(self.last_actions),
        }

    settings = to_dict

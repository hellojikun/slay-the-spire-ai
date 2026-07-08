"""Combat potion policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .combat_search import find_best_combat_sequence
from .model import PotionTempoModel
from .policy_decision import Decision
from .static_knowledge import StaticKnowledge


MonsterTargetFn = Callable[[list[dict[str, Any]]], int | None]
ChooseTargetFn = Callable[[list[dict[str, Any]], int], tuple[int | None, dict[str, Any] | None]]
LongFightFn = Callable[[list[dict[str, Any]]], bool]
DangerousScalingFightFn = Callable[[dict[str, Any], list[dict[str, Any]]], bool]
LiquidMemoriesTargetFn = Callable[[dict[str, Any], list[dict[str, Any]], int, int, int], dict[str, Any] | None]


class DuplicationPotionTargetFn(Protocol):
    def __call__(
        self,
        game: dict[str, Any],
        monsters: list[dict[str, Any]],
        *,
        incoming_sensitive: bool,
    ) -> bool: ...


EMERGENCY_TEMPO_POTION_TOKENS = (
    "fire",
    "explosive",
    "attack",
    "distilledchaos",
    "energy",
    "gambler",
    "gamblersbrew",
    "snecko",
    "swift",
    "power",
    "steroid",
    "strength",
    "dexterity",
    "speed",
    "blessingoftheforge",
    "cultist",
    "forge",
    "liquidbronze",
    "bronze",
    "thorn",
)
POTION_MODEL_MIN_EXAMPLES = 30
POTION_MODEL_ASSIST_THRESHOLD = 0.55
POTION_MODEL_PILOT_THRESHOLD = 0.48
POTION_MODEL_MIN_PROJECTED_LOSS = 8


class PotionPolicy:
    def __init__(
        self,
        *,
        highest_attack_target: MonsterTargetFn,
        choose_target: ChooseTargetFn,
        is_long_fight: LongFightFn,
        is_dangerous_early_scaling_fight: DangerousScalingFightFn,
        has_duplication_potion_target: DuplicationPotionTargetFn,
        liquid_memories_target: LiquidMemoriesTargetFn,
        potion_tempo_model: PotionTempoModel | None = None,
        model_authority: str = "shadow",
        static_knowledge: StaticKnowledge | None = None,
    ) -> None:
        self.highest_attack_target = highest_attack_target
        self.choose_target = choose_target
        self.is_long_fight = is_long_fight
        self.is_dangerous_early_scaling_fight = is_dangerous_early_scaling_fight
        self.has_duplication_potion_target = has_duplication_potion_target
        self.liquid_memories_target = liquid_memories_target
        self.potion_tempo_model = potion_tempo_model
        self.model_authority = _normalize_model_authority(model_authority)
        self.static_knowledge = static_knowledge

    def strategic_combat_potion(self, game: dict[str, Any], monsters: list[dict[str, Any]]) -> Decision | None:
        combat = game.get("combat_state", {})
        turn = int(combat.get("turn", 1) or 1)
        if turn > 2:
            return None
        long_fight = self.is_long_fight(monsters)
        dangerous_scaling_fight = self.is_dangerous_early_scaling_fight(game, monsters)
        if not long_fight and not dangerous_scaling_fight:
            return None
        reason_prefix = "Long boss/elite fight" if long_fight else "Dangerous early fight"
        for slot, potion in enumerate(game.get("potions", []), start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if "cultist" in key or "strength" in key or "steroid" in key:
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"{reason_prefix}; use {potion.get('name', potion.get('id'))}.",
                )
        if not long_fight:
            return None
        for slot, potion in enumerate(game.get("potions", []), start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if "blessingoftheforge" in key or "forge" in key:
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"Long boss/elite fight; use {potion.get('name', potion.get('id'))}.",
                )
        for slot, potion in enumerate(game.get("potions", []), start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if "duplication" in key and self.has_duplication_potion_target(game, monsters, incoming_sensitive=False):
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"Long boss/elite fight; use {potion.get('name', potion.get('id'))} before a high-impact card.",
                )
        return None

    def emergency_potion(
        self,
        game: dict[str, Any],
        monsters: list[dict[str, Any]],
        incoming: int,
        current_block: int,
        hp_ratio: float,
    ) -> Decision | None:
        potions = game.get("potions", [])
        current_hp = int(game.get("current_hp", 0) or game.get("combat_state", {}).get("player", {}).get("current_hp", 0))
        projected_loss = max(0, incoming - current_block)
        lethal = projected_loss >= current_hp
        high_damage = projected_loss >= max(18, int(current_hp * 0.30))
        low_hp = hp_ratio <= 0.35
        defensive_danger = projected_loss > 0 and (lethal or high_damage or low_hp)
        tempo_danger = lethal or high_damage or low_hp
        if not tempo_danger:
            return None

        target = self.highest_attack_target(monsters) or 1
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if ("regen" in key or "fruitjuice" in key or "blood" in key) and (low_hp or defensive_danger):
                return Decision([{"action": "use_potion", "potion_slot": slot}], f"Low HP; use {potion.get('name', potion.get('id'))}.")
        if _non_potion_search_handles_danger(game, incoming, current_block, current_hp):
            return None
        model_decision = self._model_assist_potion(
            game,
            monsters,
            incoming=incoming,
            current_block=current_block,
            hp_ratio=hp_ratio,
            defensive_danger=defensive_danger,
        )
        if model_decision is not None:
            return model_decision
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if defensive_danger and ("weak" in key or "fear" in key):
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot, "target_index": target}],
                    f"Dangerous incoming damage; use {potion.get('name', potion.get('id'))}.",
                )
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if defensive_danger and ("essenceofsteel" in key or "heartofiron" in key or "block" in key or "metallicize" in key):
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"Dangerous incoming damage; use {potion.get('name', potion.get('id'))}.",
                )
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if (
                defensive_danger
                and "duplication" in key
                and self.has_duplication_potion_target(game, monsters, incoming_sensitive=True)
            ):
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"Dangerous incoming damage; use {potion.get('name', potion.get('id'))} before a high-impact card.",
                )
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if defensive_danger and "liquidmemories" in key:
                target_card = self.liquid_memories_target(game, monsters, incoming, current_block, current_hp)
            else:
                target_card = None
            if target_card:
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"Dangerous incoming damage; use {potion.get('name', potion.get('id'))} to recover {target_card.get('name', target_card.get('id'))}.",
                )
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if defensive_danger and "skill" in key:
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"Dangerous incoming damage; use {potion.get('name', potion.get('id'))} to look for defense.",
                )
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if any(token in key for token in EMERGENCY_TEMPO_POTION_TOKENS):
                action = {"action": "use_potion", "potion_slot": slot}
                if potion.get("requires_target") or any(token in key for token in ("fire", "attack")):
                    potion_target = self.choose_target(monsters, 20)[0] if any(token in key for token in ("fire", "attack")) else target
                    action["target_index"] = potion_target or target
                return Decision([action], f"Emergency tempo; use {potion.get('name', potion.get('id'))}.")
        return None

    def _model_assist_potion(
        self,
        game: dict[str, Any],
        monsters: list[dict[str, Any]],
        *,
        incoming: int,
        current_block: int,
        hp_ratio: float,
        defensive_danger: bool,
    ) -> Decision | None:
        if self.model_authority not in {"assist", "pilot"}:
            return None
        model = self.potion_tempo_model
        if model is None or not model.feature_weights:
            return None
        examples = _safe_int((model.metadata or {}).get("examples"))
        if examples < POTION_MODEL_MIN_EXAMPLES:
            return None
        projected_loss = max(0, incoming - current_block)
        if projected_loss < POTION_MODEL_MIN_PROJECTED_LOSS and hp_ratio > 0.35:
            return None
        if not (defensive_danger or _elite_or_boss_context(game, monsters)):
            return None
        row = self._potion_model_row(game, monsters, incoming=incoming, current_block=current_block, hp_ratio=hp_ratio)
        try:
            score = model.score_row(row)
        except Exception:
            return None
        threshold = POTION_MODEL_PILOT_THRESHOLD if self.model_authority == "pilot" else POTION_MODEL_ASSIST_THRESHOLD
        if score < threshold:
            return None
        choice = _model_assist_potion_choice(game.get("potions", []), monsters, defensive_danger=defensive_danger)
        if choice is None:
            return None
        action, potion = choice
        return Decision(
            [action],
            f"Potion tempo model assist; use {potion.get('name', potion.get('id'))} (score {score:.2f}).",
            metadata={
                "potion_model_assist": {
                    "surface": "potion_tempo",
                    "level": self.model_authority,
                    "runtime_authority": True,
                    "decision_influence": "potion_use_action",
                    "direct_mcp_control": False,
                    "score": score,
                    "threshold": threshold,
                    "examples": examples,
                    "training_source_quality": (model.metadata or {}).get("training_source_quality"),
                    "projected_loss": projected_loss,
                    "defensive_danger": defensive_danger,
                }
            },
        )

    def _potion_model_row(
        self,
        game: dict[str, Any],
        monsters: list[dict[str, Any]],
        *,
        incoming: int,
        current_block: int,
        hp_ratio: float,
    ) -> dict[str, Any]:
        combat = game.get("combat_state") if isinstance(game.get("combat_state"), dict) else {}
        row: dict[str, Any] = {
            "character": game.get("class") or game.get("character"),
            "ascension": game.get("ascension_level") or game.get("ascension"),
            "floor": game.get("floor"),
            "act": game.get("act"),
            "turn": combat.get("turn"),
            "hp_ratio": hp_ratio,
            "incoming": incoming,
            "current_block": current_block,
            "potion_ids": [
                potion.get("id") or potion.get("name")
                for potion in game.get("potions", [])
                if isinstance(potion, dict) and not potion.get("is_empty")
            ],
            "enemy_ids": [
                monster.get("id") or monster.get("name")
                for monster in monsters
                if isinstance(monster, dict)
            ],
        }
        knowledge = self.static_knowledge
        if knowledge is not None:
            row.update(knowledge.potion_features(game.get("potions") or []))
            row.update(knowledge.monster_features(monsters))
            row.update(knowledge.boss_features(monsters, act=_safe_int(game.get("act"))))
            row.update(knowledge.relic_features(game.get("relics") or game.get("relic_items") or []))
        return row


def _potion_key(potion: dict[str, Any]) -> str:
    return "".join(ch for ch in str(potion.get("id") or potion.get("name") or "").lower() if ch.isalnum())


def _normalize_model_authority(value: str | None) -> str:
    normalized = str(value or "shadow").lower()
    if normalized in {"shadow", "assist", "pilot"}:
        return normalized
    return "shadow"


def _elite_or_boss_context(game: dict[str, Any], monsters: list[dict[str, Any]]) -> bool:
    if _safe_int(game.get("act")) == 1 and _safe_int(game.get("floor")) >= 16:
        return True
    for monster in monsters:
        if not isinstance(monster, dict):
            continue
        label = f"{monster.get('id', '')} {monster.get('name', '')}".replace(" ", "").lower()
        if any(token in label for token in ("hexaghost", "slimeboss", "theguardian", "gremlinnob", "sentry", "lagavulin")):
            return True
    return False


def _model_assist_potion_choice(
    potions: list[dict[str, Any]],
    monsters: list[dict[str, Any]],
    *,
    defensive_danger: bool,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    priority_groups = (
        ("weak", "fear"),
        ("essenceofsteel", "heartofiron", "block", "metallicize", "speed", "dexterity"),
        ("liquidmemories", "skill", "swift", "gambler", "gamblersbrew", "energy"),
        ("fire", "explosive", "attack", "distilledchaos", "power", "colorless", "steroid", "strength", "cultist"),
    )
    target = _highest_visible_target(monsters)
    for group in priority_groups:
        for slot, potion in enumerate(potions, start=1):
            if not isinstance(potion, dict) or potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if not any(token in key for token in group):
                continue
            if not defensive_danger and any(token in key for token in ("weak", "fear", "block", "essenceofsteel")):
                continue
            action = {"action": "use_potion", "potion_slot": slot}
            if potion.get("requires_target") or any(token in key for token in ("fire", "attack", "weak", "fear")):
                action["target_index"] = target or 1
            return action, potion
    return None


def _highest_visible_target(monsters: list[dict[str, Any]]) -> int | None:
    best: tuple[int, int] | None = None
    for index, monster in enumerate(monsters, start=1):
        if not isinstance(monster, dict) or monster.get("is_dead") or monster.get("is_gone"):
            continue
        attack = _safe_int((monster.get("move") or {}).get("damage"))
        hp = _safe_int(monster.get("current_hp") or monster.get("hp"))
        score = attack * 10 + hp
        if best is None or score > best[0]:
            best = (score, index)
    return best[1] if best else None


def _non_potion_search_handles_danger(
    game: dict[str, Any],
    incoming: int,
    current_block: int,
    current_hp: int,
) -> bool:
    current_loss = max(0, incoming - current_block)
    if current_loss <= 0:
        return True
    result = find_best_combat_sequence(game)
    if result is None or result.projected_loss >= current_hp:
        return False
    if result.projected_loss == 0:
        return True
    loss_reduction = current_loss - result.projected_loss
    if result.attacks_removed >= incoming and loss_reduction > 0:
        return True
    if _act1_boss_context(game) and loss_reduction >= max(8, int(current_hp * 0.12)):
        return result.projected_loss <= max(8, int(current_hp * 0.25))
    return loss_reduction >= max(8, int(current_hp * 0.25)) and result.projected_loss <= max(5, int(current_hp * 0.20))


def _act1_boss_context(game: dict[str, Any]) -> bool:
    if _safe_int(game.get("act")) == 1 and _safe_int(game.get("floor")) >= 16:
        return True
    combat = game.get("combat_state") if isinstance(game.get("combat_state"), dict) else {}
    monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
    for monster in monsters:
        if not isinstance(monster, dict):
            continue
        label = f"{monster.get('id', '')} {monster.get('name', '')}".replace(" ", "").lower()
        if any(boss in label for boss in ("hexaghost", "slimeboss", "theguardian")):
            return True
    return False


def _safe_int(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

"""One-turn combat search for measurable card sequences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain.monsters import monster_attack_damage


AOE_ATTACK_CARDS = {"Cleave", "Immolate", "Reaper", "Thunderclap", "Whirlwind"}
ATTACK_BLOCK_CARDS = {"Dash", "Iron Wave", "Just Lucky", "Wallop"}
ENERGY_GAIN_CARDS = {"Seeing Red": 2}
SELF_DAMAGE_CARDS = {"Hemokinesis": 2}
RETALIATION_DAMAGE_CARDS = {"Flame Barrier": 4}
REFLECT_DAMAGE_POWER_IDS = ("sharphide", "thorns")
STRENGTH_DOWN_CARDS = {"Disarm": 2, "Shockwave": 3}
STRENGTH_DOWN_UPGRADE_BONUS = {"Disarm": 1, "Shockwave": 2}
VULNERABLE_CARDS = {"Bash": 2, "Shockwave": 3, "Thunderclap": 1, "Uppercut": 1}
VULNERABLE_UPGRADE_BONUS = {"Bash": 1, "Shockwave": 2, "Uppercut": 1}
END_TURN_BLOCK_POWERS = {"Metallicize": 3}
WEAK_CARDS = {
    "Blind": 2,
    "Clothesline": 2,
    "Go for the Eyes": 1,
    "Intimidate": 1,
    "Leg Sweep": 2,
    "Neutralize": 1,
    "Shockwave": 3,
    "Sucker Punch": 1,
    "Uppercut": 1,
}
WEAK_UPGRADE_BONUS = {
    "Blind": 1,
    "Clothesline": 1,
    "Go for the Eyes": 1,
    "Leg Sweep": 1,
    "Neutralize": 1,
    "Shockwave": 2,
    "Uppercut": 1,
}
WEAK_ALL_CARDS = {"Blind", "Intimidate", "Shockwave"}
STRENGTH_DOWN_ALL_CARDS = {"Shockwave"}
VULNERABLE_ALL_CARDS = {"Shockwave", "Thunderclap"}
UNSUPPORTED_SEQUENCE_CARDS = {
    "Armaments",
    "Burning Pact",
    "Fiend Fire",
    "Havoc",
    "Second Wind",
    "True Grit",
    "Warcry",
}


@dataclass(frozen=True)
class SearchResult:
    first_action: dict[str, Any]
    sequence: tuple[dict[str, Any], ...]
    sequence_card_keys: tuple[str, ...]
    score: float
    initial_loss: int
    projected_loss: int
    kills: int
    attacks_removed: int
    avoided_lethal: bool
    retaliation_damage: int
    first_card_key: str
    reason: str


@dataclass(frozen=True)
class _Candidate:
    hand_index: int
    card: dict[str, Any]


@dataclass
class _MonsterState:
    hp: int
    block: int
    attack: int
    attack_hits: int = 1
    has_weak: bool = False
    has_vulnerable: bool = False
    artifact: int = 0
    mode_shift: int | None = None
    split_threshold: float | None = None
    rage_on_skill: int = 0
    reflect_damage: int = 0

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class _SearchState:
    energy: int
    block: int
    monsters: list[_MonsterState]
    hand: tuple[_Candidate, ...] = ()
    player_vulnerable: bool = False
    damage_dealt: int = 0
    retaliation_damage: int = 0
    kills: int = 0
    self_damage: int = 0


def find_best_combat_sequence(game: dict[str, Any], *, max_depth: int = 5, max_branch: int = 7) -> SearchResult | None:
    combat = game.get("combat_state", {})
    player = combat.get("player", {})
    hand = combat.get("hand", [])
    monsters = [_monster_state(monster) for monster in combat.get("monsters", []) if not _monster_gone(monster)]
    if not hand or not monsters:
        return None

    hp = _as_int(player.get("current_hp", game.get("current_hp", 0)))
    energy = max(0, _as_int(player.get("current_energy", 0)))
    block = max(0, _as_int(player.get("block", 0)))
    initial_incoming = _incoming(monsters)
    initial_hand = tuple(_Candidate(index, card) for index, card in enumerate(hand, start=1))
    initial_loss = max(0, initial_incoming - block) + _end_turn_status_damage(initial_hand)
    initial_total_attack = initial_incoming

    candidates = [
        _Candidate(index, card)
        for index, card in enumerate(hand, start=1)
        if _is_supported_candidate(card, energy)
    ]
    if not candidates:
        return None
    candidates = sorted(candidates, key=_candidate_sort_key, reverse=True)[:max_branch]

    best: tuple[float, tuple[_Candidate, ...], _SearchState] | None = None

    def visit(state: _SearchState, remaining: tuple[_Candidate, ...], sequence: tuple[_Candidate, ...]) -> None:
        nonlocal best
        if sequence:
            score = _score_state(state, hp, initial_loss, initial_total_attack)
            if best is None or score > best[0]:
                best = (score, sequence, _copy_state(state))
        if len(sequence) >= max_depth:
            return
        for offset, candidate in enumerate(remaining):
            cost = _card_cost(candidate.card, state.energy)
            if cost is None or cost > state.energy:
                continue
            next_state = _copy_state(state)
            _apply_card(next_state, candidate)
            next_remaining = remaining[:offset] + remaining[offset + 1 :]
            visit(next_state, next_remaining, sequence + (candidate,))

    visit(
        _SearchState(
            energy=energy,
            block=block,
            monsters=[_copy_monster(monster) for monster in monsters],
            hand=initial_hand,
            player_vulnerable=_power_amount(player, "vulnerable") > 0,
        ),
        tuple(candidates),
        (),
    )
    if best is None:
        return None

    score, sequence, final_state = best
    final_incoming = _incoming(final_state.monsters)
    projected_loss = _projected_total_loss(final_state)
    attacks_removed = max(0, initial_total_attack - final_incoming)
    avoided_lethal = initial_loss >= hp and projected_loss < hp
    first = sequence[0]
    first_action = _action_for(first, monsters)
    sequence_actions = tuple(_action_for(candidate, monsters) for candidate in sequence)
    reason = (
        f"sequence {[ _card_key(candidate.card) for candidate in sequence ]} "
        f"loss {initial_loss}->{projected_loss}, damage={final_state.damage_dealt}, "
        f"retaliation_damage={final_state.retaliation_damage}, "
        f"kills={final_state.kills}, attacks_removed={attacks_removed}, "
        f"avoided_lethal={avoided_lethal}, score={score:.1f}"
    )
    return SearchResult(
        first_action=first_action,
        sequence=sequence_actions,
        sequence_card_keys=tuple(_card_key(candidate.card) for candidate in sequence),
        score=score,
        initial_loss=initial_loss,
        projected_loss=projected_loss,
        kills=final_state.kills,
        attacks_removed=attacks_removed,
        avoided_lethal=avoided_lethal,
        retaliation_damage=final_state.retaliation_damage,
        first_card_key=_card_key(first.card),
        reason=reason,
    )


def _is_supported_candidate(card: dict[str, Any], current_energy: int) -> bool:
    if not card.get("is_playable", True):
        return False
    if _card_key(card) in UNSUPPORTED_SEQUENCE_CARDS:
        return False
    cost = _card_cost(card, current_energy)
    if cost is None or cost > current_energy:
        return False
    return (
        _card_damage(card, current_energy) > 0
        or _card_block(card) > 0
        or _energy_gain(card) > 0
        or _card_weak(card) > 0
        or _card_strength_down(card) > 0
        or _card_vulnerable(card) > 0
    )


def _candidate_sort_key(candidate: _Candidate) -> tuple[int, ...]:
    card = candidate.card
    return (
        _energy_gain(card),
        _card_strength_down(card),
        _card_vulnerable(card),
        _card_weak(card),
        _card_block(card),
        _card_damage(card, 3),
        -max(0, _as_int(card.get("cost", 0))),
    )


def _apply_card(state: _SearchState, candidate: _Candidate) -> None:
    card = candidate.card
    cost = _card_cost(card, state.energy)
    if cost is None:
        return
    state.hand = tuple(item for item in state.hand if item.hand_index != candidate.hand_index)
    spent = min(cost, state.energy)
    state.energy -= spent
    state.energy += _energy_gain(card)
    state.block += _card_block(card)
    state.self_damage += _self_damage(card)
    state.retaliation_damage += _card_retaliation_damage(card, state.monsters)
    if _is_skill(card):
        _apply_skill_reactive_attack_gain(state)

    damage = _card_damage(card, spent if _is_x_cost(card) else state.energy)
    weak = _card_weak(card)
    strength_down = _card_strength_down(card)
    vulnerable = _card_vulnerable(card)
    if damage > 0:
        if _is_aoe(card):
            state.self_damage += sum(_reflect_damage_for_attack(monster, damage) for monster in state.monsters)
            for monster in state.monsters:
                if monster.alive:
                    state.damage_dealt += _deal_damage(monster, damage)
                    if not monster.alive:
                        state.kills += 1
            if weak and _is_weak_all(card):
                _apply_weak_to_all(state.monsters)
            if vulnerable and _is_vulnerable_all(card):
                _apply_vulnerable_to_all(state.monsters)
            if strength_down and _is_strength_down_all(card):
                _apply_strength_down_to_all(state.monsters, strength_down)
            return
        target = _choose_target(state.monsters, damage)
        if target is None:
            return
        state.self_damage += _reflect_damage_for_attack(target, damage)
        before_alive = target.alive
        state.damage_dealt += _deal_damage(target, damage)
        if before_alive and not target.alive:
            state.kills += 1
        elif weak:
            _apply_weak(target)
        if strength_down:
            _apply_strength_down(target, strength_down)
        if vulnerable:
            _apply_vulnerable(target, vulnerable)
        return
    if weak:
        if _is_weak_all(card):
            _apply_weak_to_all(state.monsters)
            if vulnerable and _is_vulnerable_all(card):
                _apply_vulnerable_to_all(state.monsters)
            if strength_down and _is_strength_down_all(card):
                _apply_strength_down_to_all(state.monsters, strength_down)
            return
        target = _choose_target(state.monsters, 0)
        if target is not None:
            _apply_weak(target)
        return
    if strength_down:
        target = _choose_target(state.monsters, 0)
        if target is not None:
            _apply_strength_down(target, strength_down)
        return
    if vulnerable:
        target = _choose_target(state.monsters, 0)
        if target is not None:
            _apply_vulnerable(target, vulnerable)


def _score_state(state: _SearchState, hp: int, initial_loss: int, initial_total_attack: int) -> float:
    final_incoming = _incoming(state.monsters)
    projected_loss = _projected_total_loss(state)
    loss_reduction = initial_loss - projected_loss
    attacks_removed = max(0, initial_total_attack - final_incoming)
    score = (
        state.damage_dealt * 0.75
        + state.retaliation_damage * 0.6
        + state.kills * 18.0
        + attacks_removed * 1.5
        + loss_reduction * 4.0
    )
    score -= state.self_damage * 3.0
    fight_ended = _fight_ended(state)
    if initial_loss >= hp and projected_loss < hp:
        score += 200
    if initial_loss > 0 and projected_loss == 0:
        score += 20
    if projected_loss >= hp:
        score -= 500
    remaining_hp = hp - projected_loss
    if not fight_ended:
        score -= _low_survival_buffer_penalty(hp, projected_loss)
    if 0 < remaining_hp <= 3 and not fight_ended:
        score -= 80
    elif 3 < remaining_hp <= 5 and not fight_ended:
        score -= 35
    if state.self_damage > 0 and 0 < remaining_hp <= 6 and not fight_ended:
        score -= 30
    score -= max(0, state.energy) * 0.05
    return score


def _low_survival_buffer_penalty(hp: int, projected_loss: int) -> float:
    if hp <= 0 or projected_loss <= 0 or projected_loss >= hp:
        return 0.0
    remaining_hp = hp - projected_loss
    danger_buffer = max(6, int(hp * 0.25))
    if remaining_hp <= danger_buffer:
        return float((danger_buffer - remaining_hp + 1) * 12)
    caution_buffer = max(12, int(hp * 0.40))
    heavy_loss = max(12, int(hp * 0.45))
    if projected_loss >= heavy_loss and remaining_hp <= caution_buffer:
        return float(min(90, (caution_buffer - remaining_hp + 1) * 6))
    return 0.0


def _action_for(candidate: _Candidate, monsters: list[_MonsterState]) -> dict[str, Any]:
    card = candidate.card
    action: dict[str, Any] = {"action": "play_card", "card_index": candidate.hand_index}
    damage = _card_damage(card, 3)
    needs_single_target = (
        not _is_aoe(card)
        and not _is_weak_all(card)
        and not _is_strength_down_all(card)
        and not _is_vulnerable_all(card)
        and (damage > 0 or _card_weak(card) > 0 or _card_strength_down(card) > 0 or _card_vulnerable(card) > 0)
    )
    if needs_single_target:
        target = _choose_target(monsters, damage)
        if target is not None:
            action["target_index"] = monsters.index(target) + 1
    return action


def _card_key(card: dict[str, Any]) -> str:
    key = str(card.get("id") or card.get("name") or "").strip()
    if "+" in key:
        return key.split("+", 1)[0].strip()
    return key


def _is_aoe(card: dict[str, Any]) -> bool:
    return _card_key(card) in AOE_ATTACK_CARDS


def _is_x_cost(card: dict[str, Any]) -> bool:
    return _as_int(card.get("cost", 0)) == -1


def _is_skill(card: dict[str, Any]) -> bool:
    return str(card.get("type") or "").upper() == "SKILL"


def _card_cost(card: dict[str, Any], current_energy: int) -> int | None:
    raw = _as_int(card.get("cost", 0))
    if raw == -1:
        return current_energy if current_energy > 0 else None
    if raw < 0:
        return 0
    return raw


def _card_damage(card: dict[str, Any], x_energy: int) -> int:
    if str(card.get("type") or "").upper() != "ATTACK":
        return 0
    damage = max(0, _as_int(card.get("damage", 0)))
    if damage <= 0:
        return 0
    if _is_x_cost(card):
        return damage * max(0, x_energy)
    return damage


def _card_block(card: dict[str, Any]) -> int:
    block = max(0, _as_int(card.get("block", 0)))
    if block:
        if str(card.get("type") or "").upper() == "ATTACK" and _card_key(card) not in ATTACK_BLOCK_CARDS:
            return 0
        return block
    return END_TURN_BLOCK_POWERS.get(_card_key(card), 0)


def _energy_gain(card: dict[str, Any]) -> int:
    return ENERGY_GAIN_CARDS.get(_card_key(card), 0)


def _self_damage(card: dict[str, Any]) -> int:
    return SELF_DAMAGE_CARDS.get(_card_key(card), 0)


def _card_retaliation_damage(card: dict[str, Any], monsters: list[_MonsterState]) -> int:
    thorns = RETALIATION_DAMAGE_CARDS.get(_card_key(card), 0)
    if thorns <= 0:
        return 0
    total = 0
    for monster in monsters:
        if not monster.alive or monster.attack <= 0:
            continue
        raw = thorns * max(1, monster.attack_hits)
        total += min(monster.hp + monster.block, raw)
    return total


def _card_weak(card: dict[str, Any]) -> int:
    key = _card_key(card)
    amount = WEAK_CARDS.get(key, 0)
    if amount and _card_is_upgraded(card):
        return amount + WEAK_UPGRADE_BONUS.get(key, 0)
    return amount


def _card_strength_down(card: dict[str, Any]) -> int:
    key = _card_key(card)
    amount = STRENGTH_DOWN_CARDS.get(key, 0)
    if amount and _card_is_upgraded(card):
        return amount + STRENGTH_DOWN_UPGRADE_BONUS.get(key, 0)
    return amount


def _card_vulnerable(card: dict[str, Any]) -> int:
    key = _card_key(card)
    amount = VULNERABLE_CARDS.get(key, 0)
    if amount and _card_is_upgraded(card):
        return amount + VULNERABLE_UPGRADE_BONUS.get(key, 0)
    return amount


def _card_is_upgraded(card: dict[str, Any]) -> bool:
    if _as_int(card.get("upgrades", card.get("times_upgraded", 0))) > 0:
        return True
    return any("+" in str(card.get(key) or "") for key in ("id", "name"))


def _is_weak_all(card: dict[str, Any]) -> bool:
    return _card_key(card) in WEAK_ALL_CARDS


def _is_strength_down_all(card: dict[str, Any]) -> bool:
    return _card_key(card) in STRENGTH_DOWN_ALL_CARDS


def _is_vulnerable_all(card: dict[str, Any]) -> bool:
    return _card_key(card) in VULNERABLE_ALL_CARDS


def _projected_total_loss(state: _SearchState) -> int:
    return state.self_damage + max(0, _incoming(state.monsters) - state.block) + _end_turn_status_damage(state.hand)


def _end_turn_status_damage(hand: tuple[_Candidate, ...]) -> int:
    return sum(_burn_damage(candidate.card) for candidate in hand)


def _burn_damage(card: dict[str, Any]) -> int:
    raw_key = str(card.get("id") or card.get("name") or "")
    key = raw_key.lower().replace("+", "")
    name = str(card.get("name") or "")
    if key != "burn" and name.strip().lower().replace("+", "") != "burn":
        return 0
    if _as_int(card.get("upgrades", 0)) > 0 or "+" in name or "+" in raw_key:
        return 4
    return 2


def _fight_ended(state: _SearchState) -> bool:
    return all(not monster.alive for monster in state.monsters)


def _monster_state(monster: dict[str, Any]) -> _MonsterState:
    attack, hits = _monster_attack_parts(monster)
    hp = monster.get("current_hp")
    if hp is None:
        hp = monster.get("hp", 0)
    return _MonsterState(
        hp=max(0, _as_int(hp)),
        block=max(0, _as_int(monster.get("block", 0))),
        attack=attack,
        attack_hits=hits,
        has_weak=_power_amount(monster, "weak") > 0,
        has_vulnerable=_power_amount(monster, "vulnerable") > 0,
        artifact=_power_amount(monster, "artifact"),
        mode_shift=_mode_shift_amount(monster),
        split_threshold=_split_threshold(monster),
        rage_on_skill=_rage_on_skill_amount(monster),
        reflect_damage=_reflect_damage_amount(monster),
    )


def _monster_gone(monster: dict[str, Any]) -> bool:
    return bool(monster.get("is_dead") or monster.get("is_gone"))


def _incoming(monsters: list[_MonsterState]) -> int:
    return sum(monster.attack for monster in monsters if monster.alive)


def _choose_target(monsters: list[_MonsterState], damage: int) -> _MonsterState | None:
    live = [monster for monster in monsters if monster.alive]
    if not live:
        return None
    return max(
        live,
        key=lambda monster: (
            _attack_removed_by_damage(monster, damage),
            _damage_kills(monster, damage),
            monster.attack > 0,
            monster.attack,
            -(monster.hp + monster.block),
        ),
    )


def _deal_damage(monster: _MonsterState, damage: int) -> int:
    hp_before = monster.hp
    effective_damage = _effective_damage(monster, damage)
    blocked = min(monster.block, effective_damage)
    monster.block -= blocked
    hp_damage = min(monster.hp, max(0, effective_damage - blocked))
    monster.hp -= hp_damage
    if monster.mode_shift is not None and monster.attack > 0 and hp_damage > 0:
        monster.mode_shift = max(0, monster.mode_shift - hp_damage)
        if monster.mode_shift <= 0:
            monster.attack = 0
    if (
        monster.split_threshold is not None
        and monster.attack > 0
        and hp_damage > 0
        and monster.hp > 0
        and hp_before > monster.split_threshold
        and monster.hp <= monster.split_threshold
    ):
        monster.attack = 0
    return blocked + hp_damage


def _reflect_damage_for_attack(monster: _MonsterState, damage: int) -> int:
    if not monster.alive or damage <= 0 or monster.reflect_damage <= 0:
        return 0
    if _damage_kills(monster, damage):
        return 0
    return monster.reflect_damage


def _attack_removed_by_damage(monster: _MonsterState, damage: int) -> int:
    if monster.attack <= 0 or damage <= 0:
        return 0
    if _damage_kills(monster, damage):
        return monster.attack
    hp_damage = max(0, _effective_damage(monster, damage) - monster.block)
    if hp_damage <= 0:
        return 0
    if monster.mode_shift is not None and hp_damage >= monster.mode_shift:
        return monster.attack
    hp_after = monster.hp - hp_damage
    if (
        monster.split_threshold is not None
        and monster.hp > monster.split_threshold
        and hp_after > 0
        and hp_after <= monster.split_threshold
    ):
        return monster.attack
    return 0


def _apply_weak_to_all(monsters: list[_MonsterState]) -> None:
    for monster in monsters:
        _apply_weak(monster)


def _apply_weak(monster: _MonsterState) -> None:
    if not monster.alive or monster.has_weak:
        return
    if monster.artifact > 0:
        monster.artifact -= 1
        return
    if monster.attack > 0:
        monster.attack = _weakened_attack(monster.attack, monster.attack_hits)
    monster.has_weak = True


def _apply_strength_down(monster: _MonsterState, amount: int) -> None:
    if not monster.alive or monster.attack <= 0 or amount <= 0:
        return
    if monster.artifact > 0:
        monster.artifact -= 1
        return
    monster.attack = max(0, monster.attack - amount * max(1, monster.attack_hits))


def _apply_strength_down_to_all(monsters: list[_MonsterState], amount: int) -> None:
    for monster in monsters:
        _apply_strength_down(monster, amount)


def _apply_vulnerable(monster: _MonsterState, amount: int) -> None:
    if not monster.alive or amount <= 0 or monster.has_vulnerable:
        return
    if monster.artifact > 0:
        monster.artifact -= 1
        return
    monster.has_vulnerable = True


def _apply_vulnerable_to_all(monsters: list[_MonsterState]) -> None:
    for monster in monsters:
        _apply_vulnerable(monster, 1)


def _apply_skill_reactive_attack_gain(state: _SearchState) -> None:
    for monster in state.monsters:
        if not monster.alive or monster.attack <= 0 or monster.rage_on_skill <= 0:
            continue
        monster.attack += _scaled_attack_gain(monster.rage_on_skill, monster.has_weak, state.player_vulnerable)


def _scaled_attack_gain(raw_gain: int, has_weak: bool, player_vulnerable: bool) -> int:
    multiplier = 1.0
    if has_weak:
        multiplier *= 0.75
    if player_vulnerable:
        multiplier *= 1.5
    return max(0, int(raw_gain * multiplier))


def _weakened_attack(attack: int, hits: int) -> int:
    if attack <= 0:
        return 0
    if hits > 1 and attack % hits == 0:
        return int((attack // hits) * 0.75) * hits
    return int(attack * 0.75)


def _damage_kills(monster: _MonsterState, damage: int) -> bool:
    return _effective_damage(monster, damage) >= monster.hp + monster.block


def _effective_damage(monster: _MonsterState, damage: int) -> int:
    if damage <= 0:
        return 0
    if monster.has_vulnerable:
        return int(damage * 1.5)
    return damage


def _copy_state(state: _SearchState) -> _SearchState:
    return _SearchState(
        energy=state.energy,
        block=state.block,
        monsters=[_copy_monster(monster) for monster in state.monsters],
        hand=state.hand,
        player_vulnerable=state.player_vulnerable,
        damage_dealt=state.damage_dealt,
        retaliation_damage=state.retaliation_damage,
        kills=state.kills,
        self_damage=state.self_damage,
    )


def _copy_monster(monster: _MonsterState) -> _MonsterState:
    return _MonsterState(
        hp=monster.hp,
        block=monster.block,
        attack=monster.attack,
        attack_hits=monster.attack_hits,
        has_weak=monster.has_weak,
        has_vulnerable=monster.has_vulnerable,
        artifact=monster.artifact,
        mode_shift=monster.mode_shift,
        split_threshold=monster.split_threshold,
        rage_on_skill=monster.rage_on_skill,
        reflect_damage=monster.reflect_damage,
    )


def _monster_attack_parts(monster: dict[str, Any]) -> tuple[int, int]:
    move = monster.get("move")
    if isinstance(move, dict) and "damage" in move:
        damage = max(0, _as_int(move.get("damage", 0)))
        hits = max(1, _as_int(move.get("hits", 1)))
        return damage * hits, hits
    if "damage" in monster:
        damage = max(0, _as_int(monster.get("damage", 0)))
        hits = max(1, _as_int(monster.get("hits", 1)))
        return damage * hits, hits
    return max(0, monster_attack_damage(monster)), 1


def _mode_shift_amount(monster: dict[str, Any]) -> int | None:
    amount = _power_amount(monster, "modeshift")
    if amount > 0:
        return amount
    return None


def _split_threshold(monster: dict[str, Any]) -> float | None:
    max_hp = _as_int(monster.get("max_hp"))
    if max_hp <= 0:
        return None
    has_split_power = any(
        str(power.get("id") or power.get("name") or "").replace(" ", "").lower() == "split"
        for power in monster.get("powers", []) or []
    )
    label = f"{monster.get('id', '')} {monster.get('name', '')}".lower()
    if not has_split_power and ("slime" not in label or max_hp < 30):
        return None
    return max_hp / 2


def _rage_on_skill_amount(monster: dict[str, Any]) -> int:
    for power_id in ("anger", "rage", "enrage"):
        amount = _power_amount(monster, power_id)
        if amount > 0:
            return amount
    return 0


def _reflect_damage_amount(monster: dict[str, Any]) -> int:
    return sum(_power_amount(monster, power_id) for power_id in REFLECT_DAMAGE_POWER_IDS)


def _power_amount(monster: dict[str, Any], power_id: str) -> int:
    expected = power_id.replace(" ", "").lower()
    for power in monster.get("powers", []) or []:
        key = str(power.get("id") or power.get("name") or "").replace(" ", "").lower()
        if key == expected:
            return max(0, _as_int(power.get("amount", 0)))
    return 0


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

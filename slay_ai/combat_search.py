"""One-turn combat search for measurable card sequences."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain.monsters import monster_attack_damage


AOE_ATTACK_CARDS = {"Cleave", "Immolate", "Reaper", "Thunderclap", "Whirlwind"}
ATTACK_BLOCK_CARDS = {"Dash", "Iron Wave", "Just Lucky", "Wallop"}
ENERGY_GAIN_CARDS = {"Seeing Red": 2}
SELF_DAMAGE_CARDS = {"Hemokinesis": 2}
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
WEAK_ALL_CARDS = {"Blind", "Intimidate", "Shockwave"}
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
    artifact: int = 0
    mode_shift: int | None = None

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class _SearchState:
    energy: int
    block: int
    monsters: list[_MonsterState]
    hand: tuple[_Candidate, ...] = ()
    damage_dealt: int = 0
    kills: int = 0
    self_damage: int = 0


def find_best_combat_sequence(game: dict[str, Any], *, max_depth: int = 5, max_branch: int = 7) -> SearchResult | None:
    combat = game.get("combat_state", {})
    player = combat.get("player", {})
    hand = combat.get("hand", [])
    monsters = [_monster_state(monster) for monster in combat.get("monsters", []) if not _monster_gone(monster)]
    if len(hand) < 2 or not monsters:
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
    if len(candidates) < 2:
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
        f"loss {initial_loss}->{projected_loss}, kills={final_state.kills}, score={score:.1f}"
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
    return _card_damage(card, current_energy) > 0 or _card_block(card) > 0 or _energy_gain(card) > 0 or _card_weak(card) > 0


def _candidate_sort_key(candidate: _Candidate) -> tuple[int, int, int, int]:
    card = candidate.card
    return (
        _energy_gain(card),
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

    damage = _card_damage(card, spent if _is_x_cost(card) else state.energy)
    weak = _card_weak(card)
    if damage > 0:
        if _is_aoe(card):
            for monster in state.monsters:
                if monster.alive:
                    state.damage_dealt += _deal_damage(monster, damage)
                    if not monster.alive:
                        state.kills += 1
            if weak and _is_weak_all(card):
                _apply_weak_to_all(state.monsters)
            return
        target = _choose_target(state.monsters, damage)
        if target is None:
            return
        before_alive = target.alive
        state.damage_dealt += _deal_damage(target, damage)
        if before_alive and not target.alive:
            state.kills += 1
        elif weak:
            _apply_weak(target)
        return
    if weak:
        if _is_weak_all(card):
            _apply_weak_to_all(state.monsters)
            return
        target = _choose_target(state.monsters, 0)
        if target is not None:
            _apply_weak(target)


def _score_state(state: _SearchState, hp: int, initial_loss: int, initial_total_attack: int) -> float:
    final_incoming = _incoming(state.monsters)
    projected_loss = _projected_total_loss(state)
    loss_reduction = initial_loss - projected_loss
    attacks_removed = max(0, initial_total_attack - final_incoming)
    score = state.damage_dealt * 0.75 + state.kills * 18.0 + attacks_removed * 1.5 + loss_reduction * 4.0
    if initial_loss >= hp and projected_loss < hp:
        score += 200
    if initial_loss > 0 and projected_loss == 0:
        score += 20
    if projected_loss >= hp:
        score -= 500
    remaining_hp = hp - projected_loss
    if 0 < remaining_hp <= 3 and not _fight_ended(state):
        score -= 80
    elif 3 < remaining_hp <= 5 and not _fight_ended(state):
        score -= 35
    score -= max(0, state.energy) * 0.05
    return score


def _action_for(candidate: _Candidate, monsters: list[_MonsterState]) -> dict[str, Any]:
    card = candidate.card
    action: dict[str, Any] = {"action": "play_card", "card_index": candidate.hand_index}
    damage = _card_damage(card, 3)
    needs_single_target = not _is_aoe(card) and not _is_weak_all(card) and (damage > 0 or _card_weak(card) > 0)
    if needs_single_target:
        target = _choose_target(monsters, damage)
        if target is not None:
            action["target_index"] = monsters.index(target) + 1
    return action


def _card_key(card: dict[str, Any]) -> str:
    return str(card.get("id") or card.get("name") or "")


def _is_aoe(card: dict[str, Any]) -> bool:
    return _card_key(card) in AOE_ATTACK_CARDS


def _is_x_cost(card: dict[str, Any]) -> bool:
    return _as_int(card.get("cost", 0)) == -1


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


def _card_weak(card: dict[str, Any]) -> int:
    return WEAK_CARDS.get(_card_key(card), 0)


def _is_weak_all(card: dict[str, Any]) -> bool:
    return _card_key(card) in WEAK_ALL_CARDS


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
    return _MonsterState(
        hp=max(0, _as_int(monster.get("current_hp", 0))),
        block=max(0, _as_int(monster.get("block", 0))),
        attack=attack,
        attack_hits=hits,
        has_weak=_power_amount(monster, "weak") > 0,
        artifact=_power_amount(monster, "artifact"),
        mode_shift=_mode_shift_amount(monster),
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
            _damage_kills(monster, damage),
            monster.attack > 0,
            monster.attack,
            -(monster.hp + monster.block),
        ),
    )


def _deal_damage(monster: _MonsterState, damage: int) -> int:
    blocked = min(monster.block, damage)
    monster.block -= blocked
    hp_damage = min(monster.hp, max(0, damage - blocked))
    monster.hp -= hp_damage
    if monster.mode_shift is not None and monster.attack > 0 and hp_damage > 0:
        monster.mode_shift = max(0, monster.mode_shift - hp_damage)
        if monster.mode_shift <= 0:
            monster.attack = 0
    return blocked + hp_damage


def _apply_weak_to_all(monsters: list[_MonsterState]) -> None:
    for monster in monsters:
        _apply_weak(monster)


def _apply_weak(monster: _MonsterState) -> None:
    if not monster.alive or monster.attack <= 0 or monster.has_weak:
        return
    if monster.artifact > 0:
        monster.artifact -= 1
        return
    monster.attack = _weakened_attack(monster.attack, monster.attack_hits)
    monster.has_weak = True


def _weakened_attack(attack: int, hits: int) -> int:
    if attack <= 0:
        return 0
    if hits > 1 and attack % hits == 0:
        return int((attack // hits) * 0.75) * hits
    return int(attack * 0.75)


def _damage_kills(monster: _MonsterState, damage: int) -> bool:
    return damage >= monster.hp + monster.block


def _copy_state(state: _SearchState) -> _SearchState:
    return _SearchState(
        energy=state.energy,
        block=state.block,
        monsters=[_copy_monster(monster) for monster in state.monsters],
        hand=state.hand,
        damage_dealt=state.damage_dealt,
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
        artifact=monster.artifact,
        mode_shift=monster.mode_shift,
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

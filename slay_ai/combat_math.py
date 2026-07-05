"""Compatibility imports for combat math helpers.

New code should import from ``slay_ai.domain.monsters``.
"""

from __future__ import annotations

from .domain.monsters import incoming_damage, monster_attack_damage, monster_gone

__all__ = ["incoming_damage", "monster_attack_damage", "monster_gone"]

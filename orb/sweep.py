"""Build a grid of ORB configs for parameter sweeps."""

from __future__ import annotations

from itertools import product

from .config import StrategyConfig


def build_grid() -> list[StrategyConfig]:
    """A reasonable default search space over the core ORB parameters.

    Kept deliberately bounded so a sweep stays interpretable. Widen the lists
    here (or add feature dimensions like ema_trend_filter / trailing_stop_ticks)
    once you're working with real data.
    """
    configs: list[StrategyConfig] = []

    or_minutes = [15, 30, 45, 60]
    buffers = [1, 2]
    directions = ["both"]

    # (stop spec, target spec) pairs to explore
    range_targets = [("r_multiple", 0.5), ("r_multiple", 1.0), ("r_multiple", 1.5), ("r_multiple", 2.0)]
    fixed_combos = [(20, 20), (20, 40), (40, 40), (40, 60)]

    for om, buf, direction in product(or_minutes, buffers, directions):
        # Range stop + R-multiple target
        for ttype, rmult in range_targets:
            configs.append(
                StrategyConfig(
                    name=f"OR{om}_b{buf}_range_{rmult}R",
                    or_minutes=om,
                    entry_buffer_ticks=buf,
                    direction=direction,
                    stop_type="range",
                    target_type=ttype,
                    r_multiple=rmult,
                )
            )
        # Fixed-tick stop + fixed-tick target
        for st, tt in fixed_combos:
            configs.append(
                StrategyConfig(
                    name=f"OR{om}_b{buf}_stop{st}t_tgt{tt}t",
                    or_minutes=om,
                    entry_buffer_ticks=buf,
                    direction=direction,
                    stop_type="fixed",
                    stop_ticks=st,
                    target_type="fixed",
                    target_ticks=tt,
                )
            )

    return configs

"""H4 long-only Donchian 55 entry/20 exit; strictly previous 55/20 bars."""
from __future__ import annotations

def decide(history, state, config):
    targets={}
    signals={}
    current_nav=state.nav()
    for s in config.symbols:
        bars=list(history[s])
        if len(bars)<56:raise RuntimeError("MISSING_DONCHIAN_WARMUP")
        current=bars[-1]
        if not all(b.time_ms-bprev.time_ms==14_400_000 for bprev,b in zip(bars[:-1],bars[1:])):
            raise RuntimeError("NONCONTIGUOUS_H4_SIGNAL")
        quote24=sum(float(b.quote_volume) for b in bars[-6:])
        liquid=quote24>config.min_quote_24h
        upper55=max(b.high for b in bars[-56:-1])
        lower20=min(b.low for b in bars[-21:-1])
        owned=state.positions.get(s,0)>0
        if owned and (current.close<lower20 or not liquid):
            target=0.
            action="EXIT"
        elif owned:
            # Do not resize up an existing position; trim to the 10% cap.
            target=min(config.max_position,
                       state.positions[s]*current.close/current_nav)
            action="HOLD"
        elif liquid and current.close>upper55:
            target=config.max_position
            action="ENTER"
        else:
            target=0.
            action="FLAT"
        targets[s]=target
        signals[s]={"action":action,"close":current.close,
                    "donchian_upper_55":upper55,"donchian_lower_20":lower20,
                    "quote_volume_24h":quote24,"liquid":liquid,
                    "target_weight":target}
    return targets,signals

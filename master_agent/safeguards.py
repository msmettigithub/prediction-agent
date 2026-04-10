NEVER_MODIFY=['live/kalshi_trader.py','live/guard.py','master_agent/safeguards.py','.env','CLAUDE.md','workers/trading_brain.py']
MIN_TEST_COUNT=139
LIVE_TRADING_AUTO_ENABLE=False
def can_act(f):
    for p in NEVER_MODIFY:
        if p in str(f): return False,'Protected:'+p
    return True,None
"""Micro-lesson bank for trading discipline nudges."""

import random

_LESSONS = [
    "if the delta isn't there, close Sierra and go fix that Rakesh query. market will be here tomorrow, your money won't if you force it.",
    "you built a whole analytics SDK with patience. apply that same energy here — wait for the setup, not the dopamine.",
    "no trade today means you kept your money. that's not losing, that's winning.",
    "you wouldn't ship code without testing it. don't enter without delta confirmation.",
    "this isn't your full-time gig — you don't NEED to trade today. that's your superpower.",
    "remember why you built DH|S2 — to only trade when delta + level + VWAP align. if one is missing, you already know the answer.",
    "that 4 pip SL? that's real money. your money. earned from building products, not from gambling.",
    "the best traders are bored most of the time. go build something, come back when the setup screams at you.",
    "you didn't mass deploy untested code to prod. don't mass deploy untested trades to your account.",
    "the guys who make it aren't the smartest — they're the ones who didn't blow up learning.",
    "delta first, price second. always. price can fake, volume can't.",
    "sitting out IS a position. and today it might be the best one.",
    "one banger trade with full conviction > ten meh trades hoping for the best.",
    "the chart will be here tomorrow. your capital won't if you force it today.",
    "vibes are not an entry signal bro.",
    "you didn't trade today? good. that IS the edge.",
    "3 good trades a week beats 20 mediocre ones. quality over quantity.",
    "your SL is a promise to yourself. never move it wider.",
    "boredom is not a valid entry signal. go for a walk.",
    "that patience muscle you're building? it's worth more than any single trade.",
    "you already know the answer. if you have to ask, it's not the setup.",
    "the market rewards patience. your edge is waiting, not trading.",
    "every time you sit out a bad trade, you're compounding discipline. that compounds faster than money.",
    "your job is to wait for YOUR setup. everything else is someone else's trade.",
    "you built DH|S2 to keep you honest. let it do its job.",
    "a skipped bad trade is as valuable as a winning good one. both protect your capital.",
    "FOMO is the market's way of taking money from impatient people. don't be that person.",
    "you're a product builder who trades, not a trader who builds. act accordingly.",
    "the setup will come. it always does. the question is whether you'll have capital left when it arrives.",
    "cum delta falling + you wanting to go long = your gut fighting the tape. the tape wins.",
    "delta divergence is a whisper. wait for the shout — 3+ bars of positive delta.",
    "the morning window is 4 hours. you need ONE setup. relax.",
    "if Scotia says bullish and delta says bearish — delta wins. sentiment is context, tape is truth.",
    "you've seen what happens when you force trades. you've also seen what happens when you wait. choose.",
    "the market doesn't know you exist. it doesn't care about your analysis. it only respects your discipline.",
    "go get a chai. the level will still be there when you come back. if it's not, it wasn't your trade.",
    "every pip of SL is money you earned building myVoiceBooksAI. respect it.",
    "the urge to trade is strongest when you shouldn't. that's by design — it's how markets take money.",
    "you don't need to trade every day. you need to trade well when you trade.",
    "remember: DH|S2 has a 68% win rate BECAUSE it's selective. make it less selective and you lose the edge.",
    "the difference between a good trader and a blown account is the trades they didn't take.",
    "when all 4 checklist items are green, you won't need to convince yourself. you'll just know.",
    "hidden buying at a strong level = smart money is accumulating. but wait for them to show their hand — 3+ bars.",
    "RSI oversold doesn't mean 'buy now'. it means 'pay attention'. delta tells you when.",
    "your trading journal exists for a reason. if you can't write 'delta confirmed at a strong level' in it, don't trade.",
    "the best trade you'll ever make is the one you waited 3 hours for. the worst is the one you took in the first 5 minutes.",
    "you have 4 checks on your list. 2 are green. that's 50%. would you ship a feature that passes 50% of tests?",
    "trading is the only job where doing nothing is often the optimal action. embrace the boredom.",
    "the cum delta is a river. don't swim against it. wait for the current to change.",
    "you built this console to keep you honest. if it says STAND DOWN, stand down.",
]

_used_this_session: set = set()


def get_lesson() -> str:
    available = [l for l in _LESSONS if l not in _used_this_session]
    if not available:
        _used_this_session.clear()
        available = _LESSONS
    lesson = random.choice(available)
    _used_this_session.add(lesson)
    return lesson


def reset_session():
    _used_this_session.clear()

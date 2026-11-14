// Plain-language explainers for each paper strategy, shown in its details dialog.
// Examples use made-up round prices with NIFTY at 24,000, strikes 50 points apart and 65 units a
// lot, and leave charges out. They show the shape of the trade, not what it will make.

export type StrategyExplainer = {
  idea: string;
  how: string;
  example: string[];
  helps: string;
  risk: string;
};

export const EXAMPLE_NOTE = "Example prices are made up and charges are left out. Real prices change all the time.";

export const STRATEGY_EXPLAINERS: Record<string, StrategyExplainer> = {
  long_call: {
    idea: "A bet that NIFTY goes up. The most you can lose is what you pay.",
    how: "Buy one call at the strike nearest to NIFTY.",
    example: [
      "Buy the 24,000 call at ₹150: you pay ₹9,750 (150 × 65).",
      "NIFTY ends at 24,300: the call is worth ₹300, so you make ₹9,750.",
      "NIFTY ends at 24,150: you get your money back.",
      "NIFTY ends at or below 24,000: the call is worth nothing and you lose ₹9,750.",
    ],
    helps: "A quick, strong rise.",
    risk: "Time works against you: if NIFTY goes nowhere, the call loses value every day.",
  },
  long_put: {
    idea: "A bet that NIFTY goes down. The most you can lose is what you pay.",
    how: "Buy one put at the strike nearest to NIFTY.",
    example: [
      "Buy the 24,000 put at ₹140: you pay ₹9,100.",
      "NIFTY ends at 23,700: the put is worth ₹300, so you make ₹10,400.",
      "NIFTY ends at 23,860: you get your money back.",
      "NIFTY ends at or above 24,000: you lose ₹9,100.",
    ],
    helps: "A quick, strong fall.",
    risk: "Time works against you: if NIFTY goes nowhere, the put loses value every day.",
  },
  bull_call_spread: {
    idea: "A cheaper bet on a rise. Selling a higher call pays for part of yours, but caps the gain.",
    how: "Buy the call at NIFTY's strike and sell the next call up.",
    example: [
      "Buy 24,000 call at ₹150, sell 24,050 call at ₹120: you pay ₹30, or ₹1,950.",
      "NIFTY ends at 24,050 or higher: the spread is worth ₹50, so you make ₹1,300. That is the most.",
      "NIFTY ends at 24,030: you get your money back.",
      "NIFTY ends at or below 24,000: you lose ₹1,950. That is the most.",
    ],
    helps: "A steady move up to the higher strike.",
    risk: "A big rally earns no more than a small one.",
  },
  bear_put_spread: {
    idea: "A cheaper bet on a fall. Selling a lower put pays for part of yours, but caps the gain.",
    how: "Buy the put at NIFTY's strike and sell the next put down.",
    example: [
      "Buy 24,000 put at ₹140, sell 23,950 put at ₹112: you pay ₹28, or ₹1,820.",
      "NIFTY ends at 23,950 or lower: you make ₹1,430. That is the most.",
      "NIFTY ends at or above 24,000: you lose ₹1,820. That is the most.",
    ],
    helps: "A steady move down to the lower strike.",
    risk: "A big fall earns no more than a small one.",
  },
  bull_put_spread: {
    idea: "You get paid now and keep it if NIFTY doesn't fall. A bought put limits the loss.",
    how: "Sell the put at NIFTY's strike and buy the next put down as protection.",
    example: [
      "Sell 24,000 put at ₹140, buy 23,950 put at ₹112: you receive ₹28, or ₹1,820.",
      "NIFTY ends at or above 24,000: both expire and you keep ₹1,820.",
      "NIFTY ends at 23,950 or lower: you lose ₹1,430. That is the most.",
    ],
    helps: "NIFTY flat or rising.",
    risk: "You can lose more than you collect, and a fall takes it quickly.",
  },
  bear_call_spread: {
    idea: "You get paid now and keep it if NIFTY doesn't rise. A bought call limits the loss.",
    how: "Sell the call at NIFTY's strike and buy the next call up as protection.",
    example: [
      "Sell 24,000 call at ₹150, buy 24,050 call at ₹120: you receive ₹30, or ₹1,950.",
      "NIFTY ends at or below 24,000: you keep ₹1,950.",
      "NIFTY ends at 24,050 or higher: you lose ₹1,300. That is the most.",
    ],
    helps: "NIFTY flat or falling.",
    risk: "A sharp rally turns it into the full loss.",
  },
  iron_condor: {
    idea: "You get paid now for betting NIFTY stays inside a range. Bought options on both sides cap the loss.",
    how: "Sell a put and a call one strike either side of NIFTY, and buy protection one strike further out on each side.",
    example: [
      "Sell 23,950 put and 24,050 call, buy 23,900 put and 24,100 call: you receive ₹40, or ₹2,600.",
      "NIFTY ends between 23,950 and 24,050: everything expires and you keep ₹2,600.",
      "NIFTY ends below 23,900 or above 24,100: you lose ₹650. That is the most.",
    ],
    helps: "A quiet market that stays in a narrow range.",
    risk: "A big move either way. Wins are often small and losses come in one go.",
  },
  range_iron_condor: {
    idea: "An iron condor with a much wider range, entered only when options are expensive. You get paid for NIFTY staying inside about 1.2% either way.",
    how: "Sell a put and a call six strikes (300 points) either side of NIFTY, and buy protection six strikes further out on each side. It only enters when at-the-money implied volatility is 16% or more.",
    example: [
      "NIFTY at 25,000: sell 24,700 put and 25,300 call, buy 24,400 put and 25,600 call: you receive ₹70, or ₹4,550.",
      "NIFTY ends between 24,700 and 25,300: everything expires and you keep ₹4,550.",
      "NIFTY ends below 24,400 or above 25,600: you lose ₹14,950. That is the most.",
    ],
    helps: "A choppy market that swings but stays in a band, while option prices fall back from a high.",
    risk: "A strong trend or gap through a short strike. The loss is about three times the most it can make.",
  },
  iron_butterfly: {
    idea: "Like an iron condor, but betting NIFTY finishes very close to where it is now. More paid, narrower target.",
    how: "Sell a put and a call at NIFTY's strike, and buy protection one strike out on each side.",
    example: [
      "Sell 24,000 put and call, buy 23,950 put and 24,050 call: you receive ₹42, or ₹2,730.",
      "NIFTY ends exactly at 24,000: you keep all ₹2,730.",
      "NIFTY ends at 23,958 or 24,042: you get your money back.",
      "NIFTY ends beyond 23,950 or 24,050: you lose ₹520. That is the most.",
    ],
    helps: "NIFTY pinned at one level into expiry.",
    risk: "Almost any move eats the profit. The bid/ask spread is large next to its small stop.",
  },
  long_call_condor: {
    idea: "A small bet that NIFTY ends inside a range, made with four calls. Loss and gain are both fixed.",
    how: "Buy a call two strikes below, sell calls one strike below and above, buy a call two strikes above.",
    example: [
      "Buy 23,900 and 24,100 calls, sell 23,950 and 24,050 calls: you pay ₹10, or ₹650.",
      "NIFTY ends between 23,950 and 24,050: you make ₹2,600. That is the most.",
      "NIFTY ends below 23,900 or above 24,100: you lose ₹650. That is the most.",
    ],
    helps: "A calm market close to today's level.",
    risk: "It usually needs to be held close to expiry to pay off.",
  },
  long_put_condor: {
    idea: "The same range bet as the long call condor, built with puts.",
    how: "Buy a put two strikes below, sell puts one strike below and above, buy a put two strikes above.",
    example: [
      "Buy 23,900 and 24,100 puts, sell 23,950 and 24,050 puts: you pay ₹10, or ₹650.",
      "NIFTY ends between 23,950 and 24,050: you make ₹2,600. That is the most.",
      "NIFTY ends outside 23,900 to 24,100: you lose ₹650.",
    ],
    helps: "A calm market close to today's level.",
    risk: "It usually needs to be held close to expiry to pay off.",
  },
  short_call_condor: {
    idea: "The opposite of a long condor: a small paid bet that NIFTY breaks out of a range, made with calls.",
    how: "Sell a call two strikes below, buy calls one strike below and above, sell a call two strikes above.",
    example: [
      "Sell 23,900 and 24,100 calls, buy 23,950 and 24,050 calls: you receive ₹10, or ₹650.",
      "NIFTY ends below 23,900 or above 24,100: you keep ₹650. That is the most.",
      "NIFTY ends between 23,950 and 24,050: you lose ₹2,600. That is the most.",
    ],
    helps: "A move away from today's level, either way.",
    risk: "It risks a lot to make a little. A quiet day costs the full loss.",
  },
  short_put_condor: {
    idea: "The same breakout bet as the short call condor, built with puts.",
    how: "Sell a put two strikes below, buy puts one strike below and above, sell a put two strikes above.",
    example: [
      "Sell 23,900 and 24,100 puts, buy 23,950 and 24,050 puts: you receive ₹10, or ₹650.",
      "NIFTY ends outside 23,900 to 24,100: you keep ₹650.",
      "NIFTY ends between 23,950 and 24,050: you lose ₹2,600.",
    ],
    helps: "A move away from today's level, either way.",
    risk: "It risks a lot to make a little. A quiet day costs the full loss.",
  },
  long_call_butterfly: {
    idea: "A cheap bet that NIFTY ends right at one price. A small cost for a much bigger possible gain.",
    how: "Buy one call a strike below, sell two calls at NIFTY's strike, buy one call a strike above.",
    example: [
      "Buy 23,950 and 24,050 calls, sell two 24,000 calls: you pay ₹5, or ₹325.",
      "NIFTY ends exactly at 24,000: you make ₹2,925. That is the most.",
      "NIFTY ends below 23,950 or above 24,050: you lose ₹325.",
    ],
    helps: "NIFTY settling at today's level.",
    risk: "The winning zone is narrow, so most trades lose the small cost.",
  },
  long_put_butterfly: {
    idea: "The same pinpoint bet as the long call butterfly, built with puts.",
    how: "Buy one put a strike above, sell two puts at NIFTY's strike, buy one put a strike below.",
    example: [
      "Buy 23,950 and 24,050 puts, sell two 24,000 puts: you pay ₹5, or ₹325.",
      "NIFTY ends exactly at 24,000: you make ₹2,925.",
      "NIFTY ends outside 23,950 to 24,050: you lose ₹325.",
    ],
    helps: "NIFTY settling at today's level.",
    risk: "The winning zone is narrow, so most trades lose the small cost.",
  },
  short_call_butterfly: {
    idea: "The opposite of a long butterfly: a small paid bet that NIFTY moves away from today's price.",
    how: "Sell one call a strike below, buy two calls at NIFTY's strike, sell one call a strike above.",
    example: [
      "Sell 23,950 and 24,050 calls, buy two 24,000 calls: you receive ₹5, or ₹325.",
      "NIFTY ends below 23,950 or above 24,050: you keep ₹325.",
      "NIFTY ends exactly at 24,000: you lose ₹2,925. That is the most.",
    ],
    helps: "Any clear move away from today's level.",
    risk: "It risks a lot to make a little, and its tiny target is easily eaten by the bid/ask spread.",
  },
  short_put_butterfly: {
    idea: "The same move-away bet as the short call butterfly, built with puts.",
    how: "Sell one put a strike above, buy two puts at NIFTY's strike, sell one put a strike below.",
    example: [
      "Sell 23,950 and 24,050 puts, buy two 24,000 puts: you receive ₹5, or ₹325.",
      "NIFTY ends outside 23,950 to 24,050: you keep ₹325.",
      "NIFTY ends exactly at 24,000: you lose ₹2,925.",
    ],
    helps: "Any clear move away from today's level.",
    risk: "It risks a lot to make a little, and its tiny target is easily eaten by the bid/ask spread.",
  },
  long_straddle: {
    idea: "A bet on a big move, either up or down. You don't need to guess the direction.",
    how: "Buy a call and a put, both at the strike nearest to NIFTY.",
    example: [
      "Buy 24,000 call at ₹150 and 24,000 put at ₹140: you pay ₹290, or ₹18,850.",
      "NIFTY ends at 24,500: the call is worth ₹500, so you make ₹13,650.",
      "NIFTY ends at 23,500: the put is worth ₹500, so you make ₹13,650.",
      "NIFTY ends at 23,710 or 24,290: you get your money back.",
      "NIFTY ends at 24,000: both are worthless and you lose ₹18,850.",
    ],
    helps: "A sharp move, such as after big news.",
    risk: "It costs a lot, and loses value fast on quiet days.",
  },
  long_strangle: {
    idea: "Like a straddle, a bet on a big move either way, but using cheaper options a strike further out.",
    how: "Buy a call one strike above NIFTY and a put one strike below.",
    example: [
      "Buy 24,050 call at ₹120 and 23,950 put at ₹112: you pay ₹232, or ₹15,080.",
      "NIFTY ends at 24,500: the call is worth ₹450, so you make ₹14,170.",
      "NIFTY ends at 23,718 or 24,282: you get your money back.",
      "NIFTY ends between 23,950 and 24,050: you lose ₹15,080.",
    ],
    helps: "A sharp move either way.",
    risk: "It needs a slightly bigger move than a straddle, and quiet days cost money.",
  },
  strap: {
    idea: "A big-move bet that leans bullish: it makes about twice as much from a rise as from a fall.",
    how: "Buy two calls and one put, all at the strike nearest to NIFTY.",
    example: [
      "Buy two 24,000 calls at ₹150 and one 24,000 put at ₹140: you pay ₹440, or ₹28,600.",
      "NIFTY ends at 24,500: the calls are worth ₹1,000, so you make ₹36,400.",
      "NIFTY ends at 23,500: the put is worth ₹500, so you make ₹3,900.",
      "You get your money back at 24,220 or 23,560.",
      "NIFTY ends at 24,000: you lose ₹28,600.",
    ],
    helps: "A strong rally. A strong fall helps too, but less.",
    risk: "It costs the most of the move-seeking strategies, so a quiet day hurts.",
  },
  strip: {
    idea: "A big-move bet that leans bearish: it makes about twice as much from a fall as from a rise.",
    how: "Buy two puts and one call, all at the strike nearest to NIFTY.",
    example: [
      "Buy two 24,000 puts at ₹140 and one 24,000 call at ₹150: you pay ₹430, or ₹27,950.",
      "NIFTY ends at 23,500: the puts are worth ₹1,000, so you make ₹37,050.",
      "NIFTY ends at 24,500: the call is worth ₹500, so you make ₹4,550.",
      "You get your money back at 23,785 or 24,430.",
      "NIFTY ends at 24,000: you lose ₹27,950.",
    ],
    helps: "A sharp fall. A sharp rise helps too, but less.",
    risk: "It costs a lot, so a quiet day hurts.",
  },
  guts: {
    idea: "A big-move bet like a straddle, but using options that already have value, so part of the cost always comes back.",
    how: "Buy a call one strike below NIFTY and a put one strike above. Both are already 'in the money'.",
    example: [
      "Buy 23,950 call at ₹180 and 24,050 put at ₹170: you pay ₹350, or ₹22,750.",
      "At expiry, the pair is always worth at least ₹100, so ₹6,500 comes back. The most you can lose is ₹16,250.",
      "NIFTY ends at 24,500: the call is worth ₹550, so you make ₹13,000.",
      "You get your money back at 23,700 or 24,300.",
    ],
    helps: "A sharp move either way.",
    risk: "You pay more up front than for a straddle, and quiet days still cost money.",
  },
  put_broken_wing: {
    idea: "A put butterfly with a wider lower wing. It often starts with money in hand and pays best if NIFTY drifts to the middle strike.",
    how: "Buy a put one strike above, sell two puts at NIFTY's strike, buy a put two strikes below.",
    example: [
      "Buy 24,050 put, sell two 24,000 puts, buy 23,900 put: you receive about ₹17, or ₹1,105.",
      "NIFTY stays above 24,050: everything expires and you keep ₹1,105.",
      "NIFTY ends at 24,000: you make ₹4,355. That is the most.",
      "NIFTY ends below 23,900: you lose ₹2,145. That is the most.",
    ],
    helps: "NIFTY flat or rising, or easing down to the middle strike.",
    risk: "A sharp fall below the bottom strike.",
  },
  call_broken_wing: {
    idea: "A call butterfly with a wider upper wing: the bearish mirror of the put broken wing.",
    how: "Buy a call one strike below, sell two calls at NIFTY's strike, buy a call two strikes above.",
    example: [
      "Buy 23,950 call, sell two 24,000 calls, buy 24,100 call: you receive about ₹17, or ₹1,105.",
      "NIFTY stays below 23,950: you keep ₹1,105.",
      "NIFTY ends at 24,000: you make ₹4,355. That is the most.",
      "NIFTY ends above 24,100: you lose ₹2,145. That is the most.",
    ],
    helps: "NIFTY flat or falling, or edging up to the middle strike.",
    risk: "A sharp rally above the top strike.",
  },
  call_ratio_backspread: {
    idea: "A bet on a big rise. Selling one call pays for part of two bought calls further up.",
    how: "Sell one call at NIFTY's strike and buy two calls one strike up.",
    example: [
      "Sell 24,000 call at ₹150, buy two 24,050 calls at ₹120: you pay ₹90, or ₹5,850.",
      "NIFTY ends at 24,400: you make ₹13,650, and more the higher it goes.",
      "NIFTY ends at 24,050: you lose ₹9,100. That is the worst case.",
      "NIFTY ends below 24,000: everything expires and you lose ₹5,850.",
    ],
    helps: "A strong rally.",
    risk: "A small rise is the worst outcome, and quiet days cost money.",
  },
  put_ratio_backspread: {
    idea: "A bet on a big fall. Selling one put pays for part of two bought puts further down.",
    how: "Sell one put at NIFTY's strike and buy two puts one strike down.",
    example: [
      "Sell 24,000 put at ₹140, buy two 23,950 puts at ₹112: you pay ₹84, or ₹5,460.",
      "NIFTY ends at 23,600: you make ₹14,040, and more the lower it goes.",
      "NIFTY ends at 23,950: you lose ₹8,710. That is the worst case.",
      "NIFTY ends above 24,000: everything expires and you lose ₹5,460.",
    ],
    helps: "A sharp fall.",
    risk: "A small fall is the worst outcome, and quiet days cost money.",
  },
  inverse_iron_condor: {
    idea: "The opposite of an iron condor: you pay a little to bet NIFTY moves out of a range, with the gain capped.",
    how: "Buy a put and a call one strike either side of NIFTY, and sell options one strike further out on each side.",
    example: [
      "Buy 23,950 put and 24,050 call, sell 23,900 put and 24,100 call: you pay ₹40, or ₹2,600.",
      "NIFTY ends below 23,900 or above 24,100: you make ₹650. That is the most.",
      "NIFTY ends between 23,950 and 24,050: you lose ₹2,600. That is the most.",
    ],
    helps: "A clear move either way.",
    risk: "It risks more than it can make, so it needs moves to happen often.",
  },
  inverse_iron_butterfly: {
    idea: "Pay for a straddle and sell options further out to cut the cost: a defined-risk bet that NIFTY moves away from today's level.",
    how: "Buy a put and a call at NIFTY's strike, and sell a put and a call six strikes (300 points) out on each side.",
    example: [
      "NIFTY at 25,000: buy the 25,000 put and call for ₹300, sell the 24,700 put and 25,300 call for ₹120: you pay ₹180, or ₹11,700.",
      "NIFTY ends below 24,700 or above 25,300: you make ₹120 a unit, or ₹7,800. That is the most.",
      "NIFTY ends at 25,000: you lose ₹11,700. That is the most.",
    ],
    helps: "A clear move either way before expiry.",
    risk: "A quiet market. It needs a move of more than the ₹180 paid, about 0.7%, just to break even.",
  },
  call_calendar: {
    idea: "Sell this week's call and buy the same call for next week. You gain as this week's call loses value faster than next week's.",
    how: "Sell the call at NIFTY's strike on this expiry and buy the same strike on the next expiry. The trade is closed before this week's expiry.",
    example: [
      "NIFTY at 25,000: sell this week's 25,000 call at ₹100 and buy next week's at ₹150: you pay ₹50, or ₹3,250.",
      "NIFTY near 25,000 at this week's expiry: this week's call is nearly worthless and next week's still has value, so you gain.",
      "A big move either way: both calls move together and you can lose up to the ₹3,250 paid.",
    ],
    helps: "A calm market near the strike, and option prices that hold up or rise.",
    risk: "A big move either way, or a fall in option prices across expiries.",
  },
  calendar_put_spread: {
    idea: "The put version of a calendar: sell this week's put and buy the same put for next week, gaining as this week's put loses value faster.",
    how: "Sell the put at NIFTY's strike on this expiry and buy the same strike on the next expiry. The trade is closed before this week's expiry.",
    example: [
      "NIFTY at 25,000: sell this week's 25,000 put at ₹100 and buy next week's at ₹150: you pay ₹50, or ₹3,250.",
      "NIFTY near 25,000 at this week's expiry: this week's put is nearly worthless and next week's still has value, so you gain.",
      "A big move either way: you can lose the ₹3,250 paid and a little more, as next week's put can trade slightly below its in-the-money value.",
    ],
    helps: "A calm market near the strike, and option prices that hold up or rise.",
    risk: "A big move either way, or a fall in option prices across expiries.",
  },
  diagonal_call_spread: {
    idea: "A call calendar with a bullish tilt: buy next week's call at NIFTY's strike and sell this week's call a little higher.",
    how: "Sell the call two strikes (100 points) above NIFTY on this expiry and buy the call at NIFTY's strike on the next expiry. Closed before this week's expiry.",
    example: [
      "NIFTY at 25,000: sell this week's 25,100 call at ₹60 and buy next week's 25,000 call at ₹150: you pay ₹90, or ₹5,850.",
      "NIFTY drifts up towards 25,100 by this week's expiry: the sold call expires near worthless and the bought call gains, so you gain.",
      "NIFTY falls hard: you can lose up to the ₹5,850 paid.",
    ],
    helps: "A slow rise towards the sold strike.",
    risk: "A sharp fall, or a fall in option prices.",
  },
  diagonal_put_spread: {
    idea: "A put calendar with a bearish tilt: buy next week's put at NIFTY's strike and sell this week's put a little lower.",
    how: "Sell the put two strikes (100 points) below NIFTY on this expiry and buy the put at NIFTY's strike on the next expiry. Closed before this week's expiry.",
    example: [
      "NIFTY at 25,000: sell this week's 24,900 put at ₹60 and buy next week's 25,000 put at ₹150: you pay ₹90, or ₹5,850.",
      "NIFTY drifts down towards 24,900 by this week's expiry: the sold put expires near worthless and the bought put gains, so you gain.",
      "NIFTY rises hard: you can lose up to the ₹5,850 paid.",
    ],
    helps: "A slow fall towards the sold strike.",
    risk: "A sharp rise, or a fall in option prices.",
  },
  double_diagonal: {
    idea: "A put diagonal and a call diagonal together: you gain if NIFTY stays in a range while this week's options lose value.",
    how: "Sell a put and a call three strikes (150 points) out on this expiry, and buy a put and a call one strike (50 points) out on the next expiry. Closed before this week's expiry.",
    example: [
      "NIFTY at 25,000: sell this week's 24,850 put and 25,150 call at ₹40 each, buy next week's 24,950 put and 25,050 call at ₹130 each: you pay ₹180, or ₹11,700.",
      "NIFTY stays between 24,850 and 25,150 to this week's expiry: the sold options expire near worthless while the bought ones keep value, so you gain.",
      "A big move either way: you can lose about the ₹11,700 paid.",
    ],
    helps: "A range-bound market with steady or rising option prices.",
    risk: "A strong trend, or a fall in option prices across expiries.",
  },
  nifty_futures_intraday_long: {
    idea: "Buy one NIFTY future when the trend and option data point up, and close it the same day.",
    how: "One lot of the nearest NIFTY future. Every point NIFTY moves is ₹65 profit or loss.",
    example: [
      "Buy at 24,000. Stop at 23,940 (−60 points, −₹3,900). Target at 24,120 (+120 points, +₹7,800).",
      "Once up 50 points, a trailing stop follows 50 points behind the best price.",
      "Still open 20 minutes before the close: it closes at the market price.",
    ],
    helps: "A trending up day.",
    risk: "There's no fixed maximum loss. A sudden jump can go through the stop.",
  },
  nifty_futures_intraday_short: {
    idea: "Sell one NIFTY future when the trend and option data point down, and close it the same day.",
    how: "One lot of the nearest NIFTY future. Every point NIFTY moves is ₹65 profit or loss.",
    example: [
      "Sell at 24,000. Stop at 24,060 (−₹3,900). Target at 23,880 (+₹7,800).",
      "Once up 50 points, a trailing stop follows 50 points behind the best price.",
      "Still open 20 minutes before the close: it closes at the market price.",
    ],
    helps: "A trending down day.",
    risk: "There's no fixed maximum loss. A sudden jump can go through the stop.",
  },
  nifty_futures_positional_long: {
    idea: "Buy one NIFTY future on an up trend and hold it for days, with wider stop and target.",
    how: "One lot of the nearest NIFTY future, held overnight. Every point is ₹65.",
    example: [
      "Buy at 24,000. Stop at 23,880 (−120 points, −₹7,800). Target at 24,240 (+240 points, +₹15,600).",
      "Once up 120 points, a trailing stop follows 120 points behind the best price.",
      "Two days before the contract expires, it closes and waits to enter the next one.",
    ],
    helps: "A rise that lasts several days.",
    risk: "Overnight gaps can open well past the stop. There's no fixed maximum loss.",
  },
  nifty_futures_positional_short: {
    idea: "Sell one NIFTY future on a down trend and hold it for days, with wider stop and target.",
    how: "One lot of the nearest NIFTY future, held overnight. Every point is ₹65.",
    example: [
      "Sell at 24,000. Stop at 24,120 (−₹7,800). Target at 23,760 (+₹15,600).",
      "Once up 120 points, a trailing stop follows 120 points behind the best price.",
      "Two days before the contract expires, it closes and waits to enter the next one.",
    ],
    helps: "A fall that lasts several days.",
    risk: "Overnight gaps can open well past the stop. There's no fixed maximum loss.",
  },
};

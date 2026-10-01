# Formulas

This file explains every factor mastodon-utils computes and why it is built the way it is. Running with `--explain` prints a short version next to each number.

Most of these formulas have to cope with one fact about social engagement: it is heavy-tailed. One viral post can collect more interactions than fifty ordinary ones together. So the tool uses robust statistics (Hodges-Lehmann, Theil-Sen, Gini), shrinks small samples toward sensible baselines (Bayesian smoothing), and scores with geometric rather than arithmetic means.

## Post-level building blocks

### Weighted engagement

```
weighted = 3.0 × replies + 2.5 × quotes + 2.0 × boosts + 1.0 × favourites
```

The weights reflect effort and effect. A reply takes the most effort and starts a conversation. A quote adds commentary and also redistributes the post. A boost puts the post in front of a new audience. A favourite is a low-effort nod. You can change the weights with `--weights replies=3,quotes=2.5,boosts=2,favourites=1`.

### Young-post projection

A post from an hour ago hasn't finished collecting engagement. The tool models accumulated engagement as a saturation curve:

```
completion(age) = 1 − e^(−age / 10 hours)      (floored at 0.15)
projected       = weighted / completion(age)
```

A post has about 63% of its engagement after 10 hours, 91% after a day and 99% after two days. The floor means a post is never scaled up by more than about 6.7x. All "typical" engagement numbers use projected values, so posting yesterday doesn't count against you.

### Broadcast posts

Engagement is measured on **broadcast posts**: originals and self-thread continuations. Replies to other people are excluded because they are aimed at one person and naturally get little engagement; including them would punish conversational accounts. Replies are measured separately under the engager factor.

## Account factors

### Engagement factor

```
engagement_factor = 100 × HL(projected weighted) / (followers + 10)^0.7
```

- **HL** is the Hodges-Lehmann estimator: the median of all pairwise averages (xᵢ + xⱼ)/2. It resists outliers almost as well as the median (breakdown point 29%) while being about 95% as statistically efficient as the mean. Result: one viral post doesn't define "typical".
- **Followers^0.7** is allometric scaling. Engagement grows more slowly than audience size, because larger audiences are less attentive per person. Dividing by followers outright (the classic engagement rate) makes every big account look bad; dividing by nothing makes every big account look great. The 0.7 exponent keeps small and large accounts comparable. The +10 keeps brand-new accounts from dividing by zero.
- Rough reading: around 1 is ordinary, above 3 is strong, above 10 is exceptional.

The classic **engagement rate** (mean raw interactions ÷ followers, as a percentage) is reported alongside it for comparison with other tools.

### Consistency, Gini and top-10% share

- **Gini coefficient** of raw interactions across posts: 0 means every post does equally well, 1 means one post gets everything.
- **Consistency** = 1 − Gini.
- **Top-10% share**: the fraction of all engagement that came from the best tenth of posts. It answers "how hit-driven is this account?".

### Engagement h-index

This is the largest h such that h posts each got at least h interactions. It is borrowed from academic citation metrics. It rewards a body of consistently engaging posts; a single viral post can only ever add 1.

### Momentum

This is the Theil-Sen slope of log(1 + projected weighted engagement) against time, reported as percent change per 30 days:

```
momentum = (e^(slope × 30) − 1) × 100
```

Theil-Sen takes the median of the slopes between every pair of posts, so a single outlier can't tilt the trend line the way it would with least squares. The log scale makes the result a growth rate. It needs at least 8 broadcast posts.

### Interaction mix (Dirichlet-smoothed)

The shares of replies, boosts, favourites and quotes are smoothed toward a typical Mastodon mix (12% / 20% / 63% / 5%) with a prior strength of 20 pseudo-interactions:

```
share_k = (count_k + 20 × prior_k) / (total + 20)
```

Without smoothing, an account with 3 interactions, all boosts, would show "100% boosts". With it, large samples barely move and small samples stay sensible. Three ratios are derived from the smoothed shares:

- **Amplification ratio** = boosts ÷ favourites. Above 0.5 means people spread the posts rather than just liking them.
- **Discussion ratio** = (replies + quotes) ÷ (boosts + favourites). High means posts start conversations.
- **Ratio index** = replies ÷ favourites. Above 1 is the classic sign of being "ratioed": more people arguing than agreeing.

### Engager factor (how they talk to others)

```
reply_share     = (replies_to_others + 0.30 × 10) / (timeline_items + 10)      Beta-smoothed
partners_eff    = exp(Shannon entropy of reply recipients)                      Hill number of order 1
engager_factor  = reply_share × (1 − e^(−partners_eff / 5))
```

The **effective number of partners** is the key idea. Ten replies to ten different people give 10.0; ten replies all to one person give 1.0; a mix lands in between. The breadth term saturates, so 5 effective partners gives 0.63 and 15 gives 0.95. The factor runs from 0 to 1. It is high only for people who reply a lot *and* to many different people.

### Booster factor (how they amplify others)

This mirrors the engager factor, using boosts and the authors being boosted:

```
boost_share     = (boosts + 0.25 × 10) / (timeline_items + 10)
authors_eff     = exp(Shannon entropy of boosted authors)
booster_factor  = boost_share × (1 − e^(−authors_eff / 5))
```

High means an active curator of many voices. A high boost share with few effective authors means they amplify a small circle. Self-boosts are counted separately.

### Activity factor

```
rate_recent     = Σ e^(−λ × ageᵢ) / ((1 − e^(−λT)) / λ)        λ = ln 2 / 14 days, T = sample window
activity_factor = (1 − e^(−rate_recent / 2)) × √(active_day_ratio) × 0.5^(days_since_last / 7)
```

- **rate_recent** is an exponentially weighted Poisson rate estimate: decayed events divided by decayed exposure time. It counts posts from the last two weeks most heavily, and it is unbiased even when the sample window is short.
- **Saturation**: 2 posts per day gives 0.63. Posting 50 times a day isn't 25 times "more active" in any useful sense.
- **Active-day ratio** (share of days with at least one post) rewards showing up regularly over occasional binges. The square root softens it.
- **Freshness** halves every 7 days since the last post, so dormant accounts decay toward 0.

### Burstiness

This is the Goh-Barabási burstiness of the gaps between consecutive posts:

```
B = (σ − μ) / (σ + μ)
```

−1 is clockwork-regular (scheduled posting or a bot), 0 is random (Poisson), and values toward +1 mean the account posts in bursts.

### Posting time (circular statistics)

Times of day are angles on a 24-hour circle. The **circular mean** gives the typical posting time; a plain average of 23:00 and 01:00 would give noon, while the circular mean correctly gives midnight. The **concentration** R (the length of the mean resultant vector) runs from 0 (spread around the clock) to 1 (always at the same minute).

The **best time to post** is the 3-hour block whose broadcast posts have the highest Hodges-Lehmann projected engagement. Only blocks with at least 3 posts count.

### Content lift

For each feature (media, hashtags, links, content warnings, questions, posts over 280 characters, polls, quote posts):

```
lift = (HL(engagement of posts with it) + 1) / (HL(engagement of posts without it) + 1)
```

Above 1 means posts with the feature do better. The +1 smoothing stops near-zero baselines from producing absurd ratios. Lift is only shown when there are at least 3 posts on each side. It shows correlation, not proof: an account may attach images to its best material anyway.

### Alt text coverage

This is the share of media attachments that have a description. It is shown for accounts and for server timelines.

### Presence score (0 to 100)

```
presence = 100 × exp( Σ wₖ × ln(max(0.01, componentₖ)) / Σ wₖ )
```

This is a **weighted geometric mean** of five components, each between 0 and 1:

| Component | Formula | Weight |
|---|---|---|
| Reach | log10(followers + 1) / 6, capped at 1 (1 million followers = 1) | 0.20 |
| Resonance | 1 − e^(−engagement_factor / 3) | 0.30 |
| Activity | activity factor | 0.20 |
| Consistency | 1 − Gini | 0.15 |
| Conversation | 1 − (1 − engager_factor) × (1 − min(1, 3 × reply share received)) | 0.15 |

The mean is geometric, so a very weak dimension drags the whole score down; a huge follower count can't carry an account that never posts. Conversation is a probabilistic OR: it is high if the account replies to many people *or* draws many replies.

## Server factors

| Factor | Formula | Meaning |
|---|---|---|
| Monthly active ratio | active this month ÷ registered | Share of accounts in actual use |
| Stickiness | active this month ÷ active in six months | Near 1: people who come stay |
| Dormant share | 1 − active in six months ÷ registered | Abandoned sign-ups |
| Login momentum | Theil-Sen slope of log(weekly logins), % per week | Growing or shrinking, robust to one odd week |
| Posts per login | median of weekly posts ÷ logins | How much active users post |
| Newcomer share | registrations in last 4 complete weeks ÷ monthly actives | How much of the community is new |
| Login volatility | standard deviation ÷ mean of weekly logins | Low is a steady community |
| Voice diversity | effective number of authors in the local timeline sample ÷ sample size | 1: everyone different; low: a few accounts dominate |
| Peer domain diversity | effective number of top-level domains among peers | How varied the federation is |
| Hashtag acceleration | (uses today + 1) ÷ (average of previous days + 1) | Rising versus fading trends |

The current (partial) week in `/api/v1/instance/activity` is left out of trend calculations and reported separately, with a projection to a full week.

**Health score** (0 to 100) is a weighted geometric mean of engagement = 1 − e^(−monthly active ratio / 0.15) (weight 0.30), stickiness (0.25), momentum = logistic(login momentum / 5) (0.20) and federation = log10(peers + 1) / 5 (0.25). It is only reported when at least three of these are available, so non-Mastodon servers with thin public data don't get a misleading number.

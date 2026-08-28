# Day 25 Reliability Report

## 1. Architecture summary

`ReliabilityGateway.complete()` checks cache first. Cache hit returns text with zero provider latency and cost. Miss flows through ordered providers, each behind own circuit breaker. Primary failure or open circuit moves request to backup. If every provider fails, gateway returns static degraded message.

```
User request
    |
    v
[ReliabilityGateway]
    |
    v
[ResponseCache / SharedRedisCache] -- hit --> cached response
    |
    | miss
    v
[Primary circuit breaker] -- allowed --> primary provider
    |                                      |
    | open / provider error               | success
    v                                      v
[Backup circuit breaker] -- allowed --> backup provider --> cache response
    |
    | open / provider error
    v
[Static fallback: service temporarily degraded]
```

Circuit breaker states: `CLOSED` permits calls and counts failures. Threshold opens circuit. After reset timeout, circuit becomes `HALF_OPEN`; successful probe closes it, failed probe reopens it. Cache blocks privacy-sensitive queries and rejects semantic matches with conflicting four-digit numbers.

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| primary fail rate | 0.25 | Baseline provider fault injection. |
| primary base latency | 180 ms | Simulated primary latency. |
| primary cost | 0.01 per 1k tokens | Simulated primary cost model. |
| backup fail rate | 0.05 | Backup fault injection, lower than primary. |
| backup base latency | 260 ms | Simulated backup latency. |
| backup cost | 0.006 per 1k tokens | Simulated backup cost model. |
| failure threshold | 3 | Opens circuit after three failures while `CLOSED`. |
| reset timeout | 2 s | Wait before `OPEN` circuit admits half-open probe. |
| success threshold | 1 | One successful half-open probe closes circuit. |
| cache backend | memory | Recorded baseline uses in-memory cache; Redis shared-cache behavior has separate runtime evidence below. |
| cache TTL | 300 s | Entries older than 300 seconds expire. |
| similarity threshold | 0.92 | Semantic hit requires cosine score at least 0.92. |
| requests per scenario | 100 | Configured load-test size. Three scenarios produce 300 recorded requests. |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 0.9833 | No |
| Latency P95 | < 2500 ms | 317.09 ms | Yes |
| Fallback success rate | >= 95% | 0.9265 | No |
| Cache hit rate | >= 10% | 0.6067 | Yes |
| Recovery time | < 5000 ms | 2259.718179702759 ms | Yes |

## 4. Metrics

Source: `reports/metrics.json` baseline run.

| Metric | Value |
|---|---:|
| total requests | 300 |
| availability | 0.9833 |
| error rate | 0.0167 |
| latency P50 | 270.84 ms |
| latency P95 | 317.09 ms |
| latency P99 | 320.08 ms |
| fallback success rate | 0.9265 |
| cache hit rate | 0.6067 |
| estimated cost | 0.049904 |
| estimated cost saved | 0.182 |
| circuit open count | 8 |
| recovery time | 2259.718179702759 ms |

## 5. Cache comparison

Both executions record 300 requests. They are separate stochastic runs, so comparison shows measured outcomes rather than a controlled paired experiment. Delta is with-cache minus without-cache.

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---:|
| availability | 0.9767 | 0.9833 | +0.0066 |
| error rate | 0.0233 | 0.0167 | -0.0066 |
| latency P50 | 268.38 ms | 270.84 ms | +2.46 ms |
| latency P95 | 317.01 ms | 317.09 ms | +0.08 ms |
| latency P99 | 322.22 ms | 320.08 ms | -2.14 ms |
| fallback success rate | 0.9588 | 0.9265 | -0.0323 |
| cache hit rate | 0 | 0.6067 | +0.6067 |
| circuit open count | 17 | 8 | -9 |
| recovery time | 2269.951343536377 ms | 2259.718179702759 ms | -10.233163833618 ms |
| estimated cost | 0.134082 | 0.049904 | -0.084178 |
| estimated cost saved | 0 | 0.182 | +0.182 |

With-cache run records lower error rate, fewer circuit openings, lower estimated cost, and 0.6067 cache hit rate. Other deltas may vary between executions because runs are stochastic and separate.

## 6. Redis shared cache

In-memory cache lives inside one gateway process. Multi-instance deployment would give each instance separate entries, lower effective hit rate, and duplicate provider calls. `SharedRedisCache` stores `query` and `response` in Redis hash key `rl:cache:<query-hash>`, sets Redis TTL, then checks exact hash lookup before semantic scan. Separate gateway instances using same Redis URL and prefix can read same keyspace.

Privacy guard applies before Redis read and write. Semantic Redis lookup uses same cosine similarity and four-digit mismatch rejection as in-memory cache.

### Evidence status

`make test` ran six Redis tests successfully. Redis returned `PONG`. Two cache instances returned `('shared response', 1.0)`, proving shared exact-key state. Observed Redis key: `rl:evidence:d6665114c7ed`.

### In-memory vs Redis latency comparison

| Metric | In-memory cache | Redis cache | Notes |
|---|---:|---:|---|
| latency P50 | Not separately recorded | Not measured | Baseline aggregate reports provider latency percentiles only. |
| latency P95 | Not separately recorded | Not separately recorded | Aggregate chaos metrics report end-to-end provider latency percentiles, not cache lookup latency. |

## 7. Chaos scenarios

| Scenario | Expected behavior | Observed behavior | Pass/Fail |
|---|---|---|---|
| primary_timeout_100 | Primary fails 100%; requests use fallback path when available. | `metrics.json` records scenario status `pass`. | Pass |
| primary_flaky_50 | Primary fails 50%; breaker may open and traffic mixes with fallback. | `metrics.json` records scenario status `pass`. | Pass |
| all_healthy | Baseline with no provider overrides. | `metrics.json` records scenario status `pass`. | Pass |

All three configured scenarios pass. Aggregated run records eight circuit-open transitions and 2259.718179702759 ms recovery time.

## 8. Failure analysis

Remaining weakness: circuit breaker state is process-local. In multi-instance service, one instance can open its breaker while another keeps calling failing provider. That splits failure knowledge, increases bad-provider traffic, and weakens outage protection.

Fix before production: persist breaker counters, state, open timestamp, and transition coordination in shared Redis or dedicated coordination store. Add atomic updates with expiry matching reset timeout. Keep local fallback behavior if shared store unavailable. Add per-user rate limits and response-quality SLOs so availability does not hide low-quality fallback answers.

Redis shared-cache exact-key behavior has runtime evidence. Cache lookup latency remains unrecorded because aggregate chaos metrics measure gateway latency, not isolated cache lookup latency.

## 9. Next steps

1. Move circuit-breaker state to shared Redis with atomic counters, expiry, and cross-instance transition tests.
2. Add isolated in-memory and Redis cache lookup latency measurements.
3. Add per-user rate limiting, cache false-hit monitoring, and quality SLOs for fallback and static-fallback responses.

# League of Legends Kayle Performance Analysis

This is an in-progress data analytics project comparing a developing Kayle TOP player with higher-ranked Kayle players. The goal is to identify measurable performance gaps, trace those gaps to likely gameplay habits, and eventually build models that support focused VOD review and practice.

## Project status

The Riot API collection and normalization pipeline is working locally. The current PostgreSQL snapshot contains:

- 26 SELF Kayle TOP matches;
- 110 reference-player Kayle TOP matches;
- 22 Riot-validated reference players across Diamond, Master, and Grandmaster;
- all 10 participants for each match;
- minute-level timeline frames and Riot timeline events;
- raw Match-V5 and timeline payloads retained for future questions.

I am currently working through the SQL exploration stage. SQL schema, view, and analysis files are intentionally not published yet; I will add them as I complete and understand that work myself.

## Research question

How does my Kayle performance differ from stronger Kayle players, particularly during the early lane, and which repeatable behaviors appear to drive those differences?

Planned measures include:

- gold, XP, and CS differences by minute;
- health-trading patterns before level 6;
- level and item timing;
- deaths, solo kills, ganks, and objective participation;
- wave position and movement inferred from timeline coordinates;
- matchup- and rank-adjusted comparisons;
- links between early-game state and eventual match outcome.

## Data flow

1. A fixed OP.GG Kayle leaderboard snapshot supplies candidate Riot IDs only.
2. Riot Account-V1 resolves each Riot ID to a PUUID.
3. Riot League-V4 validates current Ranked Solo rank.
4. Riot Champion Mastery validates Kayle experience.
5. Riot Match-V5 validates actual Ranked Solo Kayle TOP games and supplies match data.
6. Riot timeline payloads supply participant frames and discrete events.
7. Python normalizes the API payloads into a local PostgreSQL relational database.
8. SQL exploration, KPI design, visualization, and predictive modeling will be added in later stages.

OP.GG is never used as an analysis source. Riot is the source of truth for rank, mastery, role, patch, match history, and gameplay data.

## Published project structure

```text
.
├── .env.example
├── .gitignore
├── requirements.txt
└── python/
    ├── __init__.py
    ├── collector.py
    ├── config.py
    ├── database.py
    ├── discovery.py
    ├── kayle_pipeline.py
    ├── normalizer.py
    ├── patches.py
    ├── public_sources.py
    └── riot_api.py
```

The public repository currently represents the Python collection layer. The SQL layer and model-development layer are in progress.

## Technical design

- Restart-safe collection with committed progress and resume support
- Stable primary keys and upserts to prevent duplicate match data
- One normalized match shared across multiple cohort associations
- Atomic match writes so interrupted collection does not leave partial matches
- Riot rate-limit handling and bounded retries
- Cheap candidate-validation steps before expensive match inspection
- Raw JSON retention alongside normalized analytical fields
- Explicit SELF and REFERENCE cohort labels
- Quality flags for remakes, missing timelines, ambiguous roles, and unexpected participant counts

## Local configuration

Create a `.env` from `.env.example` and supply a current Riot development key and local PostgreSQL connection string. The real `.env` is ignored by Git and is never committed.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
Copy-Item .env.example .env
```

The public snapshot is not yet intended as an end-to-end runnable release because the SQL schema layer is still being developed and learned. Reproducible setup instructions will be completed when that layer is published.

## Learning roadmap

- [x] Build a Riot API client with retry and rate-limit handling
- [x] Discover Kayle-specific reference candidates
- [x] Validate candidates with official Riot data
- [x] Normalize matches, participants, timeline frames, and events
- [x] Load the normalized data into PostgreSQL locally
- [ ] Complete beginner SQL exploration exercises
- [ ] Write and document original analysis queries
- [ ] Define early-lane Kayle performance KPIs
- [ ] Compare SELF and REFERENCE cohorts
- [ ] Build visual diagnostics for VOD review
- [ ] Engineer modeling features
- [ ] Train and evaluate predictive models
- [ ] Document findings, limitations, and gameplay recommendations

## Security and privacy

This repository excludes:

- Riot API keys;
- PostgreSQL passwords;
- the real `.env` file;
- virtual environments and Python caches;
- database dumps and raw local data;
- personal SELF account identifiers.

Only reproducible source code, generic configuration examples, and project documentation are published.

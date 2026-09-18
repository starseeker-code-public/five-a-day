"""END-TO-END journeys — real services, real database, real third parties.

Not pytest tests, and deliberately not named `test_*.py`: `pytest.ini` sets
`python_files = test_*.py`, so `make test` and CI walk straight past this
directory. That is the point. A journey here talks to Google Drive with a live
credential and writes to the DEV database, neither of which a CI runner has.

Run them with `make e2e` (all of them) or `make e2e ARGS="--only payment"`.
See `run.py` for the runner and `_harness.py` for what a journey is.
"""

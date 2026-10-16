# Qlib research pattern reference

Reference: Microsoft Qlib v0.9.7, qlib/workflow/task/gen.py (RollingGen), and qlib/workflow/online/manager.py. MIT license retained alongside this note. Our rolling_trials.py is an original small implementation of chronological rolling-window planning; no upstream runtime dependency or copied strategy/model code is installed.

REA inspected an inert published PyPI pyqlib 0.9.7 wheel:
- File: pyqlib-0.9.7-cp310-cp310-macosx_10_9_universal2.whl (960034 bytes). This platform-specific wheel was selected only as an archive, never installed or executed on Windows.
- SHA-256: 62c2e042094aa340b8833111e14b4b6919725d6b5c049adbead963d0c4f309d9, matched PyPI JSON metadata before opening.
- REA evidence: ev_1746cf3fe66fde0be457fc1fffc7101ca27a9b1093ac9473d5c45ee5230f1b86; inventory ev_5a4d4f571e5ae0e71d393dabecd961724be3b11e02997fa9f024eaf8df06509f.
- Inventory: 232 content nodes, 242 occurrences, 241 edges; no reported integrity contradictions. ZIP central-directory inventory only; not semantic validation, signature authentication or execution/performance evidence.
- Packaged gen.py SHA-256: 62cc7bea53314f9e876a865e657b206d78782f95a82ce526e9c82ecc1efc2ee2.
- Packaged manager.py SHA-256: eef102410434d9d604b99c6e5b11f45d2a4ea78145940174449fab6d64c5c332.
- These Python members were separately read with the standard-library ZIP reader, not executed. RollingGen supplies sliding/expanding chronological task segments and truncation gaps; OnlineManager separates simulated/online routines and retains serving history. Only the rolling planning concept is implemented in this increment.
- Wheel had no LICENSE-named member. License fetched independently from https://raw.githubusercontent.com/microsoft/qlib/v0.9.7/LICENSE; SHA-256 c2cfccb812fe482101a8f04597dfc5a9991a6b2748266c47ac91b6a5aae15383.

Our differences: final holdout endpoints remain fixed and never evaluated by the planner; counts are bounded by the manifest's remaining declared budget; invalid/duplicate session dates and inadequate purge gaps fail rather than silently truncate. Supplied session calendars and data remain unverified. Overlapping folds are dependent and must not be counted as independent observations. The evaluator must also exclude trades/labels crossing boundaries.

No claim of upstream returns transferring to NIFTY, automatic daily replacement, calibrated win probability or completed evaluator integration. This offline planning helper does not enforce a durable account-wide search budget, schedule jobs or write the registry.

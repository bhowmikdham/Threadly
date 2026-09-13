# Golden tests

Deterministic input -> expected output pairs for the NON-model paths:
extractor tier-1 regex, planner rules, PII masking, summary cache keys.

W4 gate: the freeze requires goldens passing for every deterministic module.
Add fixtures as `<module>/<case>.json` with `{"input": ..., "expected": ...}`.

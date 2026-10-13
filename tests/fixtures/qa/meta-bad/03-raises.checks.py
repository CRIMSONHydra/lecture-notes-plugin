# A checks file that raises: counts as a failure labelled <exception>.
ok(True, "before-the-crash")
raise RuntimeError("boom")

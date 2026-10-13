# Deliberately wrong claims: verify_numbers must fail and name every label below.
section("a mix of right and wrong")
ok(12 + 13 == 25, "add-result-fine")
ok(12 + 13 == 26, "add-result-WRONG", "note claims 26")
close(1.0 / 3.0, 0.34, tol=1e-3, label="third-WRONG")
close(80.0, 60.0, rel=0.05, label="ram-latency-WRONG")

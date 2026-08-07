import math
import random
import unittest

from swap_distribution import analyze_sizes, analyze_sizes_by_chain, fit_lognormal, fit_pareto_tail


class TestFitLognormal(unittest.TestCase):
    def test_recovers_known_lognormal(self):
        rng = random.Random(42)
        true_s, true_scale = 0.6, 150.0
        x = [math.exp(rng.gauss(math.log(true_scale), true_s)) for _ in range(20000)]
        s, scale = fit_lognormal(x)
        self.assertAlmostEqual(s, true_s, delta=0.03)
        self.assertAlmostEqual(scale, true_scale, delta=15.0)

    def test_empty_returns_zeros(self):
        self.assertEqual(fit_lognormal([]), (0.0, 0.0))


class TestFitParetoTail(unittest.TestCase):
    def test_hill_estimator_on_pure_pareto(self):
        rng = random.Random(7)
        alpha_true, xmin_true = 1.2, 100.0
        x = [xmin_true * rng.random() ** (-1.0 / alpha_true) for _ in range(20000)]
        alpha, xmin = fit_pareto_tail(x, 0.90)
        self.assertAlmostEqual(alpha, alpha_true, delta=0.1)
        # The q-th percentile threshold of a Pareto is xmin/(1-q)^(1/alpha).
        expected_xmin = xmin_true * (1 - 0.90) ** (-1.0 / alpha_true)
        self.assertAlmostEqual(xmin, expected_xmin, delta=expected_xmin * 0.05)


class TestAnalyzeSizes(unittest.TestCase):
    def test_too_few_returns_none(self):
        self.assertIsNone(analyze_sizes([]))
        self.assertIsNone(analyze_sizes([1.0, 2.0]))

    def test_drops_nonpositive(self):
        sizes = [0.0, -5.0, 10.0, 20.0, 30.0, 40.0]
        result = analyze_sizes(sizes)
        self.assertIsNotNone(result)
        self.assertEqual(result["n"], 4)

    def test_output_shape(self):
        rng = random.Random(1)
        x = [math.exp(rng.gauss(math.log(100), 0.8)) for _ in range(5000)]
        result = analyze_sizes(x, nbins=40, curve_points=200)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["histogram"]["edges"]), 41)
        self.assertEqual(len(result["histogram"]["mids"]), 40)
        self.assertEqual(len(result["histogram"]["dens_log"]), 40)
        self.assertEqual(len(result["histogram"]["counts"]), 40)
        self.assertEqual(len(result["histogram"]["sums"]), 40)
        # counts sum to n, sums sum to total value
        self.assertEqual(sum(result["histogram"]["counts"]), result["n"])
        self.assertAlmostEqual(sum(result["histogram"]["sums"]), sum(x), delta=1e-6)
        self.assertEqual(len(result["curves"]["lsizes"]), 200)
        self.assertEqual(len(result["curves"]["ln"]), 200)
        self.assertEqual(len(result["curves"]["composite"]), 200)
        self.assertGreater(result["median"], 0)
        self.assertGreater(result["p99"], result["p90"])
        self.assertGreater(result["lognormal"]["s"], 0)
        self.assertGreater(result["lognormal"]["scale"], 0)

    def test_composite_matches_lognormal_below_xmin(self):
        rng = random.Random(2)
        x = [math.exp(rng.gauss(math.log(100), 0.8)) for _ in range(8000)]
        result = analyze_sizes(x)
        xmin = result["pareto"]["xmin"]
        curves = result["curves"]
        for i, v in enumerate(10.0 ** s for s in curves["lsizes"]):
            if v <= xmin * 0.95:
                self.assertAlmostEqual(curves["ln"][i], curves["composite"][i],
                                       delta=max(curves["ln"][i] * 1e-9, 1e-12))

    def test_linear_histogram(self):
        rng = random.Random(6)
        x = [math.exp(rng.gauss(math.log(100), 0.8)) for _ in range(5000)]
        result = analyze_sizes(x)
        lin = result["histogram"]["linear"]
        self.assertTrue(lin["edges"][0] == 0.0)
        self.assertTrue(all(lin["edges"][i + 1] > lin["edges"][i] for i in range(len(lin["edges"]) - 1)))
        # constant-width bins
        w = lin["edges"][1] - lin["edges"][0]
        for i in range(len(lin["edges"]) - 2):
            self.assertAlmostEqual(lin["edges"][i + 2] - lin["edges"][i + 1], w, delta=1e-6)
        # counts/sums cover all values
        self.assertEqual(sum(lin["counts"]), result["n"])
        self.assertAlmostEqual(sum(lin["sums"]), sum(x), delta=1e-6)
        self.assertEqual(len(lin["counts"]), len(lin["edges"]) - 1)

    def test_densities_integrate_approx(self):
        rng = random.Random(3)
        x = [math.exp(rng.gauss(math.log(100), 0.9)) for _ in range(50000)]
        result = analyze_sizes(x)
        # sum of density-per-log10 * dlog10 over bins ≈ 1
        hist = result["histogram"]
        lo, hi = math.log10(hist["edges"][0]), math.log10(hist["edges"][-1])
        dlog = (hi - lo) / (len(hist["dens_log"]))
        total = sum(hist["dens_log"]) * dlog
        self.assertAlmostEqual(total, 1.0, delta=0.05)


class TestAnalyzeSizesByChain(unittest.TestCase):
    def _make_chain(self, rng, center, sigma, n):
        return [math.exp(rng.gauss(math.log(center), sigma)) for _ in range(n)]

    def test_chain_histograms_stack_to_overall(self):
        rng = random.Random(4)
        groups = {
            "Ethereum": self._make_chain(rng, 100, 0.8, 5000),
            "Arbitrum": self._make_chain(rng, 120, 0.9, 3000),
            "Base": self._make_chain(rng, 80, 0.7, 2000),
        }
        result = analyze_sizes_by_chain(groups, nbins=50, curve_points=200)
        self.assertIsNotNone(result)
        chains = result["chains"]
        self.assertEqual(len(chains), 3)
        # Sorted by descending n.
        counts = [c["n"] for c in chains]
        self.assertEqual(counts, sorted(counts, reverse=True))
        # Per-chain dens sum to the overall histogram density bin-by-bin.
        overall = result["histogram"]["dens_log"]
        for i in range(len(overall)):
            stacked = sum(c["dens_log"][i] for c in chains)
            self.assertAlmostEqual(stacked, overall[i], delta=max(overall[i] * 1e-9, 1e-12))
        # Per-chain counts/sums stack to the overall as well.
        oc = result["histogram"]["counts"]
        os = result["histogram"]["sums"]
        for i in range(len(oc)):
            self.assertEqual(sum(c["counts"][i] for c in chains), oc[i])
            self.assertAlmostEqual(sum(c["sums"][i] for c in chains), os[i], delta=max(os[i] * 1e-9, 1e-6))
        # Names preserved.
        self.assertEqual({c["name"] for c in chains}, {"Ethereum", "Arbitrum", "Base"})
        # Linear per-chain counts/sums exist and stack to the overall linear histogram.
        lin = result["histogram"]["linear"]
        nb = len(lin["counts"])
        for c in chains:
            self.assertEqual(len(c["linear_counts"]), nb)
            self.assertEqual(len(c["linear_sums"]), nb)
        for i in range(nb):
            self.assertEqual(sum(c["linear_counts"][i] for c in chains), lin["counts"][i])
            self.assertAlmostEqual(sum(c["linear_sums"][i] for c in chains),
                                   lin["sums"][i], delta=max(lin["sums"][i] * 1e-9, 1e-6))
        self.assertEqual(sum(lin["counts"]), result["n"])

    def test_empty_chain_dropped(self):
        groups = {"Ethereum": [10, 20, 30], "Base": []}
        result = analyze_sizes_by_chain(groups)
        self.assertIsNotNone(result)
        self.assertEqual([c["name"] for c in result["chains"]], ["Ethereum"])

    def test_too_few_overall_returns_none(self):
        self.assertIsNone(analyze_sizes_by_chain({"Ethereum": [1.0, 2.0]}))

    def test_edges_shared_across_chains(self):
        rng = random.Random(5)
        groups = {
            "Ethereum": self._make_chain(rng, 100, 0.8, 4000),
            "Solana": self._make_chain(rng, 5000, 1.5, 1500),
        }
        result = analyze_sizes_by_chain(groups, nbins=40)
        self.assertIsNotNone(result)
        edges = result["histogram"]["edges"]
        for c in result["chains"]:
            self.assertEqual(len(c["dens_log"]), len(edges) - 1)


if __name__ == "__main__":
    unittest.main()

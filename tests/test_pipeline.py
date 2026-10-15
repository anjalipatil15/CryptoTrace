"""End-to-end checks against a hand-built test fixture whose correct answers are known.

The fixture is test-only; real results always come from Etherscan data.
"""

from types import SimpleNamespace

import pytest

from cryptotrace.collection.tracer import trace
from cryptotrace.config import load_settings
from synthetic_case import addr, build_synthetic_case
from cryptotrace.pipeline import analyze
from cryptotrace.processing.preprocess import normalize


@pytest.fixture(scope="module")
def result():
    fixture = build_synthetic_case()
    settings = load_settings()
    case = SimpleNamespace(case_id="fixture", name="fixture", seeds=fixture.seeds, start_time=None,
                           ground_truth=None, settings=settings, description="")
    traced = trace(fixture.seeds, fixture.fetch, fixture.labels, settings)
    case.extra = {"prices_usd": {"ETH": 1125, "USDC": 1}}
    res = analyze(traced.transfers, traced.nodes, case, fixture.labels, fixture.ground_truth)
    res.traced = traced
    return res


def conf(result, name):
    row = result.scored.set_index("address").loc[addr(name)]
    return row["confidence"], row["band"]


def test_normalize_skips_failed_and_scales_decimals():
    rows = [
        {"hash": "0x1", "from": "0xA", "to": "0xB", "value": str(10**18), "timeStamp": "0", "blockNumber": "1", "isError": "0"},
        {"hash": "0x2", "from": "0xA", "to": "0xB", "value": str(10**18), "timeStamp": "0", "blockNumber": "1", "isError": "1"},
    ]
    df = normalize(rows, "normal")
    assert len(df) == 1 and df.loc[0, "value"] == 1.0 and df.loc[0, "from_addr"] == "0xa"


def test_seed_is_certain_and_direct_hop_is_very_high(result):
    assert conf(result, "Theft") == (1.0, "very_high")
    c, band = conf(result, "A")
    assert c == pytest.approx(0.92) and band == "very_high"


def test_confidence_never_increases_along_best_path(result):
    s = result.scored.set_index("address")["confidence"]
    for path in result.scored["path"]:
        values = [s[a] for a in path]
        assert all(a >= b for a, b in zip(values, values[1:]))


def test_service_endpoints_are_low_and_not_expanded(result):
    assert conf(result, "Tornado")[1] == "very_low"
    assert conf(result, "Exchange 1")[1] in ("low", "very_low")
    status = result.traced.nodes.set_index("address")["status"]
    assert status[addr("Tornado")] == "terminal"


def test_pre_theft_outflow_is_not_on_trail(result):
    assert addr("Old Friend") not in set(result.scored["address"])


def test_fan_in_with_clean_funds_is_penalised(result):
    edges = result.edges
    into_f = edges[edges["to_addr"] == addr("F")]
    assert into_f["fan_in"].all()
    # direct hop (0.92) scaled by fan-in adjustment 0.6 + 0.4 * taint(0.5)
    assert into_f["factor"].to_numpy() == pytest.approx(0.92 * 0.8)
    taint_f = result.scored.set_index("address").loc[addr("F"), "taint_ratio"]
    assert taint_f == pytest.approx(200 / 400)


def test_swap_preserves_taint(result):
    usdc = result.edges[(result.edges["from_addr"] == addr("Uniswap")) & (result.edges["token"] == "USDC")]
    assert len(usdc) == 1 and usdc["tainted_value"].iloc[0] > 0


def test_all_patterns_detected(result):
    found = set(result.patterns["pattern"])
    assert {"fan_out", "fan_in", "peeling_chain", "rapid_movement", "circular"} <= found


def test_validation_metrics(result):
    v = result.validation
    assert v["wallet_identification"]["recall"] == 1.0
    assert v["trace_completeness"]["completeness"] == 1.0
    assert v["confidence_stability"]["rank_correlation_mean"] > 0.9


def test_insights_tell_the_story(result):
    ins = result.insights
    keys = [s["key"] for s in ins["stages"]]
    assert keys[0] == "theft" and {"conversion", "distribution", "mixer", "exchange", "bridge"} <= set(keys)
    roles = {w["address"]: w["role"] for w in ins["wallets"]}
    assert roles[addr("Theft")] == "Attacker wallet"
    assert roles[addr("P4")] == "Mixer depositor"
    assert ins["kpis"]["mixer_deposits"] == 1
    # every Sankey link is forward-only (a Sankey cannot contain cycles)
    import networkx as nx
    sk = nx.DiGraph((l["source"], l["target"]) for l in ins["sankey"]["links"])
    assert nx.is_directed_acyclic_graph(sk)


def test_dashboard_renders(result, tmp_path):
    from cryptotrace.dashboard.build import render_dashboard

    html = render_dashboard(result.insights, tmp_path / "d.html").read_text(encoding="utf-8")
    assert "/*__CASE_DATA__*/" not in html and '"stages"' in html

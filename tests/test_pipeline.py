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


def test_cli_loads_and_lists_commands(capsys):
    from cryptotrace.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert {"trace", "analyze", "calibrate"} <= set(capsys.readouterr().out.replace(",", " ").replace("{", " ").replace("}", " ").split())


def test_etherscan_reads_history_in_pages(tmp_path):
    """Etherscan caps each response at 1,000 rows; a longer history must be read page by page."""
    from cryptotrace.collection.etherscan import EtherscanClient
    from cryptotrace.storage.db import Database

    history = [{"hash": f"0x{i}"} for i in range(2500)]
    pages = []

    def fake_get(params):
        pages.append(params["page"])
        start = (params["page"] - 1) * params["offset"]
        return history[start:start + params["offset"]]

    settings = load_settings()
    client = EtherscanClient("key", settings, Database(tmp_path / "t.db"))
    client._get = fake_get
    assert len(client.transfers("0xabc", "normal")) == 2500 and pages == [1, 2, 3]
    assert len(client.transfers("0xabc", "normal")) == 2500 and pages == [1, 2, 3]  # second read is cached


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


def test_recovery_return_ends_the_trail(result):
    into = result.edges[result.edges["to_addr"] == addr("Recovery")]
    assert len(into) == 1 and into["hop_type"].iloc[0] == "recovery_return"
    assert result.traced.nodes.set_index("address")["status"][addr("Recovery")] == "terminal"
    roles = {w["address"]: w["role"] for w in result.insights["wallets"]}
    assert roles[addr("E5")] == "Returner"
    assert "recovery" in {s["key"] for s in result.insights["stages"]}


def test_post_mixer_ground_truth_is_out_of_scope():
    import pandas as pd
    from cryptotrace.validation.metrics import wallet_identification

    scored = pd.DataFrame({"address": ["0xa", "0xb"]})
    gt = pd.DataFrame({"address": ["0xa", "0xb", "0xc"], "role": ["exploiter", "tornado_depositor", "post_mixer_withdrawal"]})
    wi = wallet_identification(scored, gt)
    assert wi["recall"] == pytest.approx(2 / 3) and wi["in_scope_recall"] == 1.0
    assert wi["found_by_role"]["post_mixer_withdrawal"] == "0/1"


def test_band_nudge_only_moves_crowded_cutoffs():
    import numpy as np
    from cryptotrace.validation.calibration import nudge_bands

    bands = {"very_high": 0.9, "high": 0.75, "medium": 0.55, "lower": 0.35, "low": 0.15, "very_low": 0.0}
    out = nudge_bands(np.array([0.551, 0.552, 0.8]), bands)
    assert out["medium"] != 0.55 and abs(out["medium"] - 0.55) <= 0.05 + 1e-9   # scores sat on the cut-off
    assert out["lower"] == 0.35 and out["low"] == 0.15                   # nothing nearby: unchanged


def test_freeze_rewrites_only_scoring_values(tmp_path):
    import shutil

    import yaml
    from cryptotrace.config import DEFAULT_SETTINGS
    from cryptotrace.validation.calibration import freeze

    path = tmp_path / "settings.yaml"
    shutil.copy(DEFAULT_SETTINGS, path)
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    chosen = {"scoring": {"hop_factors": {**before["scoring"]["hop_factors"], "direct": 0.95},
                          "bands": {**before["scoring"]["bands"], "medium": 0.52}}}
    changed = freeze(path, chosen)
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["scoring"]["hop_factors"]["direct"] == 0.95 and after["scoring"]["bands"]["medium"] == 0.52
    assert len(changed) == 2 and "# Tornado Cash etc." in path.read_text(encoding="utf-8")
    after["scoring"] = before["scoring"] = None
    assert after == before  # nothing outside the scoring block moved


def test_calibration_picks_a_candidate(result):
    from cryptotrace.validation.calibration import calibrate

    fixture = build_synthetic_case()
    table, best = calibrate(result.graph, fixture.seeds, load_settings(), None, fixture.ground_truth, runs=3)
    assert table["chosen"].sum() == 1 and "current" in set(table["candidate"])
    f = best["scoring"]["hop_factors"]
    assert f["direct"] > f["split"] > f["fan_out"]


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


def test_dashboard_script_is_valid_javascript(result, tmp_path):
    """A JS syntax error leaves the page blank, and only a JS parser catches it."""
    import re
    import shutil
    import subprocess

    from cryptotrace.dashboard.build import PRESENTATION, TEMPLATE, render_dashboard

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    html = "".join(render_dashboard(result.insights, tmp_path / f"{t.stem}.html", t).read_text(encoding="utf-8")
                   for t in (TEMPLATE, PRESENTATION))
    scripts = [body for attrs, body in re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", html, re.S)
               if "case-data" not in attrs]
    js = tmp_path / "page.js"
    js.write_text("\n".join(scripts), encoding="utf-8")
    proc = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

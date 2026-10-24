"""Command line interface.

    cryptotrace trace   config/cases/harmony.yaml     # Phase 1-2: collect into SQLite
    cryptotrace analyze config/cases/harmony.yaml     # Phase 3-7: score, detect, report
    cryptotrace calibrate config/cases/nomad.yaml     # tune factors for stability (calibration case only)
"""

from __future__ import annotations

import argparse
from .config import PROJECT_ROOT, etherscan_api_key, load_case, resolve
from .labels import AUTO_SOURCE, auto_labels_path, labels_for_case


def _print_files(files) -> None:
    for name, path in files.items():
        print(f"  {name:9s} {path}")


def cmd_trace(args) -> None:
    from .collection.etherscan import EtherscanClient
    from .collection.tracer import contract_classifier, etherscan_fetcher, trace
    from .labels import LabelStore
    from .storage.db import Database

    case = load_case(args.case)
    if not case.seeds:
        raise SystemExit(f"No seeds in {args.case}; add verified attacker addresses first.")
    db = Database(resolve(case.settings["storage"]["db_path"]))
    client = EtherscanClient(etherscan_api_key(), case.settings, db)
    labels = LabelStore.from_csv()
    db.clear_case(case.case_id)

    def on_node(address, hop, status):
        db.save_node(case.case_id, address, hop, status)
        print(f"  hop {hop}  {status:9s} {address}")

    result = trace(case.seeds, etherscan_fetcher(client, case.settings), labels, case.settings,
                   case.start_time, on_node=on_node, classify=contract_classifier(client, case.settings))
    db.save_transfers(case.case_id, result.transfers)
    n = labels.to_csv(auto_labels_path(case.case_id), source=AUTO_SOURCE)
    print(f"Auto-labeled {n} contracts -> {auto_labels_path(case.case_id)}")
    print(f"Stored {len(result.transfers)} transfers across {len(result.nodes)} addresses.")
    for line in result.log:
        print("  note:", line)


def cmd_analyze(args) -> None:
    from .pipeline import analyze, write_outputs
    from .storage.db import Database

    case = load_case(args.case)
    db = Database(resolve(case.settings["storage"]["db_path"]))
    transfers, nodes = db.load_transfers(case.case_id), db.load_nodes(case.case_id)
    if transfers.empty:
        raise SystemExit(f"No transfers stored for '{case.case_id}'. Run `cryptotrace trace {args.case}` first.")
    result = analyze(transfers, nodes, case, labels_for_case(case.case_id), run_sensitivity=not args.no_sensitivity,
                     contract_names=_theft_source_names(case, transfers, nodes, db))
    print(f"{len(result.scored)} wallets scored, {len(result.patterns)} patterns detected.")
    _print_files(write_outputs(result, case, PROJECT_ROOT / "outputs" / case.case_id))


def cmd_calibrate(args) -> None:
    import yaml

    from .graph.builder import build_graph
    from .storage.db import Database
    from .validation.calibration import calibrate
    from .validation.metrics import load_ground_truth

    case = load_case(args.case)
    db = Database(resolve(case.settings["storage"]["db_path"]))
    transfers, nodes = db.load_transfers(case.case_id), db.load_nodes(case.case_id)
    if transfers.empty:
        raise SystemExit(f"No transfers stored for '{case.case_id}'. Run `cryptotrace trace {args.case}` first.")
    g = build_graph(transfers, labels_for_case(case.case_id), nodes)
    table, best = calibrate(g, case.seeds, case.settings, case.start_time, load_ground_truth(case.ground_truth),
                            runs=args.runs)
    out = PROJECT_ROOT / "outputs" / case.case_id
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "calibration.csv", index=False)
    cols = ["candidate", "direct", "split", "fan_out", "swap", "band_change_rate", "rank_correlation", "auc_non_seed"]
    print(table.sort_values("band_change_rate")[cols].head(8).round(4).to_string(index=False))
    print(f"\nAll {len(table)} candidates -> {out / 'calibration.csv'}")
    print("Chosen (copy into config/settings.yaml to freeze):")
    print(yaml.safe_dump({"scoring": {"hop_factors": best["scoring"]["hop_factors"], "bands": best["scoring"]["bands"]}},
                         sort_keys=False))


def _theft_source_names(case, transfers, nodes, db) -> dict:
    """Verified contract names of the addresses that paid the attacker (e.g. the exploited bridge)."""
    from .collection.etherscan import EtherscanClient

    traced = set(nodes["address"]) if not nodes.empty else set()
    sources = set(transfers.loc[transfers["to_addr"].isin(case.seeds) & ~transfers["from_addr"].isin(traced), "from_addr"])
    try:
        client = EtherscanClient(etherscan_api_key(), case.settings, db)
    except RuntimeError:
        return {}
    names = {}
    for a in sorted(sources):
        try:
            if name := client.contract_name(a):
                names[a] = name
        except Exception:  # names are cosmetic; never fail the analysis over them
            pass
    return names


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="cryptotrace", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("trace", help="collect transfers for a case via Etherscan")
    p.add_argument("case")
    p.set_defaults(func=cmd_trace)
    p = sub.add_parser("analyze", help="score, detect patterns, validate and report")
    p.add_argument("case")
    p.add_argument("--no-sensitivity", action="store_true", help="skip the perturbation analysis")
    p.set_defaults(func=cmd_analyze)
    p = sub.add_parser("calibrate", help="tune hop factors for stability on a calibration case (Nomad)")
    p.add_argument("case")
    p.add_argument("--runs", type=int, default=100, help="noise runs per candidate (default 100)")
    p.set_defaults(func=cmd_calibrate)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

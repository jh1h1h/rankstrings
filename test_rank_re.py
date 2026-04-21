#!/usr/bin/env python3
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import rank_re


@dataclass
class MatchResult:
    query: str
    kind: str
    found: bool
    rank: int | None
    score: int | None
    detail: str | None
    max_rank: int | None
    enforce: bool
    passed: bool


def load_fixture(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_string_match(ranked: list[rank_re.RankedString], query: str) -> tuple[int, rank_re.RankedString] | None:
    lower_query = query.lower()
    for index, item in enumerate(ranked, start=1):
        if lower_query in item.text.lower():
            return index, item
    return None


def find_symbol_match(ranked: list[rank_re.RankedSymbol], query: str) -> tuple[int, rank_re.RankedSymbol] | None:
    lower_query = query.lower()
    for index, item in enumerate(ranked, start=1):
        if lower_query in item.name.lower():
            return index, item
    return None


def evaluate_fixture(fixture_path: Path, limit: int) -> tuple[bool, str]:
    fixture = load_fixture(fixture_path)
    target = fixture_path.parent / fixture["target"]
    custom_keywords = fixture.get("custom_keywords", [])
    min_length = int(fixture.get("min_length", 4))
    context_limit = int(fixture.get("context_limit", 20))

    if not target.exists():
        return False, f"[FAIL] {fixture_path.name}: target not found: {target}"

    strings_result = rank_re.run_strings_raw(str(target), min_length)
    if not strings_result.ok:
        return False, f"[FAIL] {fixture_path.name}: strings failed on {target}"

    ranked_strings = rank_re.rank_strings_from_output(strings_result.stdout, custom_keywords)
    context = rank_re.derive_strings_context(ranked_strings, custom_keywords, context_limit)

    nm_result = rank_re.run_nm_raw(str(target))
    ranked_symbols: list[rank_re.RankedSymbol] = []
    nm_ok = nm_result.ok
    if nm_ok:
        ranked_symbols = rank_re.rank_nm_from_output(nm_result.stdout, custom_keywords, context)

    all_results: list[MatchResult] = []

    for entry in fixture.get("expected_strings", []):
        query = entry["contains"]
        max_rank = entry.get("max_rank")
        enforce = bool(entry.get("enforce", True))
        match = find_string_match(ranked_strings, query)
        if match is None:
            all_results.append(
                MatchResult(query=query, kind="string", found=False, rank=None, score=None, detail=None, max_rank=max_rank, enforce=enforce, passed=not enforce)
            )
            continue
        rank_num, item = match
        passed = True if max_rank is None else rank_num <= max_rank
        all_results.append(
            MatchResult(
                query=query,
                kind="string",
                found=True,
                rank=rank_num,
                score=item.score,
                detail=item.text,
                max_rank=max_rank,
                enforce=enforce,
                passed=(passed or not enforce),
            )
        )

    for entry in fixture.get("expected_symbols", []):
        query = entry["contains"]
        max_rank = entry.get("max_rank")
        enforce = bool(entry.get("enforce", True))
        if not nm_ok:
            all_results.append(
                MatchResult(query=query, kind="symbol", found=False, rank=None, score=None, detail="nm failed", max_rank=max_rank, enforce=enforce, passed=not enforce)
            )
            continue
        match = find_symbol_match(ranked_symbols, query)
        if match is None:
            all_results.append(
                MatchResult(query=query, kind="symbol", found=False, rank=None, score=None, detail=None, max_rank=max_rank, enforce=enforce, passed=not enforce)
            )
            continue
        rank_num, item = match
        passed = True if max_rank is None else rank_num <= max_rank
        range_text = (
            f"0x{item.address:x}-0x{item.end_address:x}"
            if item.end_address is not None
            else f"0x{item.address:x}"
        )
        all_results.append(
            MatchResult(
                query=query,
                kind="symbol",
                found=True,
                rank=rank_num,
                score=item.score,
                detail=f"{range_text} {item.name}",
                max_rank=max_rank,
                enforce=enforce,
                passed=(passed or not enforce),
            )
        )

    passed_fixture = all(result.passed for result in all_results if result.enforce)

    lines: list[str] = []
    status = "PASS" if passed_fixture else "FAIL"
    lines.append(f"[{status}] {fixture_path.name} -> {target}")
    lines.append("  Top strings:")
    for item in ranked_strings[:limit]:
        lines.append(f"    #{ranked_strings.index(item)+1:<3} score={item.score:<3} 0x{item.offset:x} {item.text[:110]}")
    if nm_ok:
        lines.append("  Top symbols:")
        for item in ranked_symbols[:limit]:
            rank_num = ranked_symbols.index(item) + 1
            range_text = (
                f"0x{item.address:x}-0x{item.end_address:x}"
                if item.end_address is not None
                else f"0x{item.address:x}"
            )
            lines.append(f"    #{rank_num:<3} score={item.score:<3} {range_text} {item.name[:100]}")
    else:
        lines.append("  Top symbols:")
        lines.append("    nm failed on this file")

    lines.append("  Expected matches:")
    for result in all_results:
        if result.found:
            threshold = f", max_rank={result.max_rank}" if result.max_rank is not None else ""
            if result.enforce:
                match_status = "PASS" if result.passed else "FAIL"
            else:
                match_status = "INFO"
            lines.append(
                f"    [{match_status}] {result.kind:<6} '{result.query}' -> rank #{result.rank}, score={result.score}{threshold}"
            )
            lines.append(f"           {result.detail}")
        else:
            match_status = "FAIL" if result.enforce else "INFO"
            lines.append(f"    [{match_status}] {result.kind:<6} '{result.query}' -> not found")

    return passed_fixture, "\n".join(lines)


def discover_fixtures(fixtures_dir: Path) -> list[Path]:
    if not fixtures_dir.exists():
        return []
    return sorted(fixtures_dir.glob("*.json"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fixture-driven regression test harness for rank_re.py."
    )
    parser.add_argument(
        "--fixtures-dir",
        default="/home/jh1h1h/Downloads/test-files",
        help="Directory containing binary fixtures and expectation JSON files",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="How many top strings/symbols to print per fixture report",
    )
    args = parser.parse_args()

    fixtures_dir = Path(args.fixtures_dir)
    fixture_files = discover_fixtures(fixtures_dir)
    if not fixture_files:
        raise SystemExit(f"No fixture JSON files found in {fixtures_dir}")

    all_passed = True
    reports: list[str] = []
    for fixture_path in fixture_files:
        passed, report = evaluate_fixture(fixture_path, args.limit)
        all_passed &= passed
        reports.append(report)

    print("\n\n".join(reports))
    raise SystemExit(0 if all_passed else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys
from dataclasses import dataclass


STRING_KEYWORDS = {
    "debug": 9,
    "developer": 10,
    "hidden": 9,
    "secret": 8,
    "flag": 10,
    "token": 10,
    "auth": 9,
    "authenticate": 10,
    "password": 9,
    "passphrase": 9,
    "key": 7,
    "mode": 6,
    "command": 6,
    "usage": 5,
    "help": 5,
    "error": 5,
    "fail": 6,
    "success": 6,
    "admin": 8,
    "test": 4,
    "repair": 4,
    "quit": 3,
    "status": 3,
    "list": 3,
    "exec": 5,
    "run": 3,
    "tmp": 5,
}

SYMBOL_KEYWORDS = {
    "main": 12,
    "mode": 10,
    "command": 9,
    "dispatch": 10,
    "handle": 9,
    "verify": 12,
    "check": 10,
    "auth": 11,
    "validate": 9,
    "parse": 8,
    "process": 8,
    "execute": 9,
    "exec": 8,
    "run": 7,
    "debug": 9,
    "dev": 9,
    "developer": 10,
    "flag": 12,
    "secret": 10,
    "token": 10,
    "key": 6,
    "read": 6,
    "write": 7,
    "create": 7,
    "open": 6,
    "path": 5,
    "file": 5,
    "construct": 6,
    "encode": 5,
    "decode": 5,
    "mix": 5,
    "launch": 7,
    "spawn": 7,
}

CUSTOM_KEYWORD_WEIGHT = 10
NM_CONTEXT_KEYWORD_WEIGHT = 6
NM_CONTEXT_CONCEPT_WEIGHT = 8

STRING_LOW_SIGNAL_PATTERNS = [
    (re.compile(r"^_ZN"), 18, "mangled symbol"),
    (re.compile(r"(core::|alloc::|std::|tokio::|serde::|hashbrown::)"), 12, "runtime/library namespace"),
    (re.compile(r"(GCC:|GLIBC_|libc\.so|ld-linux|crt1|libstdc\+\+)"), 12, "toolchain/runtime marker"),
    (re.compile(r"(/usr/lib/debug|\.debug_[a-z_]+|gnu_debuglink)"), 22, "debug section noise"),
    (re.compile(r"(panicked at|stack backtrace|assertion failed)"), 6, "panic/runtime text"),
    (re.compile(r"(fatal runtime error|RUST_BACKTRACE|TLS keys|panic|verbose backtrace)"), 16, "rust runtime noise"),
    (re.compile(r"^[A-Za-z_][A-Za-z0-9_]*::[A-Za-z0-9_:<>$]+$"), 10, "namespace-like symbol"),
    (re.compile(r"^[A-Za-z0-9_./+-]{1,6}$"), 4, "very short token"),
]

SYMBOL_LOW_SIGNAL_PATTERNS = [
    (re.compile(r"drop_in_place"), 20, "drop glue"),
    (re.compile(r"^core::|^alloc::|^std::"), 12, "stdlib namespace"),
    (re.compile(r"(fmt::|panicking|backtrace|symbolize|unwind|lang_start)"), 12, "runtime/plumbing"),
    (re.compile(r"(Iterator|iter::|slice::|vec::|btree|hashbrown)"), 8, "collection/iterator glue"),
    (re.compile(r"closure"), 4, "closure glue"),
    (re.compile(r"debug_assert"), 4, "assert helper"),
]

USER_FACING_PATTERNS = [
    (re.compile(r"[A-Za-z].*[ .,:;?!][A-Za-z]"), 8, "sentence-like text"),
    (re.compile(r"(/[^ ]+|[A-Za-z]:\\\\[^ ]+)"), 7, "path-like text"),
    (re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b"), 5, "env-var-like token"),
    (re.compile(r"`[^`]+`"), 3, "contains command-like quoting"),
]

CONCEPT_BOOSTS = {
    "auth": {
        "strings": {"auth", "authenticate", "token", "password", "passphrase", "key"},
        "symbols": {"verify", "check", "auth", "validate", "token", "key"},
    },
    "hidden": {
        "strings": {"developer", "debug", "hidden", "mode", "command", "admin"},
        "symbols": {"dev", "debug", "mode", "command", "dispatch", "handle"},
    },
    "artifact": {
        "strings": {"flag", "secret", "tmp", "success", "fail", "error"},
        "symbols": {"flag", "secret", "file", "path", "read", "write", "open", "create", "exec", "run"},
    },
}


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


@dataclass
class RankedString:
    score: int
    offset: int
    text: str
    reasons: list[str]


@dataclass
class RankedSymbol:
    score: int
    address: int
    end_address: int | None
    symbol_type: str
    name: str
    reasons: list[str]


@dataclass
class StringsContext:
    keywords: set[str]
    concepts: set[str]


def run_command(cmd: list[str]) -> CommandResult:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return CommandResult(
        ok=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
    )


def parse_custom_keywords(values: list[str] | None) -> list[str]:
    if not values:
        return []

    keywords: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in re.split(r"[,;|]", value):
            token = part.strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            keywords.append(token)
    return keywords


def score_bucket(score: int) -> str:
    if score >= 18:
        return "high"
    if score >= 8:
        return "medium"
    return "low"


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("target", help="Binary or file to analyze")
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=40,
        help="Number of ranked results to print per section",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="Only show results at or above this score",
    )
    parser.add_argument(
        "--show-low",
        action="store_true",
        help="Include low-signal results in the output",
    )
    parser.add_argument(
        "--add-custom",
        action="append",
        default=[],
        metavar="WORDS",
        help=(
            "Boost entries containing custom words; repeat option or separate "
            "multiple values with ',', ';', or '|'"
        ),
    )


def add_strings_args(parser: argparse.ArgumentParser) -> None:
    add_common_args(parser)
    parser.add_argument(
        "--min-length",
        type=int,
        default=4,
        help="Minimum strings length passed to strings",
    )


def add_nm_args(parser: argparse.ArgumentParser) -> None:
    add_common_args(parser)
    parser.add_argument(
        "--min-length",
        type=int,
        default=4,
        help="Minimum strings length used for the automatic strings prepass",
    )
    parser.add_argument(
        "--no-strings-context",
        action="store_true",
        help="Do not run strings first to boost related nm symbols",
    )
    parser.add_argument(
        "--context-limit",
        type=int,
        default=20,
        help="How many top-ranked strings to inspect when building nm context",
    )


def run_strings_raw(target: str, min_length: int) -> CommandResult:
    return run_command(["strings", "-t", "x", f"-{min_length}", target])


def parse_strings_output(output: str) -> list[tuple[int, str]]:
    parsed: list[tuple[int, str]] = []
    for line in output.splitlines():
        parts = line.rstrip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        try:
            offset = int(parts[0], 16)
        except ValueError:
            continue
        text = parts[1].strip()
        if text:
            parsed.append((offset, text))
    return parsed


def score_string(text: str, custom_keywords: list[str] | None = None) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    lower = text.lower()

    for word, weight in STRING_KEYWORDS.items():
        if word in lower:
            score += weight
            reasons.append(f"keyword:{word}")

    for word in custom_keywords or []:
        if word in lower:
            score += CUSTOM_KEYWORD_WEIGHT
            reasons.append(f"custom:{word}")

    for pattern, weight, reason in USER_FACING_PATTERNS:
        if pattern.search(text):
            score += weight
            reasons.append(reason)

    if " " in text:
        score += 3
        reasons.append("contains spaces")
    if len(text) >= 12:
        score += 2
        reasons.append("usable length")
    if len(text) >= 24:
        score += 2
        reasons.append("descriptive")
    if re.search(r"[=:]", text):
        score += 2
        reasons.append("structured text")
    if re.search(r"[.]{2,}|[!?]", text):
        score += 2
        reasons.append("message punctuation")
    if 8 <= len(text.split()) <= 20:
        score += 3
        reasons.append("human-sized message")

    for pattern, weight, reason in STRING_LOW_SIGNAL_PATTERNS:
        if pattern.search(text):
            score -= weight
            reasons.append(f"low:{reason}")

    if len(text) > 180:
        score -= 18
        reasons.append("low:oversized blob")
    elif len(text) > 120:
        score -= 8
        reasons.append("low:very long")
    if len(set(text)) <= 3:
        score -= 8
        reasons.append("low:repetitive")
    if re.fullmatch(r"[0-9a-fA-F]{16,}", text):
        score -= 5
        reasons.append("low:hex blob")
    if re.fullmatch(r"[A-Za-z0-9+/=]{20,}", text):
        score -= 3
        reasons.append("low:opaque token")
    if sum(ch.isupper() for ch in text) > 25 and " " not in text:
        score -= 6
        reasons.append("low:symbol-like")

    return score, reasons


def rank_strings_from_output(output: str, custom_keywords: list[str]) -> list[RankedString]:
    ranked: list[RankedString] = []
    for offset, text in parse_strings_output(output):
        score, reasons = score_string(text, custom_keywords)
        ranked.append(RankedString(score=score, offset=offset, text=text, reasons=reasons))
    ranked.sort(key=lambda item: (-item.score, item.offset, item.text))
    return ranked


def derive_strings_context(
    ranked_strings: list[RankedString],
    custom_keywords: list[str],
    context_limit: int,
) -> StringsContext:
    keywords = set(custom_keywords)
    concepts: set[str] = set()

    for item in ranked_strings[:context_limit]:
        if score_bucket(item.score) == "low":
            continue
        if len(item.text) > 100:
            continue
        lower = item.text.lower()

        for keyword in SYMBOL_KEYWORDS:
            if keyword in lower:
                keywords.add(keyword)

        for concept, mapping in CONCEPT_BOOSTS.items():
            if any(token in lower for token in mapping["strings"]):
                concepts.add(concept)

    return StringsContext(keywords=keywords, concepts=concepts)


def symbol_name_tokens(name: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", name.lower()) if token}


def run_nm_raw(target: str) -> CommandResult:
    return run_command(["nm", "-n", "-C", target])


def parse_nm_output(output: str) -> list[tuple[int, str, str]]:
    parsed: list[tuple[int, str, str]] = []
    for line in output.splitlines():
        parts = line.rstrip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        addr_text, symbol_type, name = parts
        if not re.fullmatch(r"[0-9a-fA-F]+", addr_text):
            continue
        try:
            address = int(addr_text, 16)
        except ValueError:
            continue
        parsed.append((address, symbol_type, name))
    return parsed


def score_symbol(
    name: str,
    symbol_type: str,
    custom_keywords: list[str],
    context: StringsContext | None,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    lower = name.lower()
    tokens = symbol_name_tokens(name)

    for word, weight in SYMBOL_KEYWORDS.items():
        if word in lower or word in tokens:
            score += weight
            reasons.append(f"keyword:{word}")

    for word in custom_keywords:
        if word in lower or word in tokens:
            score += CUSTOM_KEYWORD_WEIGHT
            reasons.append(f"custom:{word}")

    if context is not None:
        for word in sorted(context.keywords):
            if word in lower or word in tokens:
                score += NM_CONTEXT_KEYWORD_WEIGHT
                reasons.append(f"context:{word}")
        for concept in sorted(context.concepts):
            if any(token in lower or token in tokens for token in CONCEPT_BOOSTS[concept]["symbols"]):
                score += NM_CONTEXT_CONCEPT_WEIGHT
                reasons.append(f"context-concept:{concept}")

    if symbol_type in {"T", "t", "W", "w"}:
        score += 4
        reasons.append("code symbol")
    elif symbol_type in {"D", "d", "R", "r", "B", "b"}:
        score += 1
        reasons.append("data symbol")

    if "::" in name and not lower.startswith(("core::", "alloc::", "std::")):
        score += 3
        reasons.append("non-stdlib namespace")
    if re.search(r"(mode|verify|check|debug|dev|command)", lower):
        score += 2
        reasons.append("control-flow hint")

    for pattern, weight, reason in SYMBOL_LOW_SIGNAL_PATTERNS:
        if pattern.search(name):
            score -= weight
            reasons.append(f"low:{reason}")

    if len(name) > 180:
        score -= 14
        reasons.append("low:oversized symbol")
    elif len(name) > 120:
        score -= 6
        reasons.append("low:very long symbol")

    return score, reasons


def rank_nm_from_output(
    output: str,
    custom_keywords: list[str],
    context: StringsContext | None,
) -> list[RankedSymbol]:
    ranked: list[RankedSymbol] = []
    parsed = parse_nm_output(output)

    next_greater_address: list[int | None] = [None] * len(parsed)
    upcoming: int | None = None
    for idx in range(len(parsed) - 1, -1, -1):
        address, _, _ = parsed[idx]
        next_greater_address[idx] = upcoming
        if upcoming is None or address < upcoming:
            upcoming = address

    for idx, (address, symbol_type, name) in enumerate(parsed):
        end_address = next_greater_address[idx]
        score, reasons = score_symbol(name, symbol_type, custom_keywords, context)
        ranked.append(
            RankedSymbol(
                score=score,
                address=address,
                end_address=end_address,
                symbol_type=symbol_type,
                name=name,
                reasons=reasons,
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.address, item.name))
    return ranked


def print_ranked_strings(
    ranked: list[RankedString],
    limit: int,
    min_score: int | None,
    show_low: bool,
) -> int:
    shown = 0
    for item in ranked:
        level = score_bucket(item.score)
        if not show_low and level == "low":
            continue
        if min_score is not None and item.score < min_score:
            continue
        print(
            f"{item.score:>3}  {level:<6}  0x{item.offset:>x}  {item.text}\n"
            f"      reasons: {', '.join(item.reasons[:8])}"
        )
        shown += 1
        if shown >= limit:
            break
    return shown


def print_ranked_symbols(
    ranked: list[RankedSymbol],
    limit: int,
    min_score: int | None,
    show_low: bool,
) -> int:
    shown = 0
    for item in ranked:
        level = score_bucket(item.score)
        if not show_low and level == "low":
            continue
        if min_score is not None and item.score < min_score:
            continue
        if item.end_address is None:
            range_text = f"0x{item.address:>x}"
        else:
            range_text = f"0x{item.address:>x}-0x{item.end_address:>x}"
        print(
            f"{item.score:>3}  {level:<6}  {range_text:<23}  {item.symbol_type}  {item.name}\n"
            f"      reasons: {', '.join(item.reasons[:8])}"
        )
        shown += 1
        if shown >= limit:
            break
    return shown


def load_strings_context(
    target: str,
    min_length: int,
    custom_keywords: list[str],
    context_limit: int,
) -> tuple[StringsContext | None, list[RankedString] | None]:
    result = run_strings_raw(target, min_length)
    if not result.ok:
        sys.stderr.write("note: strings prepass failed; continuing without string context\n")
        if result.stderr:
            sys.stderr.write(result.stderr)
        return None, None
    ranked_strings = rank_strings_from_output(result.stdout, custom_keywords)
    context = derive_strings_context(ranked_strings, custom_keywords, context_limit)
    return context, ranked_strings


def do_strings(args: argparse.Namespace) -> int:
    custom_keywords = parse_custom_keywords(args.add_custom)
    result = run_strings_raw(args.target, args.min_length)
    if not result.ok:
        sys.stderr.write("strings failed on this file\n")
        if result.stderr:
            sys.stderr.write(result.stderr)
        return 1

    ranked = rank_strings_from_output(result.stdout, custom_keywords)
    shown = print_ranked_strings(ranked, args.limit, args.min_score, args.show_low)
    return 0 if shown >= 0 else 1


def do_nm(args: argparse.Namespace) -> int:
    custom_keywords = parse_custom_keywords(args.add_custom)
    context = None
    if not args.no_strings_context:
        context, _ = load_strings_context(
            args.target,
            args.min_length,
            custom_keywords,
            args.context_limit,
        )
        if context is not None and (context.keywords or context.concepts):
            print(
                "context: "
                f"keywords={','.join(sorted(context.keywords)) or '-'} "
                f"concepts={','.join(sorted(context.concepts)) or '-'}"
            )

    result = run_nm_raw(args.target)
    if not result.ok:
        sys.stderr.write("nm failed on this file\n")
        if result.stderr:
            sys.stderr.write(result.stderr)
        return 1

    ranked = rank_nm_from_output(result.stdout, custom_keywords, context)
    print_ranked_symbols(ranked, args.limit, args.min_score, args.show_low)
    return 0


def do_both(args: argparse.Namespace) -> int:
    custom_keywords = parse_custom_keywords(args.add_custom)
    any_success = False

    strings_result = run_strings_raw(args.target, args.min_length)
    ranked_strings: list[RankedString] | None = None
    context = None

    print("== strings ==")
    if strings_result.ok:
        ranked_strings = rank_strings_from_output(strings_result.stdout, custom_keywords)
        print_ranked_strings(ranked_strings, args.limit, args.min_score, args.show_low)
        context = derive_strings_context(ranked_strings, custom_keywords, args.context_limit)
        any_success = True
    else:
        print("strings failed on this file")
        if strings_result.stderr:
            sys.stderr.write(strings_result.stderr)

    print()
    print("== nm ==")
    if context is not None and (context.keywords or context.concepts):
        print(
            "context: "
            f"keywords={','.join(sorted(context.keywords)) or '-'} "
            f"concepts={','.join(sorted(context.concepts)) or '-'}"
        )

    nm_result = run_nm_raw(args.target)
    if nm_result.ok:
        ranked_nm = rank_nm_from_output(nm_result.stdout, custom_keywords, context)
        print_ranked_symbols(ranked_nm, args.limit, args.min_score, args.show_low)
        any_success = True
    else:
        print("nm failed on this file")
        if nm_result.stderr:
            sys.stderr.write(nm_result.stderr)

    return 0 if any_success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank strings and nm output by likely reversing signal."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    strings_parser = subparsers.add_parser(
        "strings",
        help="Rank strings output",
    )
    add_strings_args(strings_parser)
    strings_parser.set_defaults(handler=do_strings)

    nm_parser = subparsers.add_parser(
        "nm",
        help="Rank nm -C output, optionally using a strings prepass",
    )
    add_nm_args(nm_parser)
    nm_parser.set_defaults(handler=do_nm)

    both_parser = subparsers.add_parser(
        "both",
        help="Print ranked strings first, then ranked nm output using strings context",
    )
    add_nm_args(both_parser)
    both_parser.set_defaults(handler=do_both)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()

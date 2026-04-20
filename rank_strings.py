#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys
from dataclasses import dataclass


HIGH_SIGNAL_KEYWORDS = {
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

LOW_SIGNAL_PATTERNS = [
    (re.compile(r"^_ZN"), 18, "mangled symbol"),
    (re.compile(r"(core::|alloc::|std::|tokio::|serde::|hashbrown::)"), 12, "runtime/library namespace"),
    (re.compile(r"(GCC:|GLIBC_|libc\.so|ld-linux|crt1|libstdc\+\+)"), 12, "toolchain/runtime marker"),
    (re.compile(r"(/usr/lib/debug|\.debug_[a-z_]+|gnu_debuglink)"), 22, "debug section noise"),
    (re.compile(r"(panicked at|stack backtrace|assertion failed)"), 6, "panic/runtime text"),
    (re.compile(r"(fatal runtime error|RUST_BACKTRACE|TLS keys|panic|verbose backtrace)"), 16, "rust runtime noise"),
    (re.compile(r"^[A-Za-z_][A-Za-z0-9_]*::[A-Za-z0-9_:<>$]+$"), 10, "namespace-like symbol"),
    (re.compile(r"^[A-Za-z0-9_./+-]{1,6}$"), 4, "very short token"),
]

USER_FACING_PATTERNS = [
    (re.compile(r"[A-Za-z].*[ .,:;?!][A-Za-z]"), 8, "sentence-like text"),
    (re.compile(r"(/[^ ]+|[A-Za-z]:\\\\[^ ]+)"), 7, "path-like text"),
    (re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b"), 5, "env-var-like token"),
    (re.compile(r"`[^`]+`"), 3, "contains command-like quoting"),
]


@dataclass
class RankedString:
    score: int
    offset: int
    text: str
    reasons: list[str]


def run_strings(target: str, min_length: int) -> list[tuple[int, str]]:
    cmd = ["strings", "-t", "x", f"-{min_length}", target]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or exc.stdout or "strings failed\n")
        sys.exit(exc.returncode or 1)

    out: list[tuple[int, str]] = []
    for line in proc.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        try:
            offset = int(parts[0], 16)
        except ValueError:
            continue
        text = parts[1].strip()
        if text:
            out.append((offset, text))
    return out


def score_string(text: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    lower = text.lower()

    for word, weight in HIGH_SIGNAL_KEYWORDS.items():
        if word in lower:
            score += weight
            reasons.append(f"keyword:{word}")

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

    for pattern, weight, reason in LOW_SIGNAL_PATTERNS:
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


def bucket(score: int) -> str:
    if score >= 18:
        return "high"
    if score >= 8:
        return "medium"
    return "low"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank strings output by likely reversing signal."
    )
    parser.add_argument("target", help="Binary or file to scan with strings")
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=40,
        help="Number of ranked results to print",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=4,
        help="Minimum strings length passed to strings",
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
    args = parser.parse_args()

    ranked: list[RankedString] = []
    for offset, text in run_strings(args.target, args.min_length):
        score, reasons = score_string(text)
        ranked.append(RankedString(score=score, offset=offset, text=text, reasons=reasons))

    ranked.sort(key=lambda item: (-item.score, item.offset, item.text))

    shown = 0
    for item in ranked:
        level = bucket(item.score)
        if not args.show_low and level == "low":
            continue
        if args.min_score is not None and item.score < args.min_score:
            continue
        print(
            f"{item.score:>3}  {level:<6}  0x{item.offset:>x}  {item.text}\n"
            f"      reasons: {', '.join(item.reasons[:8])}"
        )
        shown += 1
        if shown >= args.limit:
            break


if __name__ == "__main__":
    main()

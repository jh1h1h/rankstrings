# `rank_strings.py`

`rank_strings.py` is a small helper for reversing workflows. It runs `strings` against a target file, scores each extracted string with a few heuristics, and prints the most promising candidates first.

The goal is not to replace manual analysis. The goal is to move likely high-signal strings such as prompts, hidden mode names, token/auth text, file paths, and output messages above runtime noise and debug junk.

## Requirements

- Python 3
- GNU or compatible `strings`

## Usage

Basic run:

```bash
./rank_strings.py /path/to/binary
```

Show more results:

```bash
./rank_strings.py /path/to/binary --limit 80
```

Only show stronger candidates:

```bash
./rank_strings.py /path/to/binary --min-score 20
```

Change the minimum string length passed to `strings`:

```bash
./rank_strings.py /path/to/binary --min-length 6
```

Include low-signal results as well:

```bash
./rank_strings.py /path/to/binary --show-low
```

Example:

```bash
./rank_strings.py /home/jh1h1h/Downloads/DSO_Challenge/crabby-repair --limit 20
```

## Output Format

Each result is printed like this:

```text
 46  high    0xd11d  Authenticating Developer Token ...
      reasons: keyword:developer, keyword:token, keyword:auth, sentence-like text, contains spaces
```

Fields:

- `46`: the numeric score
- `high`: the score bucket
- `0xd11d`: the string offset reported by `strings -t x`
- the string text itself
- `reasons`: the main heuristic reasons that increased or decreased the score

The offset is important because it lets you correlate the string with nearby code in `objdump`, `radare2`, `ghidra`, or another disassembler.

## How Ranking Works

The script uses a simple additive scoring model. It does not try to understand the whole program. It just favors strings that tend to be useful during reversing.

### Factors That Push Strings Higher

These features add score:

- High-signal keywords such as:
  - `developer`
  - `debug`
  - `flag`
  - `token`
  - `auth`
  - `password`
  - `secret`
  - `mode`
  - `command`
  - `help`
  - `fail`
  - `success`
- Sentence-like text
  - Prompts, errors, banners, and status messages are often more useful than isolated words.
- Path-like strings
  - Examples: `/tmp/foo`, `/usr/bin/...`, `C:\...`
- Environment-variable-like tokens
  - Examples: `TMPDIR`, `CRYPTIFY_KEY`
- Strings with spaces
  - Human-facing prompts and messages often contain spaces.
- Useful lengths
  - Very short strings are often ambiguous. Moderately long descriptive strings are often better clues.
- Structured text
  - Strings containing `=` or `:` are often configuration text, prompts, or messages.
- Message punctuation
  - `...`, `?`, `!` and similar markers often show user-facing output.
- Human-sized messages
  - Medium-length strings are often better than giant concatenated blobs.

### Factors That Push Strings Lower

These features reduce score:

- Rust mangled symbols
  - Example: strings beginning with `_ZN...`
- Standard library and runtime namespaces
  - `core::`, `alloc::`, `std::`, `tokio::`, `serde::`, and similar
- Toolchain/runtime markers
  - `GLIBC_`, `ld-linux`, `libc.so`, and related noise
- Debug-section noise
  - `/usr/lib/debug`, `.debug_*`, `gnu_debuglink`
- Panic and runtime text
  - `panicked at`, `stack backtrace`, `fatal runtime error`, `RUST_BACKTRACE`
- Very short tokens
  - These are often too ambiguous to be useful on their own.
- Oversized concatenated blobs
  - Large multi-message chunks often come from packed rodata or debug content and are harder to act on directly.
- Repetitive strings
  - Very low-information content gets penalized.
- Opaque hex or base64-like blobs
  - These may still matter, but usually they are worse first targets than prompts or filenames.
- Symbol-like all-caps noise without spaces
  - Often library or runtime internals rather than intended challenge clues.

## Why Offsets Matter

The script keeps the offset from `strings -t x` on purpose.

Once a string looks interesting, a common next step is:

```bash
objdump -d <target> | less
```

or:

```bash
objdump -s --start-address=<offset-nearby> --stop-address=<offset-nearby> <target>
```

Then search for the relevant area and look for:

- references to that rodata
- nearby success and failure messages
- calls into verifier or mode-handling functions
- file names, paths, or environment variables tied to the same region

## What The Script Is Good At

- Surfacing likely prompts and menu text
- Highlighting auth/token/flag-related strings
- Surfacing hidden mode names and developer/debug clues
- Promoting path names and environment-variable names
- Pushing obvious Rust runtime junk lower

## What The Script Is Not Good At

- It does not prove that a top-ranked string is important.
- It does not decompile or trace control flow.
- It does not understand context across nearby strings.
- It may still rank some runtime text too highly.
- It may rank a real clue too low if it does not match the current heuristics.

Treat the output as a prioritization aid, not as ground truth.

## Recommended Workflow

One practical workflow is:

1. Run the scorer.
2. Take the top 10-20 strings.
3. Ignore obvious runtime/debug/toolchain noise.
4. Focus on:
   - prompts
   - hidden commands
   - token/auth strings
   - file paths
   - output artifact names
5. Correlate those strings with:
   - `file <target>`
   - `./<target>`
   - `nm -C <target>`
   - `objdump -d --start-address=<start> --stop-address=<stop> <target>`

For example:

```bash
./rank_strings.py <target> --limit 20
nm -C <target> | rg 'main|mode|command|verify|auth|debug|dev'
objdump -d --start-address=<start> --stop-address=<stop> <target>
```

## Extending The Heuristics

If you want to tune the ranking, edit these sections in the script:

- `HIGH_SIGNAL_KEYWORDS`
- `LOW_SIGNAL_PATTERNS`
- `USER_FACING_PATTERNS`
- `score_string()`

The easiest customization points are:

- add challenge-specific keywords
- penalize additional runtime namespaces
- increase or decrease the score of path-like strings
- change the thresholds used for `high`, `medium`, and `low`

## Notes

- The script shells out to `strings`, so behavior depends in part on the local `strings` implementation.
- The output is most useful on executables and mixed binary blobs, but it can also be used on other file types.
- For stripped Rust binaries, the script is often most valuable when paired with runtime testing and small-slice disassembly.

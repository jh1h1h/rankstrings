# `rank_re.py`

`rank_re.py` is a reversing helper that ranks two kinds of output:

- `strings`
- `nm -C`

It is designed to push likely high-signal clues such as prompts, hidden mode names, verifier-related functions, file/process handlers, and output artifacts above standard library noise and runtime junk.

There is also a compatibility wrapper:

- `rank_strings.py`

If you run `rank_strings.py` without a subcommand, it behaves like:

```bash
./rank_re.py strings <target>
```

## Requirements

- Python 3
- `strings`
- `nm`

## Modes

### `strings`

Ranks `strings -t x` output.

```bash
./rank_re.py strings /path/to/binary
```

### `nm`

Ranks `nm -n -C` output.

By default it also runs a lightweight `strings` prepass first and uses that to boost related symbol names.

```bash
./rank_re.py nm /path/to/binary
```

Disable the automatic `strings` prepass:

```bash
./rank_re.py nm /path/to/binary --no-strings-context
```

### `both`

Prints ranked `strings` first, then ranked `nm` output with cross-signal context enabled.

```bash
./rank_re.py both /path/to/binary
```

## Common Options

Show more or fewer results:

```bash
./rank_re.py strings /path/to/binary --limit 80
```

Only show stronger results:

```bash
./rank_re.py nm /path/to/binary --min-score 20
```

Include low-signal results too:

```bash
./rank_re.py both /path/to/binary --show-low
```

Boost custom words:

```bash
./rank_re.py strings /path/to/binary --add-custom verifier,secret,payload
./rank_re.py nm /path/to/binary --add-custom verifier --add-custom payload
```

Adjust the minimum string length used by `strings`:

```bash
./rank_re.py strings /path/to/binary --min-length 6
./rank_re.py nm /path/to/binary --min-length 6
```

## Output Format

### Strings Mode

```text
 46  high    0xd11d  Authenticating Developer Token ...
      reasons: keyword:developer, keyword:token, keyword:auth, sentence-like text
```

Fields:

- score
- score bucket
- string offset from `strings -t x`
- string text
- the main reasons that increased or decreased the score

### NM Mode

```text
 35  high    0xc7950  t  crabby_repair::verify::verify
      reasons: keyword:verify, keyword:auth, code symbol, non-stdlib namespace
```

Fields:

- score
- score bucket
- symbol address from `nm -n -C`
- symbol type
- demangled symbol name
- the main reasons that increased or decreased the score

## How Strings Are Ranked

Strings get pushed higher when they look like useful reversing clues.

### Positive Factors

- High-signal keywords such as:
  - `developer`
  - `debug`
  - `hidden`
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
- Path-like text
- Environment-variable-like tokens
- Strings with spaces
- Descriptive length
- Structured text such as `foo: bar`
- Message punctuation such as `...` or `?`
- Human-sized messages rather than huge concatenated blobs
- Any custom keywords you pass with `--add-custom`

### Negative Factors

- Rust mangled symbol text
- `core::`, `alloc::`, `std::`, and similar runtime noise
- GLIBC/toolchain markers
- debug-section noise such as `/usr/lib/debug` and `.debug_*`
- panic/backtrace/runtime-error text
- very short strings
- oversized concatenated blobs
- repetitive low-information strings
- opaque hex-like or base64-like blobs
- symbol-like all-caps noise without spaces

## How NM Symbols Are Ranked

`nm` ranking is more function-oriented.

### Positive Factors

- Function names containing likely control-flow words:
  - `main`
  - `mode`
  - `command`
  - `dispatch`
  - `handle`
- Gate/validation words:
  - `verify`
  - `check`
  - `auth`
  - `validate`
  - `token`
  - `key`
- Side-effect words:
  - `read`
  - `write`
  - `open`
  - `create`
  - `exec`
  - `run`
  - `spawn`
  - `launch`
- Transformation words:
  - `construct`
  - `encode`
  - `decode`
  - `mix`
  - `parse`
  - `process`
- Code symbol types like `T`, `t`, `W`, `w`
- Non-stdlib namespaces
- Custom keywords from `--add-custom`

### Negative Factors

- `drop_in_place`
- `core::`, `alloc::`, `std::`
- formatting, panicking, backtrace, unwind, lang-start helpers
- iterator/collection glue
- closure-heavy generic names
- very large symbol names

## Cross-Signal Ranking

This is the main reason the tool was combined instead of keeping separate unrelated scripts.

`nm` mode can use a `strings` prepass to improve symbol ranking.

The idea:

1. Run `strings`
2. Take the top-ranked strings
3. Extract high-signal keywords and concepts
4. Boost symbol names that match those concepts

Example:

If top strings include:

- `Authenticating Developer Token ...`
- `Running Utility in Developer Mode ...`
- `Token Authenticated`

then the script learns that concepts such as:

- auth
- hidden mode
- token
- developer

matter for this file.

That means symbols such as:

- `verify`
- `check`
- `auth`
- `dev_mode_enabled`
- `command`

get extra weight in `nm` mode.

This is what “cross-signal ranking” means:

- one source of evidence influences how another source is ranked

## Failure Handling

The tool is designed to fail cleanly.

Examples:

- if `strings` fails in `strings` mode:
  - it prints `strings failed on this file`
- if `nm` fails in `nm` mode:
  - it prints `nm failed on this file`
- if the automatic strings prepass fails in `nm` mode:
  - it prints a note and still ranks `nm` output without string context

This is useful for unusual file formats, stripped or incompatible targets, or files where only one tool is informative.

## Recommended Workflow

Start broad:

```bash
./rank_re.py both <target> --limit 20
```

Then use the top results to guide manual inspection:

```bash
./<target>
nm -n -C <target> | rg 'main|mode|command|verify|check|auth|debug|dev'
objdump -d --start-address=<start> --stop-address=<stop> <target>
```

Use `strings` results to prioritize:

- prompts
- hidden modes
- token/auth text
- file paths
- artifact names

Use `nm` results to prioritize:

- dispatchers
- gates/verifiers
- file/process functions
- transforms

## Files

- [rank_re.py](/home/jh1h1h/Downloads/rank_re.py)
- [rank_strings.py](/home/jh1h1h/Downloads/rank_strings.py)

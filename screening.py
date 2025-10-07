#!/usr/bin/env python3
import argparse
import csv
import os
import re
import sys
from datetime import datetime
import textwrap
import shutil

# -------- Optional Rich UI --------
HAVE_RICH = False
if os.environ.get("NO_COLOR", "").lower() not in {"1", "true", "yes"}:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
        from rich.prompt import Prompt
        from rich.theme import Theme
        HAVE_RICH = True
    except Exception:
        HAVE_RICH = False

# -------- Reasons & required columns --------
REASONS = {
    "1": "non-paper",
    "2": "survey or review",
    "3": "non-english",
    "4": "not auditing OF AI",
    "5": "other",  # asks for details; stores just "other"
}
REQUIRED_COLUMNS = ["Document Type", "Article Title", "Abstract"]

# -------- ANSI fallback (when Rich not present) --------
ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "cyan": "\033[36m",
    "magenta": "\033[35m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "red": "\033[31m",
    "dim": "\033[2m",
}
def supports_ansi():
    try:
        return sys.stdout.isatty()
    except Exception:
        return False

# -------- CSV helpers --------
def validate_columns(fieldnames, need_include_reason=False):
    missing = [c for c in REQUIRED_COLUMNS if c not in (fieldnames or [])]
    if missing:
        raise SystemExit(
            f"ERROR: Missing required column(s): {', '.join(missing)}\n"
            f"Found: {', '.join(fieldnames or [])}"
        )
    if need_include_reason:
        for c in ("include", "reason"):
            if c not in (fieldnames or []):
                raise SystemExit(
                    f"ERROR: Working file is missing '{c}'. Use --from-scratch to rebuild."
                )

def read_csv(path, encoding="utf-8"):
    with open(path, "r", encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)

def write_csv(path, fieldnames, rows, encoding="utf-8"):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    os.replace(tmp, path)

def init_or_load_work(input_csv, work_csv, encoding, from_scratch=False):
    if os.path.exists(work_csv) and not from_scratch:
        flds, rows = read_csv(work_csv, encoding)
        validate_columns(flds, need_include_reason=True)
        return flds, rows
    in_fields, in_rows = read_csv(input_csv, encoding)
    validate_columns(in_fields)
    out_fields = list(in_fields)
    if "include" not in out_fields:
        out_fields.append("include")
    if "reason" not in out_fields:
        out_fields.append("reason")
    for r in in_rows:
        r.setdefault("include", "")
        r.setdefault("reason", "")
    write_csv(work_csv, out_fields, in_rows, encoding)
    print(f"Created working copy: {work_csv} ({len(in_rows)} rows)")
    return out_fields, in_rows

# -------- Keyword building --------
def build_keyword_patterns(user_keywords):
    defaults = [
        "audit*", "fair*", "priva*", "explain*", "interpret*", "transparent*",
        "AI", "machine learning", "data mining",
    ]
    terms = defaults + (user_keywords or [])
    pats = []
    for t in terms:
        t = t.strip()
        if not t:
            continue
        p = re.escape(t).replace(r"\*", r"\w*").replace(r"\ ", r"\s+")
        pats.append(rf"\b{p}\b")
    if not pats:
        pats = [r"(?!x)x"]
    return re.compile(rf"(?i)({'|'.join(pats)})"), defaults, user_keywords or []

# -------- Rich render helpers --------
def make_console(theme_name):
    if not HAVE_RICH:
        return None
    if theme_name == "high-contrast":
        theme = Theme({
            "hdr": "bold white on blue",
            "label": "bold cyan",
            "keyword": "bold yellow",
            "prompt": "bold magenta",
            "good": "bold green",
            "bad": "bold red",
            "dim": "dim",
            "border": "cyan",
        })
    elif theme_name == "solarized":
        theme = Theme({
            "hdr": "bold black on #b58900",
            "label": "bold #268bd2",
            "keyword": "bold #b58900",
            "prompt": "bold #d33682",
            "good": "bold #859900",
            "bad": "bold #dc322f",
            "dim": "dim",
            "border": "#268bd2",
        })
    else:  # default
        theme = Theme({
            "hdr": "bold white on blue",
            "label": "bold cyan",
            "keyword": "bold yellow",
            "prompt": "bold magenta",
            "good": "bold green",
            "bad": "bold red",
            "dim": "dim",
            "border": "cyan",
        })
    # soft_wrap=True ensures Rich never truncates long lines; it wraps instead
    return Console(theme=theme, highlight=False, soft_wrap=True)

def text_with_keyword_style(s, matcher, keyword_style):
    if not HAVE_RICH:
        return s or ""
    # Explicitly disable truncation & allow wrapping
    t = Text(s or "", no_wrap=False, overflow="fold", end="")
    for m in matcher.finditer(s or ""):
        t.stylize(keyword_style, m.start(), m.end())
    return t

def show_record_rich(console, idx, x, total, doc_type, title, abstract, matcher, width):
    console.rule(f"[hdr]Row #{idx} • Progress: [{x} / {total}][/]")
    dt = text_with_keyword_style(doc_type, matcher, "keyword")
    tt = text_with_keyword_style(title, matcher, "keyword")
    ab = text_with_keyword_style(abstract, matcher, "keyword")

    # Let panels expand to full width; no artificial width cap unless user requested one
    panel_kwargs = {"border_style": "border", "expand": True}
    if width and width > 0:
        panel_kwargs["width"] = min(width, console.width - 2)

    console.print(Panel(dt, title="[label]Document Type[/]", **panel_kwargs))
    console.print(Panel(tt, title="[label]Article Title[/]", **panel_kwargs))
    console.print(Panel(ab, title="[label]Abstract[/]", **panel_kwargs))

def prompt_choice_rich(console):
    return Prompt.ask("[prompt](i)nclude, (e)xclude, (s)kip, (q)uit?[/]", choices=["i","e","s","q"])

def prompt_reason_rich(console):
    console.print("\n[prompt]Exclusion reason[/]:")
    for k in sorted(REASONS.keys(), key=int):
        console.print(f"  [label]{k})[/] {REASONS[k]}")
    code = Prompt.ask("[prompt]Enter 1–5[/]", choices=list(REASONS.keys()))
    label = REASONS[code]
    if code == "5":
        label = Prompt.ask("[prompt]Describe the 'other' reason.", default="")
    return label

# -------- Fallback print/prompts --------
def wrap_ansi(text, matcher, width, highlight=True):
    s = text or ""
    if highlight and supports_ansi():
        s = matcher.sub(lambda m: f"{ANSI['yellow']}{ANSI['bold']}{m.group(0)}{ANSI['reset']}", s)
    # If width==0, use terminal width; else use provided width
    if not width or width <= 0:
        width = max(40, shutil.get_terminal_size(fallback=(120, 25)).columns - 4)
    return textwrap.wrap(s, width=width, replace_whitespace=False)

def show_record_ansi(idx, x, total, doc_type, title, abstract, matcher, width, use_colors):
    bright = ANSI if (use_colors and supports_ansi()) else {k:"" for k in ANSI}
    print(f"{bright['bold']}{bright['cyan']}==== Row #{idx} • Progress: [{x} / {total}] ===={bright['reset']}")
    print(f"{bright['bold']}{bright['cyan']}Document Type:{bright['reset']}")
    for line in wrap_ansi(doc_type, matcher, width, highlight=use_colors):
        print("  " + line)
    print(f"\n{bright['bold']}{bright['cyan']}Article Title:{bright['reset']}")
    for line in wrap_ansi(title, matcher, width, highlight=use_colors):
        print("  " + line)
    print(f"\n{bright['bold']}{bright['cyan']}Abstract:{bright['reset']}")
    for line in wrap_ansi(abstract, matcher, width, highlight=use_colors):
        print("  " + line)
    print(bright["dim"] + "-" * max(40, min(200, width or 120)) + bright["reset"])

def prompt_choice_ansi():
    while True:
        choice = input("\033[35m[i]nclude, [e]xclude, [s]kip, [q]uit?\033[0m ").strip().lower()
        if choice in {"i","e","s","q"}:
            return choice
        print("Please enter i / e / s / q.")

def prompt_reason_ansi():
    print("\n\033[35mExclusion reason:\033[0m")
    for k in sorted(REASONS.keys(), key=int):
        print(f"  {k}) {REASONS[k]}")
    while True:
        code = input("Enter 1–5: ").strip()
        if code in REASONS:
            break
        print("Please enter a number 1–5.")
    if code == "5":
        _ = input("Describe the 'other' reason (stored as 'other'): ").strip()
    return REASONS[code]

# -------- Main --------
def main():
    parser = argparse.ArgumentParser(
        description="Interactive CSV screening tool with colorful UI and resumeable working copy."
    )
    parser.add_argument("csv_path", help="Path to the INPUT CSV file.")
    parser.add_argument("--work", default="screening_work.csv",
                        help="Path to WORKING copy CSV (default: screening_work.csv).")
    parser.add_argument("--from-scratch", action="store_true",
                        help="Rebuild the working copy from input (overwrites existing).")
    parser.add_argument("-k", "--keyword", action="append", default=[],
                        help="Add a keyword/phrase (use '*' as wildcard, repeatable).")
    parser.add_argument("--encoding", default="utf-8", help="CSV encoding (default: utf-8).")
    parser.add_argument("--width", type=int, default=0,
                        help="Max panel/line width (0 = auto full terminal width).")
    parser.add_argument("--theme", choices=["default", "high-contrast", "solarized"], default="default",
                        help="Color theme (requires 'rich').")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable colored output (plain text/ANSI off).")
    parser.add_argument("--force-color", action="store_true",
                        help="Force color even if not detected as a TTY.")
    parser.add_argument("--pager", action="store_true",
                        help="Show each paper inside a scrollable pager (no truncation).")
    parser.add_argument("--redo-completed", action="store_true",
                        help="Also revisit rows that already have decisions.")
    args = parser.parse_args()

    fieldnames, rows = init_or_load_work(
        args.csv_path, args.work, args.encoding, from_scratch=args.from_scratch
    )

    matcher, defaults, user_terms = build_keyword_patterns(args.keyword)

    total = len(rows)
    indices = range(total)
    if not args.redo_completed:
        indices = [i for i, r in enumerate(rows) if (r.get("include", "").strip() not in {"yes", "no"})]
    decided_so_far = sum(1 for r in rows if (r.get("include", "").strip() in {"yes", "no"}))

    use_rich = HAVE_RICH and not args.no_color and (args.force_color or sys.stdout.isatty())
    console = make_console(args.theme) if use_rich else None

    # Banner
    if use_rich:
        console.rule("[hdr]Keyword highlighting[/]")
        console.print(f"[label]Default[/]: " + ", ".join(defaults))
        if user_terms:
            console.print(f"[label]User[/]: " + ", ".join(user_terms))
        console.print("[dim](Keywords shown in [keyword]bold yellow[/]. Use --theme to change.)[/]\n")
    else:
        if not args.no_color and supports_ansi():
            print(f"{ANSI['cyan']}{ANSI['bold']}Keyword highlighting{ANSI['reset']}")
        print("Default: " + ", ".join(defaults))
        if user_terms:
            print("User: " + ", ".join(user_terms))
        print()

    for idx in indices:
        r = rows[idx]
        x = decided_so_far + 1 if r.get("include", "").strip() not in {"yes", "no"} else decided_so_far

        doc_type = r.get("Document Type", "") or ""
        title = r.get("Article Title", "") or ""
        abstract = r.get("Abstract", "") or ""

        # Show record (no truncation)
        if use_rich:
            if args.pager:
                with console.pager(styles=True):
                    show_record_rich(console, idx, x, total, doc_type, title, abstract, matcher, args.width)
            else:
                show_record_rich(console, idx, x, total, doc_type, title, abstract, matcher, args.width)

            existing = r.get("include", "").strip()
            if existing in {"yes", "no"} and args.redo_completed:
                console.print(f"[dim](already decided: include={existing}, reason={r.get('reason','')})[/]")
            choice = prompt_choice_rich(console)
        else:
            show_record_ansi(idx, x, total, doc_type, title, abstract, matcher, args.width, use_colors=(not args.no_color))
            existing = r.get("include", "").strip()
            if existing in {"yes", "no"} and args.redo_completed:
                print(f"(already decided: include={existing}, reason={r.get('reason','')})")
            choice = prompt_choice_ansi()

        changed = False
        if choice == "i":
            r["include"] = "yes"
            r["reason"] = ""
            if existing not in {"yes", "no"}:
                decided_so_far += 1
            changed = True
            if use_rich: console.print("[good]Included[/]")
            else: print(f"{ANSI['green']}Included{ANSI['reset']}")
        elif choice == "e":
            reason = prompt_reason_rich(console) if use_rich else prompt_reason_ansi()
            r["include"] = "no"
            r["reason"] = reason
            if existing not in {"yes", "no"}:
                decided_so_far += 1
            changed = True
            if use_rich: console.print(f"[bad]Excluded[/] ([label]reason[/]: {reason})")
            else: print(f"{ANSI['red']}Excluded{ANSI['reset']} (reason: {reason})")
        elif choice == "s":
            pass
        elif choice == "q":
            if use_rich: console.print("[dim]Exiting. Working copy saved.[/]")
            else: print("Exiting. Working copy saved.")
            break

        if changed:
            write_csv(args.work, fieldnames, rows, args.encoding)
            ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            if use_rich:
                console.print(f"[dim]Saved to {args.work} at {ts}[/]")
            else:
                print(f"Saved to {args.work} at {ts}")

    if use_rich:
        console.rule("[hdr]Done[/]")
    else:
        print("\nDone.")

if __name__ == "__main__":
    main()


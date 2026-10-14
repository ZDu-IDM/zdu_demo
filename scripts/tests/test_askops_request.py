#!/usr/bin/env python3
"""Parser test for askops_request (no network, no PyYAML needed).

Only parse_block is exercised: it is the part that reads author-written text,
and the part that has twice reported a filled-in form as empty. yaml is
imported lazily inside frontmatter(), so this file runs with stdlib alone.

Run:  python scripts/tests/test_askops_request.py
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("askops_request",
                                              HERE.parent / "askops_request.py")
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name}\n        got:  {got!r}\n        want: {want!r}")


def block(inner):
    return "Some prose.\n\n<!-- ASKOPS:START -->\n" + inner + "\n<!-- ASKOPS:END -->\n"


def answers(inner):
    return ar.parse_block(block(inner))


def refusal(inner):
    try:
        answers(inner)
    except ValueError as e:
        return str(e)
    return "no error raised"


# --- the shape the template produces: answer on the line below the heading ---
TEMPLATE = """**Intended users -- which roles, and roughly how many?**
About 20.

**Target date or urgency**
Oct 1.
"""
check("template shape, answer below the heading",
      answers(TEMPLATE), {"users": "About 20.", "urgency": "Oct 1."})

# --- REGRESSION (PR #65): the answer written on the heading line itself.
# Recognising the heading and then collecting the lines below it dropped the
# answer and reported the field empty while it sat on screen.
INLINE_BOLD = """**Intended users -- which roles, and roughly how many?**: None
**Target date or urgency**: Oct 1, 2026
"""
check("inline answer after bold heading",
      answers(INLINE_BOLD), {"users": "None", "urgency": "Oct 1, 2026"})

INLINE_PLAIN = """Intended users: about 20 contributors
Target date: mid-October
"""
check("inline answer, no bold at all",
      answers(INLINE_PLAIN),
      {"users": "about 20 contributors", "urgency": "mid-October"})

# --- formatting must not be load-bearing: it is what a plain text box loses ---
HEADINGS = """### Intended users
About 20.

### Target date or urgency
Oct 1.
"""
check("markdown headings count as headings",
      answers(HEADINGS), {"users": "About 20.", "urgency": "Oct 1."})

check("case does not matter",
      answers("- intended users: everyone\n- TARGET DATE: today"),
      {"users": "everyone", "urgency": "today"})

# --- an inline answer plus continuation lines is one answer, not two ---
CONTINUED = """**Intended users**: about 20,
mostly IDM
**Target date**: Oct 1
"""
check("inline answer joins its continuation lines",
      answers(CONTINUED), {"users": "about 20, mostly IDM", "urgency": "Oct 1"})

# --- the optional third answer ---
check("business justification is picked up when written",
      answers("Business justification: Saves a manual step.\n"
              "Intended users: 20\nTarget date: Oct 1")["justification"],
      "Saves a manual step.")

check("business justification is optional, not required",
      sorted(answers("Intended users: 20\nTarget date: Oct 1")),
      ["urgency", "users"])

# --- REFUSALS: a blank form still looks like an answer to whoever reads it ---
check("an empty form is refused, naming the blank fields",
      refusal("**Intended users**\n\n**Target date or urgency**\n"),
      "these answers are empty: Intended users; Target date")

check("one blank field is refused on its own",
      refusal("Intended users: 20\nTarget date:"),
      "these answers are empty: Target date")

try:
    ar.parse_block("A PR description with no markers at all.")
    check("a missing block is refused", "no error raised", "ValueError")
except ValueError as e:
    check("a missing block is refused", "no ASKOPS block" in str(e), True)

# --- instructions inside the HTML comments are not answers ---
COMMENTED = """**Intended users**
<!-- e.g. "about 30 analysts on the finance team" -->
Six economists.

**Target date**
<!-- a date or "no deadline" -->
Oct 1.
"""
check("comment guidance is stripped, not collected",
      answers(COMMENTED), {"users": "Six economists.", "urgency": "Oct 1."})

# --- list-or-string frontmatter reads the same in the email ---
# allowed-tools and surfaces may be YAML lists. str() of one put
# "['Read', 'Grep']" in the governance email.
check("a YAML list reads as a comma list",
      ar.listed_text(["Read", "Grep"], "x"), "Read, Grep")
check("a comma string is unchanged",
      ar.listed_text("Read, Grep", "x"), "Read, Grep")
check("absent falls back to the given text",
      ar.listed_text(None, "not declared"), "not declared")
check("an empty list falls back too",
      ar.listed_text([], "not declared"), "not declared")

print(f"\n{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
